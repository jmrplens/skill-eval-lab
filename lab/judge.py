"""Grade each scenario answer against the suite's answer key, blind to the
model and the condition that produced it. One judge model, forced to answer
through a tool so the grade is structured; the request, the key and the skill
files the key cites form a cached prefix shared by every answer to a scenario."""

from __future__ import annotations

import concurrent.futures as cf
import os
import re

from common import APIError, BudgetExceeded, read_json, write_json
from providers import adapter_for

INPUTS = "/inputs"
KEY_DIR = "/key"
SKILLS_ROOT = "/skills"

GRADE_TOOL = {
    "name": "record_grade",
    "description": "Record the grade of the answer.",
    "parameters": {
        "type": "object",
        "properties": {
            "must": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "score": {"type": "number", "enum": [0, 0.5, 1]},
                        "evidence": {"type": "string"},
                    },
                    "required": ["n", "score", "evidence"],
                },
            },
            "must_not": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "violated": {"type": "boolean"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["n", "violated", "evidence"],
                },
            },
            "inventions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"claim": {"type": "string"}, "why": {"type": "string"}},
                    "required": ["claim", "why"],
                },
            },
            "unverifiable": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["must", "must_not", "inventions", "unverifiable", "summary"],
    },
}

JUDGE_SYSTEM = """You grade one answer that an AI assistant gave to a user's request. You receive the request, an answer key, and the files of the skill the key was written from. The assistant is not named and may not have had the skill; judge only what the answer says.

- Score every Must point of the key: 1 if the answer states it correctly and clearly enough for the user to act on it, 0.5 if it is only partly there (incomplete, vague or buried), 0 if it is missing or wrong. Number Must points as the key numbers them.
- Check every Must not point, numbered in the order the key lists them, from 1: violated is true only if the answer actually does what the point forbids.
- List as inventions the concrete claims that are wrong: a command, URL, label, quick action, bot behaviour, count, deadline or rule that contradicts the key or the skill files, or that you know to be false. Do not list a claim only because the files do not mention it; put claims you cannot check under unverifiable. Do not count one error both as a Must not violation and as an invention.
- A different but equally correct way to reach the outcome a Must point asks for counts as a hit.
- Be strict and consistent: the same answer must always get the same grade. Answer only by calling record_grade."""


def key_sections(path: str) -> dict:
    text = open(path, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"^## (S\d+)[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        body = m.group(2).strip()
        must_part = body.split("Must not:", 1)[0]
        not_part = body.split("Must not:", 1)[1] if "Must not:" in body else ""
        out[m.group(1)] = {
            "text": body,
            "must": len(re.findall(r"^\d+\.", must_part, re.M)),
            "must_not": len(re.findall(r"^- ", not_part, re.M)),
        }
    return out


def prefix(skill: str, request: str, key: str, sources: list[str]) -> str:
    parts = [f"<request>\n{request}\n</request>", f"<answer_key>\n{key}\n</answer_key>", "<skill_files>"]
    for rel in sources:
        path = os.path.join(SKILLS_ROOT, skill, rel)
        body = open(path, encoding="utf-8").read() if os.path.isfile(path) else "(missing)"
        parts.append(f'<file path="{rel}">\n{body}\n</file>')
    parts.append("</skill_files>")
    return "\n\n".join(parts)


def score_of(grade: dict, n_must: int) -> dict:
    must = {g["n"]: g["score"] for g in grade.get("must", []) if 1 <= g.get("n", 0) <= n_must}
    hits = sum(must.values())
    violated = sum(1 for g in grade.get("must_not", []) if g.get("violated"))
    inventions = len(grade.get("inventions", []))
    total = hits - violated - inventions
    return {
        "must_hits": hits,
        "must_points": n_must,
        "must_graded": len(must),
        "violations": violated,
        "inventions": inventions,
        "score": total,
        # T0: a truncated judge grade, fewer Must points scored than the key
        # has (e.g. 1 of 8 on a long, fenced S4 answer), must not be divided by
        # the full denominator: the ungraded points read as zeros and sink a
        # strong answer (haiku S4: 1/8 = 0.125 while the judge's prose calls it
        # strong). Discard it (None) so the report drops the cell and it is
        # re-judged, instead of recording a false low score. An empty answer
        # grades nothing at all (len(must) == 0) and keeps its honest 0.
        "normalized": None if (n_must and 0 < len(must) < n_must) else (round(total / n_must, 4) if n_must else None),
    }


def run(r) -> None:
    from run import parse_scenarios  # the CLI module; imported late to avoid a cycle

    suite = read_json(os.path.join(INPUTS, "suite.json"))
    skill = suite["skill"]
    sources = suite.get("judge_sources", {})
    scenarios = parse_scenarios(os.path.join(INPUTS, "scenarios.md"))
    keys = key_sections(os.path.join(KEY_DIR, suite.get("key_file", "scenario-key.md")))
    judge_model = r.models[r.args.judge]
    obs_root = os.path.join(r.args.out, "obs", "scenarios")

    pending: dict[str, list] = {}
    for root, _dirs, files in os.walk(obs_root):
        for f in sorted(files):
            if not f.endswith(".json"):
                continue
            obs_path = os.path.join(root, f)
            grade_path = obs_path.replace(os.sep + "obs" + os.sep + "scenarios" + os.sep, os.sep + "grades" + os.sep)
            if os.path.exists(grade_path):
                continue
            obs = read_json(obs_path)
            sid = obs.get("scenario") or f.split("-r")[0]
            pending.setdefault(sid, []).append((obs_path, grade_path, obs))
    r.log(f"judge {judge_model['id']}: {sum(len(v) for v in pending.values())} answers to grade")

    def grade_one(sid: str, item) -> None:
        obs_path, grade_path, obs = item
        k = keys[sid]
        base = {"scenario": sid, "model": obs.get("model"), "condition": obs.get("condition"), "judge": judge_model["id"]}
        if obs.get("error") or not (obs.get("final") or "").strip():
            write_json(grade_path, {**base, "skipped": obs.get("error") or "empty answer",
                                    **score_of({}, k["must"])})
            return
        adapter = adapter_for(judge_model, r.budget)
        adapter.max_tokens = 8000
        msgs = [{"role": "user", "content": [
            {"text": prefix(skill, scenarios[sid], k["text"], sources.get(sid, ["SKILL.md"])), "cache": True},
            {"text": f"<answer>\n{obs['final']}\n</answer>\n\nGrade this answer with record_grade."},
        ]}]
        # Opus 5.5 refuses a forced tool_choice (HTTP 400, 2026-10-19), so the
        # judge is asked in the prompt and nudged once if it answers in text.
        try:
            resp = adapter.call(JUDGE_SYSTEM, msgs, [GRADE_TOOL])
            if not resp.tool_calls:
                msgs += [
                    {"role": "assistant", "text": resp.text, "tool_calls": [], "raw": resp.raw},
                    {"role": "user", "content": "Record the grade now by calling record_grade."},
                ]
                resp = adapter.call(JUDGE_SYSTEM, msgs, [GRADE_TOOL])
        except BudgetExceeded as e:
            r.log(f"STOP budget: {e}")
            return
        except APIError as e:
            r.log(f"judge error {obs_path}: {e}")
            return
        if not resp.tool_calls:
            r.log(f"judge returned no grade for {obs_path}")
            return
        grade = resp.tool_calls[0]["args"]
        write_json(grade_path, {**base, "grade": grade, **score_of(grade, k["must"]), "usd": resp.usd})
        r.log(f"graded {os.path.relpath(grade_path, r.args.out)}: {score_of(grade, k['must'])['score']}")

    for sid, items in sorted(pending.items()):
        if sid not in keys:
            r.log(f"no key section for {sid}; skipped")
            continue
        # The first call writes the cached prefix; the rest read it.
        grade_one(sid, items[0])
        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda it: grade_one(sid, it), items[1:]))
        if r.budget.stopped:
            return
