"""Anthropic Messages API adapter."""

from __future__ import annotations

from common import Usage
from providers import Adapter, Response, get_json, register


@register("anthropic")
class Anthropic(Adapter):
    url = "https://api.anthropic.com/v1/messages"
    slots = 4
    key_var = "ANTHROPIC_API_KEY"

    def list_models(self) -> list[str]:
        data = get_json(
            self.url.replace("/messages", "/models"),
            {"x-api-key": self.api_key(), "anthropic-version": "2023-06-01"},
        )
        return sorted(m.get("id", "") for m in (data.get("data") or []) if m.get("id"))

    def build(self, system, msgs, tools, tool_choice):
        out = []
        explicit_cache = False
        for m in msgs:
            if m["role"] == "user" and isinstance(m["content"], list):
                # Blocks [{"text", "cache"}]: the caller places the breakpoints.
                blocks = []
                for b in m["content"]:
                    block = {"type": "text", "text": b["text"]}
                    if b.get("cache"):
                        block["cache_control"] = {"type": "ephemeral"}
                        explicit_cache = True
                    blocks.append(block)
                out.append({"role": "user", "content": blocks})
            elif m["role"] == "user":
                out.append({"role": "user", "content": [{"type": "text", "text": m["content"]}]})
            elif m["role"] == "assistant":
                out.append({"role": "assistant", "content": list(m["raw"])})
            else:
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]}
                        for r in m["results"]
                    ],
                })
        if out and not explicit_cache:
            last = dict(out[-1]["content"][-1])
            last["cache_control"] = {"type": "ephemeral"}
            out[-1]["content"] = out[-1]["content"][:-1] + [last]
        payload = {
            "model": self.model["model"],
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": out,
        }
        if tools:
            payload["tools"] = [
                {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools
            ]
        if tool_choice:
            payload["tool_choice"] = {"type": "tool", "name": tool_choice}
        headers = {"x-api-key": self.api_key(), "anthropic-version": "2023-06-01"}
        return self.url, headers, payload

    def parse(self, data):
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            {"id": b["id"], "name": b["name"], "args": b.get("input") or {}}
            for b in blocks if b.get("type") == "tool_use"
        ]
        u = data.get("usage") or {}
        usage = Usage(
            uncached=u.get("input_tokens", 0),
            cache_write=u.get("cache_creation_input_tokens", 0) or 0,
            cache_read=u.get("cache_read_input_tokens", 0) or 0,
            output=u.get("output_tokens", 0),
        )
        return Response(text, calls, blocks, usage, data.get("stop_reason", ""), data.get("model", ""))
