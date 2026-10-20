"""Provider adapters, one module per provider.

Adding a provider is adding a file to this package: define a class that
subclasses `Adapter` (or `OpenAICompatible` for an OpenAI-style endpoint),
implement `build` and `parse`, and register it with `@register("<id>")`. Every
module in this package is imported on load, so the decorator is all the wiring
there is. The rest of the lab calls `adapter_for(model, budget)` and the shared
`Adapter.call`, and never names a provider class.

The agent keeps a provider-neutral history:
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}], "raw": <provider form>}
  {"role": "tool", "results": [{"id", "name", "content"}]}
and each adapter's `build` turns it into its API's own shape while `parse` turns
the API's answer back into a `Response`. An assistant turn is echoed back
exactly as the provider returned it (Gemini's thought signatures and Anthropic's
blocks depend on that).
"""

from __future__ import annotations

import importlib
import json
import os  # noqa: F401  (re-exported for provider modules)
import pkgutil
from dataclasses import dataclass, field

from common import Budget, Usage, cost, get_json, post_json, price_for  # noqa: F401

ADAPTERS: dict[str, type] = {}


def register(name: str):
    """Class decorator: make the adapter the one `adapter_for` builds for a
    model whose "provider" is `name`."""

    def deco(cls):
        if name in ADAPTERS:
            raise ValueError(f"duplicate provider id: {name}")
        cls.provider_id = name
        ADAPTERS[name] = cls
        return cls

    return deco


@dataclass
class Response:
    text: str
    tool_calls: list
    raw: object
    usage: Usage
    stop: str
    model_version: str = ""
    usd: float = 0.0
    extra: dict = field(default_factory=dict)


def _estimate_tokens(payload: dict) -> int:
    # Conservative: three characters a token.
    return len(json.dumps(payload)) // 3 + 1


class Adapter:
    max_tokens = 16000
    # How many calls to this provider may be in flight at once; the runner
    # reads it off the registered class, so a provider sets its own ceiling.
    slots = 3
    # Default environment variable the provider's API key is read from; a model
    # overrides it with `api_key_env`, or supplies the key inline with `api_key`.
    key_var = ""

    def __init__(self, model: dict, budget: Budget):
        self.model = model
        self.budget = budget

    def api_key(self) -> str:
        """The API key for this model: an inline `api_key`, else the value of
        the environment variable `api_key_env` (or the provider's default
        `key_var`). Empty when none is set; the provider's own error surfaces."""
        name = self.model.get("api_key_env") or self.key_var
        return self.model.get("api_key") or (os.environ.get(name, "") if name else "")

    def build(self, system: str, msgs: list, tools: list, tool_choice: str | None):
        raise NotImplementedError

    def parse(self, data) -> Response:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        """The provider's available model names, for `discover`. A provider
        without a list endpoint raises NotImplementedError and `discover`
        reports it rather than failing."""
        raise NotImplementedError(f"{self.provider_id}: no model-list endpoint")

    def call(self, system: str, msgs: list, tools: list, tool_choice: str | None = None) -> Response:
        url, headers, payload = self.build(system, msgs, tools, tool_choice)
        prompt = _estimate_tokens(payload)
        p = price_for(self.model, prompt)
        worst = (prompt * p["in"] + self.max_tokens * p["out"]) / 1e6
        self.budget.reserve(worst)
        usd = 0.0
        try:
            data = post_json(url, headers, payload)
            resp = self.parse(data)
            usd = cost(self.model, resp.usage)
            resp.usd = usd
            return resp
        finally:
            self.budget.settle(worst, usd, {"model": self.model["id"]})


class OpenAICompatible(Adapter):
    """Base for any endpoint that speaks the OpenAI Chat Completions shape.
    A new OpenAI-compatible provider is a subclass that sets `url`, `key_var`,
    and (if they differ) `max_tokens_field` and `max_tokens`."""

    url = "https://api.openai.com/v1/chat/completions"
    key_var = "OPENAI_API_KEY"
    max_tokens_field = "max_completion_tokens"
    max_tokens = 32000

    def list_models(self) -> list[str]:
        url = self.url.replace("/chat/completions", "/models")
        data = get_json(url, {"authorization": "Bearer " + self.api_key()})
        return sorted(m.get("id", "") for m in (data.get("data") or []) if m.get("id"))

    def build(self, system, msgs, tools, tool_choice):
        out = [{"role": "system", "content": system}]
        for m in msgs:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                out.append(m["raw"])
            else:
                for r in m["results"]:
                    out.append({"role": "tool", "tool_call_id": r["id"], "content": r["content"]})
        payload = {"model": self.model["model"], "messages": out, self.max_tokens_field: self.max_tokens}
        payload.update(self.model.get("extra_body", {}))
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                for t in tools
            ]
        if tool_choice:
            payload["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        headers = {"authorization": "Bearer " + self.api_key()}
        return self.url, headers, payload

    def parse(self, data):
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = []
        bad = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
                bad.append(fn.get("arguments"))
            calls.append({"id": c.get("id"), "name": fn.get("name"), "args": args})
        raw = {"role": "assistant", "content": msg.get("content")}
        if msg.get("tool_calls"):
            raw["tool_calls"] = msg["tool_calls"]
        u = data.get("usage") or {}
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        usage = Usage(
            uncached=max(0, (u.get("prompt_tokens") or 0) - cached),
            cache_read=cached,
            output=u.get("completion_tokens") or 0,
        )
        resp = Response(msg.get("content") or "", calls, raw, usage, choice.get("finish_reason", ""), data.get("model", ""))
        if bad:
            resp.extra["unparsed_arguments"] = bad
        if msg.get("reasoning_content"):
            resp.extra["reasoning_chars"] = len(msg["reasoning_content"])
        return resp


def adapter_for(model: dict, budget: Budget) -> Adapter:
    return ADAPTERS[model["provider"]](model, budget)


# Import every provider module in this package so each one registers itself.
for _m in pkgutil.iter_modules(__path__):
    if not _m.name.startswith("_"):
        importlib.import_module(f"{__name__}.{_m.name}")
