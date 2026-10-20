"""Turn observations and grades into report.md and summary.json."""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict

from common import read_json, write_json

INPUTS = "/inputs"


def _walk(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            if f.endswith(".json"):
                yield os.path.join(dirpath, f)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def triggers(out: str, suite: dict, skill: str) -> dict:
    t = suite.get("triggers", {})
    should, should_not, soft = set(t.get("should", [])), set(t.get("should_not", [])), set(t.get("soft_not", []))
    per = defaultdict(lambda: {"hit": 0, "should": 0, "false": 0, "should_not": 0, "soft_loaded": 0, "soft": 0,
                               "errors": 0, "misses": [], "false_ids": [], "other_loads": defaultdict(int)})
    for p in _walk(os.path.join(out, "obs", "trigger")):
        o = read_json(p)
        m, tid = o["model"], o.get("prompt_id")
        row = per[m]
        if "error" in o:
            row["errors"] += 1
            continue
        loaded = o.get("skills_read", [])
        for s in loaded:
            if s != skill:
                row["other_loads"][s] += 1
        ours = skill in loaded
        if tid in should:
            row["should"] += 1
            row["hit"] += ours
            if not ours:
                row["misses"].append(tid)
        elif tid in should_not:
            row["should_not"] += 1
            row["false"] += ours
            if ours:
                row["false_ids"].append(tid)
        elif tid in soft:
            row["soft"] += 1
            row["soft_loaded"] += ours
    return {m: {**v, "other_loads": dict(v["other_loads"])} for m, v in per.items()}


def scenarios(out: str, suite: dict, skill: str) -> dict:
    sources = suite.get("judge_sources", {})
    rows = []
    for p in _walk(os.path.join(out, "obs", "scenarios")):
        o = read_json(p)
        gp = p.replace(os.sep + "obs" + os.sep + "scenarios" + os.sep, os.sep + "grades" + os.sep)
        g = read_json(gp) if os.path.exists(gp) else None
        reads = [e for e in o.get("reads", []) if e.get("ok") and e["tool"] == "read_file"]
        paths = {e["resolved"] for e in reads}
        wanted = {f"/skills/{skill}/{s}" for s in sources.get(o.get("scenario"), [])}
        rows.append({
            "model": o["model"], "condition": o.get("condition"), "scenario": o.get("scenario"),
            "error": o.get("error"), "calls": o.get("calls"), "usd": o.get("usd"),
            "files_read": len(paths), "read_our_skill": skill in o.get("skills_read", []),
            "reached_key_sources": round(len(paths & wanted) / len(wanted), 2) if wanted else None,
            "other_skills": [s for s in o.get("skills_read", []) if s != skill],
            "graded": bool(g and "grade" in g), "normalized": g.get("normalized") if g else None,
            "violations": g.get("violations") if g else None, "inventions": g.get("inventions") if g else None,
        })
    return rows


def run(out: str) -> None:
    suite = read_json(os.path.join(INPUTS, "suite.json"))
    skill = suite["skill"]
    trig = triggers(out, suite, skill)
    rows = scenarios(out, suite, skill)
    spend = defaultdict(float)
    ledger = os.path.join(out, "ledger.jsonl")
    if os.path.exists(ledger):
        for line in open(ledger, encoding="utf-8"):
            if line.strip():
                e = json.loads(line)
                spend[e.get("model", "?")] += e["usd"]

    models = sorted({r["model"] for r in rows} | set(trig))
    sids = sorted({r["scenario"] for r in rows if r["scenario"]})
    lines = [f"# Skill lab report: {skill}", ""]

    lines += ["## Triggering", "",
              "| Model | Recall | False loads | Soft edge loaded | Misses | False loads on | Errors |",
              "|---|---|---|---|---|---|---|"]
    for m in models:
        t = trig.get(m)
        if not t:
            continue
        lines.append(f"| {m} | {t['hit']}/{t['should']} | {t['false']}/{t['should_not']} | {t['soft_loaded']}/{t['soft']} | "
                     f"{', '.join(t['misses']) or '-'} | {', '.join(t['false_ids']) or '-'} | {t['errors']} |")

    lines += ["", "## Scenarios: normalized score (Must hits minus violations and inventions, over Must points)", ""]
    header = "| Model | Condition | " + " | ".join(sids) + " | Mean | Violations | Inventions | Files read | Key sources reached |"
    lines += [header, "|" + "---|" * (header.count("|") - 1)]
    summary = {"skill": skill, "triggers": trig, "scenarios": {}, "spend": dict(spend)}
    for m in models:
        for cond in ("with", "without"):
            sel = [r for r in rows if r["model"] == m and r["condition"] == cond]
            if not sel:
                continue
            by = {s: _mean([r["normalized"] for r in sel if r["scenario"] == s]) for s in sids}
            mean = _mean([r["normalized"] for r in sel])
            summary["scenarios"][f"{m}/{cond}"] = {"by_scenario": by, "mean": mean}
            lines.append(
                f"| {m} | {cond} | " + " | ".join("-" if by[s] is None else f"{by[s]:.2f}" for s in sids)
                + f" | {'-' if mean is None else f'{mean:.2f}'} | {sum(r['violations'] or 0 for r in sel)} | "
                + f"{sum(r['inventions'] or 0 for r in sel)} | {_mean([r['files_read'] for r in sel])} | "
                + f"{_mean([r['reached_key_sources'] for r in sel if cond == 'with'])} |")

    lines += ["", "## Lift from the skill (mean with minus mean without)", "", "| Model | With | Without | Lift |", "|---|---|---|---|"]
    for m in models:
        w = summary["scenarios"].get(f"{m}/with", {}).get("mean")
        wo = summary["scenarios"].get(f"{m}/without", {}).get("mean")
        lift = None if w is None or wo is None else round(w - wo, 3)
        summary["scenarios"].setdefault(f"{m}/lift", lift)
        lines.append(f"| {m} | {w} | {wo} | {lift} |")

    errors = [r for r in rows if r["error"]]
    ungraded = [r for r in rows if not r["error"] and not r["graded"]]
    loaded_others = [r for r in rows if r["other_skills"]]
    lines += ["", "## Other observations", "",
              f"- Runs with an error: {len(errors)}" + "".join(f"\n  - {r['model']}/{r['condition']}/{r['scenario']}: {str(r['error'])[:160]}" for r in errors),
              f"- Runs not graded yet: {len(ungraded)}",
              f"- Runs that also read another skill: {len(loaded_others)}"
              + "".join(f"\n  - {r['model']}/{r['condition']}/{r['scenario']}: {', '.join(r['other_skills'])}" for r in loaded_others),
              f"- Runs with the skill installed that never read it: "
              + str(sum(1 for r in rows if r['condition'] == 'with' and not r['read_our_skill']))]
    lines += ["", "## Spend (USD)", "", "| Model | USD |", "|---|---|"]
    lines += [f"| {m} | {u:.4f} |" for m, u in sorted(spend.items())]
    lines.append(f"| total | {sum(spend.values()):.4f} |")

    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    write_json(os.path.join(out, "summary.json"), {**summary, "rows": rows})
    print("\n".join(lines))
