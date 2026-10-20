"""OpenAI adapters: Chat Completions (`openai`) and the Responses API
(`openai-responses`), which serves reasoning models with function tools where
Chat Completions refuses them."""

from __future__ import annotations

import json

from common import Usage
from providers import Adapter, OpenAICompatible, Response, get_json, register


@register("openai")
class OpenAI(OpenAICompatible):
    pass


@register("openai-responses")
class OpenAIResponses(Adapter):
    """The Responses API. Nothing is stored server side: the reasoning items
    come back encrypted and are echoed with the history."""

    url = "https://api.openai.com/v1/responses"
    key_var = "OPENAI_API_KEY"
    max_tokens = 32000

    def list_models(self) -> list[str]:
        data = get_json(self.url.replace("/responses", "/models"), {"authorization": "Bearer " + self.api_key()})
        return sorted(m.get("id", "") for m in (data.get("data") or []) if m.get("id"))

    def build(self, system, msgs, tools, tool_choice):
        items = []
        for m in msgs:
            if m["role"] == "user":
                content = m["content"] if isinstance(m["content"], str) else "\n\n".join(b["text"] for b in m["content"])
                items.append({"role": "user", "content": content})
            elif m["role"] == "assistant":
                items.extend(m["raw"])
            else:
                for r in m["results"]:
                    items.append({"type": "function_call_output", "call_id": r["id"], "output": r["content"]})
        payload = {
            "model": self.model["model"],
            "instructions": system,
            "input": items,
            "max_output_tokens": self.max_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        payload.update(self.model.get("extra_body", {}))
        if tools:
            payload["tools"] = [
                {"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]}
                for t in tools
            ]
        if tool_choice:
            payload["tool_choice"] = {"type": "function", "name": tool_choice}
        return self.url, {"authorization": "Bearer " + self.api_key()}, payload

    def parse(self, data):
        output = data.get("output") or []
        text = "".join(
            c.get("text", "")
            for item in output if item.get("type") == "message"
            for c in item.get("content") or [] if c.get("type") == "output_text"
        )
        calls = []
        bad = []
        for item in output:
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                    bad.append(item.get("arguments"))
                calls.append({"id": item.get("call_id"), "name": item.get("name"), "args": args})
        u = data.get("usage") or {}
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens") or 0
        usage = Usage(
            uncached=max(0, (u.get("input_tokens") or 0) - cached),
            cache_read=cached,
            output=u.get("output_tokens") or 0,
        )
        resp = Response(text, calls, output, usage, data.get("status", ""), data.get("model", ""))
        reasoning = (u.get("output_tokens_details") or {}).get("reasoning_tokens")
        if reasoning:
            resp.extra["reasoning_tokens"] = reasoning
        if bad:
            resp.extra["unparsed_arguments"] = bad
        return resp
