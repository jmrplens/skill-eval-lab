"""A generic agent that knows skills only from a catalog in its system prompt
and reads them through two read-only tools, the way an assistant without
native skill support would use the Agent Skills format."""

from __future__ import annotations

import os
import posixpath
import re

SKILLS_ROOT = "/skills"
MAX_FILE_CHARS = 120_000

TOOLS = [
    {
        "name": "read_file",
        "description": "Read a text file from the skill library. Paths are absolute, for example /skills/<skill>/SKILL.md or /skills/<skill>/references/<file>.md.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path of the file under /skills."}},
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List the entries of a directory in the skill library (directories end with /).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute path of a directory under /skills."}},
            "required": ["path"],
        },
    },
]

SYSTEM_TEMPLATE = """You are an AI assistant answering a user's request. You have two tools, read_file and list_dir, and they can only read a library of skills under /skills. You cannot run commands, browse the web or reach gitlab.com or any other service, so you answer by telling the user what to do.

A skill is a folder of instructions for one kind of task. The installed skills are listed below, each with its name, a description of when to use it, and the path of its SKILL.md. When the user's request matches a skill's description, read that skill's SKILL.md before you answer and follow it, and read the other files it points to when you need them (paths inside a skill are relative to that skill's folder). Do not read skills that do not apply. If no skill applies, answer from your own knowledge.

<available_skills>
{skills}
</available_skills>"""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def frontmatter(path: str) -> dict:
    """Read name and description from a SKILL.md frontmatter (plain, quoted,
    and folded or literal block scalars)."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    body = lines[1:end]
    out: dict = {}
    i = 0
    while i < len(body):
        m = re.match(r"^([A-Za-z_][\w-]*):\s?(.*)$", body[i])
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2).strip()
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            block = []
            i += 1
            while i < len(body) and (body[i].startswith(" ") or not body[i].strip()):
                block.append(body[i].strip())
                i += 1
            joiner = " " if value.startswith(">") else "\n"
            out[key] = joiner.join(b for b in block if b).strip()
            continue
        # A plain scalar may continue on indented lines.
        i += 1
        while i < len(body) and body[i].startswith("  ") and not value.startswith(('"', "'")):
            value += " " + body[i].strip()
            i += 1
        out[key] = _unquote(value)
    return out


class SkillFS:
    """Read-only view of /skills limited to the skills a condition installs."""

    def __init__(self, allowed: list[str], root: str = SKILLS_ROOT):
        self.root = root
        self.allowed = sorted(allowed)
        self.reads: list[dict] = []

    def catalog(self) -> str:
        entries = []
        for name in self.allowed:
            fm = frontmatter(os.path.join(self.root, name, "SKILL.md"))
            entries.append(
                "<skill>\n<name>{}</name>\n<description>{}</description>\n<location>{}/{}/SKILL.md</location>\n</skill>".format(
                    fm.get("name", name), fm.get("description", ""), SKILLS_ROOT, name
                )
            )
        return SYSTEM_TEMPLATE.format(skills="\n".join(entries))

    def _resolve(self, path: str):
        if not isinstance(path, str) or not path.strip():
            return None, "Error: a path is required."
        p = path.strip()
        if not p.startswith("/"):
            p = posixpath.join(SKILLS_ROOT, p[len("skills/"):] if p.startswith("skills/") else p)
        p = posixpath.normpath(p)
        if p == SKILLS_ROOT:
            return p, None
        if not p.startswith(SKILLS_ROOT + "/"):
            return None, f"Error: {path} is outside the skill library; paths start with {SKILLS_ROOT}/<skill>/."
        skill = p[len(SKILLS_ROOT) + 1:].split("/", 1)[0]
        if skill not in self.allowed:
            return None, f"Error: no such skill: {skill}."
        real = os.path.realpath(os.path.join(self.root, p[len(SKILLS_ROOT) + 1:]))
        base = os.path.realpath(os.path.join(self.root, skill))
        if real != base and not real.startswith(base + os.sep):
            return None, f"Error: {path} is outside the skill library."
        return p, None

    def _host(self, p: str) -> str:
        return os.path.join(self.root, p[len(SKILLS_ROOT) + 1:]) if p != SKILLS_ROOT else self.root

    def handle(self, name: str, args: dict) -> str:
        path = (args or {}).get("path", "")
        p, err = self._resolve(path)
        entry = {"tool": name, "path": path, "resolved": p, "ok": False}
        self.reads.append(entry)
        if err:
            entry["error"] = err
            return err
        host = self._host(p)
        if name == "list_dir":
            if not os.path.isdir(host):
                return f"Error: {path} is not a directory."
            names = self.allowed if p == SKILLS_ROOT else sorted(os.listdir(host))
            entry["ok"] = True
            return "\n".join(n + ("/" if os.path.isdir(os.path.join(host, n)) else "") for n in names)
        if name == "read_file":
            if not os.path.isfile(host):
                return f"Error: no such file: {path}."
            with open(host, encoding="utf-8", errors="replace") as f:
                text = f.read()
            entry["ok"] = True
            entry["chars"] = len(text)
            if len(text) > MAX_FILE_CHARS:
                text = text[:MAX_FILE_CHARS] + "\n[truncated]"
            return text
        return f"Error: unknown tool {name}."


def skills_read(reads: list[dict]) -> list[str]:
    """Skills whose SKILL.md was read successfully, in order."""
    out = []
    for r in reads:
        if r["tool"] == "read_file" and r["ok"] and r["resolved"].endswith("/SKILL.md"):
            skill = r["resolved"][len(SKILLS_ROOT) + 1:].split("/", 1)[0]
            if r["resolved"] == f"{SKILLS_ROOT}/{skill}/SKILL.md" and skill not in out:
                out.append(skill)
    return out


def run_agent(adapter, fs: SkillFS, user_text: str, max_rounds: int = 25, stop=None) -> dict:
    """Run the tool loop until the model answers without calling a tool, the
    round limit is reached, or stop(fs, round) says to stop. Returns the
    transcript and totals."""
    from common import Usage

    system = fs.catalog()
    msgs: list = [{"role": "user", "content": user_text}]
    usage = Usage()
    usd = 0.0
    calls = 0
    final = ""
    ended = "answered"
    versions = set()
    for rnd in range(max_rounds + 1):
        resp = adapter.call(system, msgs, TOOLS)
        calls += 1
        usage.add(resp.usage)
        usd += resp.usd
        if resp.model_version:
            versions.add(resp.model_version)
        msgs.append({"role": "assistant", "text": resp.text, "tool_calls": resp.tool_calls, "raw": resp.raw, "stop": resp.stop, "extra": resp.extra})
        if not resp.tool_calls:
            final = resp.text
            break
        results = [
            {"id": c["id"], "provider_id": c.get("provider_id"), "name": c["name"], "content": fs.handle(c["name"], c["args"])}
            for c in resp.tool_calls
        ]
        if stop and stop(fs, rnd):
            ended = "stopped"
            final = resp.text
            break
        if rnd == max_rounds - 1:
            # The next turn is the last one.
            for r in results:
                r["content"] += "\n\n[The tool budget for this task is used up. Answer the user now with what you have.]"
        if rnd == max_rounds:
            ended = "round_limit"
            final = resp.text
            break
        msgs.append({"role": "tool", "results": results})
    return {
        "system": system,
        "messages": [
            {k: v for k, v in m.items() if k != "raw"} if m["role"] == "assistant" else m for m in msgs
        ],
        "final": final,
        "ended": ended,
        "calls": calls,
        "usage": usage.as_dict(),
        "usd": round(usd, 6),
        "model_versions": sorted(versions),
        "reads": fs.reads,
        "skills_read": skills_read(fs.reads),
    }
