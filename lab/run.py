"""Skill lab: does an Agent Skill load when it should, and does it change
what a model answers, across providers? The model knows the skills only from
a catalog in its system prompt and reads them through read-only tools, as an
assistant without native skill support would.

  run.py smoke     --out DIR [--models ids]
  run.py trigger   --out DIR [--models ids] [--reps N]
  run.py scenarios --out DIR [--models ids] [--conditions with,without] [--scenarios S1,..] [--reps N]
  run.py judge     --out DIR [--judge opus-5.5]
  run.py report    --out DIR
  run.py discover  --out DIR [--providers a,b]   (writes DIR/providers.yaml)

Every paid call goes through one Budget whose ledger lives in DIR, so the
cap (--cap, USD) holds across invocations. A unit whose observation file
exists is not run again, so a stopped run resumes where it stopped.
Inputs: /skills (the skill library, read-only), /inputs (scenarios.md,
trigger-prompts.md), /key (the answer key; only judge reads it).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import sys
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent import SKILLS_ROOT, TOOLS, SkillFS, run_agent  # noqa: E402
from common import APIError, Budget, BudgetExceeded, load_models, openrouter_prices, read_json, write_json  # noqa: E402
from providers import ADAPTERS, adapter_for  # noqa: E402

INPUTS = "/inputs"
# How many calls to each provider may be in flight at once, read off the
# registered adapter class, so a new provider sets its own ceiling in its file.
PROVIDER_SLOTS = {name: cls.slots for name, cls in ADAPTERS.items()}


def load_suite() -> dict:
    """The suite names the skill under test and what the report and the
    judge need to know about it; see suites/example/inputs/suite.json."""
    return read_json(os.path.join(INPUTS, "suite.json"))


SUITE = load_suite() if os.path.exists(os.path.join(INPUTS, "suite.json")) else {}
OUR = SUITE.get("skill", "")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def parse_scenarios(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"^## (S\d+) [^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        out[m.group(1)] = m.group(2).strip()
    return out


def parse_triggers(path: str) -> dict:
    out = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^- (T\d+): (.+)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def skill_digest(name: str) -> str:
    h = hashlib.sha256()
    base = os.path.join(SKILLS_ROOT, name)
    for root, dirs, files in os.walk(base):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, base).encode() + b"\0")
            h.update(open(p, "rb").read())
    return h.hexdigest()[:16]


def all_skills() -> list[str]:
    return sorted(d for d in os.listdir(SKILLS_ROOT) if os.path.isfile(os.path.join(SKILLS_ROOT, d, "SKILL.md")))


def record_invocation(out: str, args) -> None:
    path = os.path.join(out, "run.json")
    data = read_json(path) if os.path.exists(path) else {"invocations": []}
    data.setdefault("skills", {name: skill_digest(name) for name in all_skills()})
    data["invocations"].append({"at": now(), "argv": sys.argv[1:]})
    write_json(path, data)


class Runner:
    def __init__(self, args):
        self.args = args
        self.models = load_models(getattr(args, "models_file", "") or None)
        self.budget = Budget(args.cap, os.path.join(args.out, "ledger.jsonl"))
        self.slots = {p: threading.Semaphore(n) for p, n in PROVIDER_SLOTS.items()}
        self.print_lock = threading.Lock()

    @staticmethod
    def _priced(models: list[dict]) -> list[dict]:
        """Refuse up front a selected model that has no price, so a missing or
        unresolved price never silently drives the budget. The user sets it in
        the catalog, or removes the model from --models."""
        missing = sorted(m["id"] for m in models if "price" not in m)
        if missing:
            raise SystemExit("no price for " + ", ".join(missing) + "; set it in the catalog (see `discover`) or drop it from --models")
        return models

    def selected(self) -> list[dict]:
        """Resolve --models to model entries. Each comma-separated token is a
        model id (`haiku-5.5`), a provider meaning its whole array
        (`anthropic`), or a provider with a `+`-separated subset
        (`anthropic:haiku-5.5+sonnet-5.5`). Empty selects every model."""
        if not self.args.models:
            return self._priced(list(self.models.values()))
        picked: list[dict] = []
        seen: set[str] = set()
        for token in self.args.models.split(","):
            token = token.strip()
            if not token:
                continue
            if token in self.models:
                ids = [token]
            elif ":" in token:
                provider, _, rest = token.partition(":")
                wanted = {w for w in rest.split("+") if w}
                ids = [i for i, m in self.models.items()
                       if m["provider"] == provider and (not wanted or i in wanted)]
            else:
                ids = [i for i, m in self.models.items() if m["provider"] == token]
            if not ids:
                raise SystemExit(f"no model or provider matches {token!r}")
            for i in ids:
                if i not in seen:
                    seen.add(i)
                    picked.append(self.models[i])
        return self._priced(picked)

    def log(self, msg: str) -> None:
        with self.print_lock:
            print(f"[{now()}] {msg} (spent {self.budget.spent:.4f} USD)", flush=True)

    def execute(self, units: list) -> None:
        """units: (path, model, fn). Runs the ones whose file is missing."""
        todo = [u for u in units if not os.path.exists(u[0])]
        self.log(f"{len(todo)} of {len(units)} units to run")

        def one(unit):
            path, model, fn = unit
            if self.budget.stopped:
                return
            with self.slots[model["provider"]]:
                if self.budget.stopped:
                    return
                started = now()
                try:
                    result = fn()
                except BudgetExceeded as e:
                    self.log(f"STOP budget: {e}")
                    return
                except APIError as e:
                    result = {"error": f"{e}", "status": e.status}
                except Exception as e:  # noqa: BLE001
                    result = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-3000:]}
                result.update({"model": model["id"], "provider_model": model["model"], "started": started, "finished": now()})
                write_json(path, result)
                state = "ERROR " + result["error"][:200] if "error" in result else f"{result.get('ended')} calls={result.get('calls')} usd={result.get('usd')}"
                self.log(f"{os.path.relpath(path, self.args.out)}: {state}")

        with cf.ThreadPoolExecutor(max_workers=self.args.parallel) as pool:
            list(pool.map(one, todo))


def cmd_smoke(r: Runner) -> None:
    fs = SkillFS(all_skills())
    for m in r.selected():
        ad = adapter_for(m, r.budget)
        ad.max_tokens = 400
        try:
            resp = ad.call(
                "You are testing a tool interface. Always use the tool when asked.",
                [{"role": "user", "content": f"Call read_file with the path {SKILLS_ROOT}/{OUR}/SKILL.md and nothing else."}],
                TOOLS,
            )
            ok = bool(resp.tool_calls) and resp.tool_calls[0]["name"] == "read_file"
            content = fs.handle("read_file", resp.tool_calls[0]["args"]) if ok else ""
            r.log(f"SMOKE {m['id']}: {'OK' if ok and not content.startswith('Error') else 'UNEXPECTED'} "
                  f"version={resp.model_version} calls={resp.tool_calls} usage={resp.usage.as_dict()} usd={resp.usd:.6f}")
        except BudgetExceeded as e:
            r.log(f"STOP budget: {e}")
            return
        except Exception as e:  # noqa: BLE001
            r.log(f"SMOKE {m['id']}: FAIL {e}")


def cmd_trigger(r: Runner) -> None:
    prompts = parse_triggers(os.path.join(INPUTS, "trigger-prompts.md"))
    skills = all_skills()

    def stop(fs: SkillFS, rnd: int) -> bool:
        # Stop once the model moves past SKILL.md files, or after three rounds.
        read_other = any(e["tool"] == "read_file" and e.get("resolved") and not e["resolved"].endswith("/SKILL.md") for e in fs.reads)
        return read_other or rnd >= 2

    units = []
    for m in r.selected():
        for tid, text in prompts.items():
            for rep in range(1, r.args.reps + 1):
                path = os.path.join(r.args.out, "obs", "trigger", m["id"], f"{tid}-r{rep}.json")

                def fn(m=m, text=text, tid=tid):
                    res = run_agent(adapter_for(m, r.budget), SkillFS(skills), text, max_rounds=3, stop=stop)
                    res["prompt_id"] = tid
                    return res

                units.append((path, m, fn))
    r.execute(units)


def cmd_scenarios(r: Runner) -> None:
    scenarios = parse_scenarios(os.path.join(INPUTS, "scenarios.md"))
    if r.args.scenarios:
        scenarios = {k: v for k, v in scenarios.items() if k in r.args.scenarios.split(",")}
    skills = all_skills()
    installed = {"with": skills, "without": [s for s in skills if s != OUR]}
    units = []
    for m in r.selected():
        for cond in r.args.conditions.split(","):
            for sid, text in scenarios.items():
                for rep in range(1, r.args.reps + 1):
                    path = os.path.join(r.args.out, "obs", "scenarios", m["id"], cond, f"{sid}-r{rep}.json")

                    def fn(m=m, cond=cond, sid=sid, text=text):
                        res = run_agent(adapter_for(m, r.budget), SkillFS(installed[cond]), text, max_rounds=r.args.max_rounds)
                        res.update({"scenario": sid, "condition": cond})
                        return res

                    units.append((path, m, fn))
    r.execute(units)


def _yaml_scalar(s: str) -> str:
    """A YAML-safe double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(s, ensure_ascii=False)


def _render_catalog_yaml(catalog: dict) -> str:
    """Render a discovered catalog as a providers.yaml the user can edit and
    point MODELS_FILE at. A model with no price is left as a commented TODO."""
    lines = [
        "# Generated by `run.py discover`. Model names come from each provider's",
        "# own /models API; prices are OpenRouter's listed figures, matched by exact",
        "# model name. Verify prices against the provider's own pricing page. A model",
        "# with no matched price is a TODO: the budget refuses a selected model that",
        "# has none. Set the key per provider with api_key_env (a .env variable name)",
        "# or an inline api_key, and keep in `models` only what you want to run.",
        "",
    ]
    for provider in sorted(catalog):
        block = catalog[provider]
        lines.append(f"{provider}:")
        lines.append(f"  api_key_env: {block['api_key_env']}")
        lines.append("  models:")
        for m in block["models"]:
            lines.append(f"    - id: {_yaml_scalar(m['id'])}")
            lines.append(f"      model: {_yaml_scalar(m['model'])}")
            if "price" in m:
                lines.append(f"      price: {{in: {m['price']['in']}, out: {m['price']['out']}}}")
            else:
                lines.append("      # price: {in: 0.0, out: 0.0}  # TODO: no OpenRouter match; set from the provider's pricing")
        lines.append("")
    return "\n".join(lines) + "\n"


def cmd_discover(r: Runner) -> None:
    """List each provider's models through its official /models API and autofill
    prices from OpenRouter, writing <out>/providers.yaml. Needs each provider's
    key (from the environment) for its listing; OpenRouter needs none."""
    want = {p.strip() for p in r.args.providers.split(",") if p.strip()}
    for p in want:
        if p not in ADAPTERS:
            raise SystemExit(f"unknown provider {p!r}; known: {', '.join(sorted(ADAPTERS))}")
    providers = [p for p in ADAPTERS if not want or p in want]
    try:
        prices = openrouter_prices()
        r.log(f"OpenRouter: {len(prices)} priced model names")
    except Exception as e:  # noqa: BLE001
        r.log(f"OpenRouter prices unavailable ({e}); every model gets a price TODO")
        prices = {}
    catalog: dict = {}
    seen_ids: set = set()
    for p in providers:
        ad = adapter_for({"provider": p, "model": ""}, r.budget)
        try:
            names = ad.list_models()
        except NotImplementedError:
            r.log(f"{p}: no model-list endpoint; skipped")
            continue
        except Exception as e:  # noqa: BLE001
            r.log(f"{p}: could not list models ({e}); skipped")
            continue
        rows = []
        for name in names:
            # The lab id must be unique across providers; openai and
            # openai-responses share OpenAI's model list, so suffix a clash.
            rid = name if name not in seen_ids else f"{name}-{p}"
            seen_ids.add(rid)
            row = {"id": rid, "model": name}
            pr = prices.get(name.lower())
            if pr:
                row["price"] = {"in": round(pr["in"], 4), "out": round(pr["out"], 4)}
            rows.append(row)
        catalog[p] = {"api_key_env": ad.key_var or f"{p.upper().replace('-', '_')}_API_KEY", "models": rows}
        r.log(f"{p}: {len(rows)} models, {sum('price' in x for x in rows)} priced")
    path = os.path.join(r.args.out, "providers.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_render_catalog_yaml(catalog))
    r.log(f"wrote providers.yaml with {len(catalog)} providers; edit it, then pass it as MODELS_FILE")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["smoke", "trigger", "scenarios", "judge", "report", "discover"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default="")
    ap.add_argument("--models-file", default="",
                    help="user catalog (JSON, or YAML with PyYAML) merged over lab/models.json")
    ap.add_argument("--providers", default="",
                    help="discover: comma-separated provider ids to list (empty = all)")
    ap.add_argument("--conditions", default="with,without")
    ap.add_argument("--scenarios", default="")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-rounds", type=int, default=25)
    ap.add_argument("--parallel", type=int, default=10)
    ap.add_argument("--cap", type=float, default=25.0)
    ap.add_argument("--judge", default="opus-5.5")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    record_invocation(args.out, args)
    r = Runner(args)
    if args.command == "smoke":
        cmd_smoke(r)
    elif args.command == "trigger":
        cmd_trigger(r)
    elif args.command == "scenarios":
        cmd_scenarios(r)
    elif args.command == "discover":
        cmd_discover(r)
    elif args.command == "judge":
        import judge
        judge.run(r)
    else:
        import report
        report.run(args.out)
    r.log(f"done: {args.command}")


if __name__ == "__main__":
    main()
