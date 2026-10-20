"""Alibaba Cloud Model Studio (Qwen) adapter: the OpenAI Chat Completions
shape, on its own endpoint and key."""

from __future__ import annotations

from providers import OpenAICompatible, register


@register("qwen")
class Qwen(OpenAICompatible):
    url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
    key_var = "QWEN_API_KEY"
    max_tokens_field = "max_tokens"
    max_tokens = 16000
