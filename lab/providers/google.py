"""Google Gemini (generateContent) adapter."""

from __future__ import annotations

import json

from common import Usage
from providers import Adapter, Response, get_json, register


@register("google")
class Google(Adapter):
    base = "https://generativelanguage.googleapis.com/v1beta/models/"
    max_tokens = 32000
    key_var = "GOOGLE_API_KEY"

    def list_models(self) -> list[str]:
        data = get_json(self.base.rstrip("/"), {"x-goog-api-key": self.api_key()})
        return sorted(m.get("name", "").split("/")[-1] for m in (data.get("models") or []) if m.get("name"))

    def build(self, system, msgs, tools, tool_choice):
        contents = []
        for m in msgs:
            if m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                contents.append({"role": "model", "parts": m["raw"]})
            else:
                parts = []
                for r in m["results"]:
                    fr = {"name": r["name"], "response": {"content": r["content"]}}
                    if r.get("provider_id"):
                        fr["id"] = r["provider_id"]
                    parts.append({"functionResponse": fr})
                contents.append({"role": "user", "parts": parts})
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": self.max_tokens},
        }
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in tools
            ]}]
        if tool_choice:
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [tool_choice]}}
        url = self.base + self.model["model"] + ":generateContent"
        return url, {"x-goog-api-key": self.api_key()}, payload

    def parse(self, data):
        cands = data.get("candidates") or []
        if not cands:
            raise RuntimeError("no candidates: " + json.dumps(data.get("promptFeedback", {}))[:500])
        cand = cands[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
        calls = []
        for i, p in enumerate(parts):
            fc = p.get("functionCall")
            if fc:
                calls.append({"id": fc.get("id") or f"call_{i}", "provider_id": fc.get("id"), "name": fc.get("name"), "args": fc.get("args") or {}})
        u = data.get("usageMetadata") or {}
        cached = u.get("cachedContentTokenCount") or 0
        usage = Usage(
            uncached=max(0, (u.get("promptTokenCount") or 0) - cached),
            cache_read=cached,
            output=(u.get("candidatesTokenCount") or 0) + (u.get("thoughtsTokenCount") or 0),
        )
        return Response(text, calls, parts, usage, cand.get("finishReason", ""), data.get("modelVersion", ""))
