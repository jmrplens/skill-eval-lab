"""OpenRouter adapter: an OpenAI-compatible gateway to many providers' models,
on its own endpoint and key. Model ids are the provider/slug form OpenRouter
routes by."""

from __future__ import annotations

from providers import OpenAICompatible, register


@register("openrouter")
class OpenRouter(OpenAICompatible):
    url = "https://openrouter.ai/api/v1/chat/completions"
    key_var = "OPENROUTER_API_KEY"
    max_tokens_field = "max_tokens"
    max_tokens = 16000
