"""Shared pieces of the skill lab: the model table, prices, the spending cap
and an HTTP client with retries. Standard library only."""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

LAB = os.path.dirname(os.path.abspath(__file__))

KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "QWEN_API_KEY", "OPENROUTER_API_KEY")


def _flatten_catalog(data) -> dict:
    """A catalog is a map of provider id to its models; the provider is
    injected from the key. A provider's value is either the array of its models
    (the compact form) or a block `{api_key_env?, api_key?, models: [...]}` that
    carries the provider's key down onto each of its models, so the YAML says
    which providers to use, which models in each, and where each key comes from.
    A flat list of entries, each carrying its own "provider", is also accepted.
    Returns id -> model."""
    out: dict = {}
    if isinstance(data, dict):
        for provider, block in data.items():
            if isinstance(block, dict):
                models = block.get("models") or []
                carried = {k: block[k] for k in ("api_key", "api_key_env") if k in block}
            else:
                models = block or []
                carried = {}
            for m in models:
                # A model that sets its own key source replaces the block's.
                mc = {} if ("api_key" in m or "api_key_env" in m) else carried
                out[m["id"]] = {**mc, **m, "provider": provider}
        return out
    for m in (data or []):
        out[m["id"]] = dict(m)
    return out


def _read_catalog(path: str):
    """Read a user catalog file. JSON always (standard library); YAML when the
    path ends in .yaml/.yml and PyYAML is importable (the lab wrappers add it to
    the container for a YAML file). Prices live inside each model's own entry."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as e:
            raise SystemExit(
                f"{path} is YAML but PyYAML is not installed. Pass a JSON catalog, "
                "or install pyyaml (the lab wrappers do this in the container for a "
                ".yaml/.yml file)."
            ) from e
        return yaml.safe_load(text)
    return json.loads(text)


def load_models(extra_path: str | None = None) -> dict:
    """The model table keyed by id. The built-in lab/models.json is loaded
    first; a user catalog passed as extra_path (--models-file / MODELS_FILE) is
    then merged over it, so a new id is added and an existing id's fields
    (price, model, note, extra_body, ...) are overridden. That is how a user
    extends or re-prices the catalog without editing the shipped file."""
    with open(os.path.join(LAB, "models.json"), encoding="utf-8") as f:
        models = _flatten_catalog(json.load(f))
    if extra_path:
        for mid, entry in _flatten_catalog(_read_catalog(extra_path)).items():
            models[mid] = {**models.get(mid, {}), **entry}
    return models


def openrouter_prices(timeout: float = 60) -> dict:
    """OpenRouter's public models API is the one place that lists per-token
    prices for models across many providers as stable JSON (no key needed), so
    `discover` uses it to autofill prices. Returns {model_name: {"in", "out"}}
    in USD per million tokens, keyed by the part after the last "/" of each
    OpenRouter id (e.g. "anthropic/claude-haiku" -> "claude-haiku") and by the
    full id, both lowercased. A price `discover` cannot match here is left for
    the user to fill, and the budget refuses a model with no price, so a missing
    or wrong match never silently drives spending."""
    data = get_json("https://openrouter.ai/api/v1/models", timeout=timeout)
    out: dict = {}
    for m in data.get("data") or []:
        mid = (m.get("id") or "").strip()
        pricing = m.get("pricing") or {}
        try:
            # OpenRouter prices are USD per token; the lab works in per-million.
            price = {"in": float(pricing["prompt"]) * 1e6, "out": float(pricing["completion"]) * 1e6}
        except (KeyError, TypeError, ValueError):
            continue
        if price["in"] <= 0 or price["out"] <= 0:
            continue  # free/variable/negative side: no figure the budget can trust
        for key in (mid, mid.rsplit("/", 1)[-1]):
            if key:
                out.setdefault(key.lower(), price)
    return out


@dataclass
class Usage:
    uncached: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0

    def add(self, other: "Usage") -> None:
        self.uncached += other.uncached
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read
        self.output += other.output

    def prompt(self) -> int:
        return self.uncached + self.cache_write + self.cache_read

    def as_dict(self) -> dict:
        return asdict(self)


def price_for(model: dict, prompt_tokens: int) -> dict:
    long = model.get("long")
    if long and prompt_tokens > long["over"]:
        return long
    return model["price"]


def cost(model: dict, u: Usage) -> float:
    p = price_for(model, u.prompt())
    return (
        u.uncached * p["in"]
        + u.cache_write * p.get("cw", p["in"])
        + u.cache_read * p.get("cr", p["in"])
        + u.output * p["out"]
    ) / 1e6


class BudgetExceeded(Exception):
    pass


class Budget:
    """A hard spending cap. Each call reserves its worst case before it is
    sent and settles the real cost after, so concurrent calls can never take
    the total past the cap. The ledger survives restarts."""

    def __init__(self, cap: float, ledger: str):
        self.cap = cap
        self.ledger = ledger
        self.lock = threading.Lock()
        self.spent = 0.0
        self.reserved = 0.0
        self.stopped = False
        if os.path.exists(ledger):
            with open(ledger, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.spent += json.loads(line)["usd"]

    def reserve(self, worst: float) -> None:
        with self.lock:
            if self.stopped or self.spent + self.reserved + worst > self.cap:
                self.stopped = True
                raise BudgetExceeded(
                    f"cap {self.cap:.2f} USD: spent {self.spent:.4f}, reserved {self.reserved:.4f}, next call up to {worst:.4f}"
                )
            self.reserved += worst

    def settle(self, worst: float, usd: float, meta: dict) -> None:
        with self.lock:
            self.reserved -= worst
            self.spent += usd
            with open(self.ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps({"usd": usd, **meta}) + "\n")


def _secrets() -> list[str]:
    return [v for v in (os.environ.get(k, "") for k in KEY_VARS) if len(v) >= 8]


def redact(text: str) -> str:
    for s in _secrets():
        text = text.replace(s, "[redacted]")
    return text


class APIError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:2000]}")
        self.status = status
        self.body = body


RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _retry_after(headers) -> float | None:
    value = headers.get("retry-after") if headers else None
    if not value:
        return None
    try:
        return min(120.0, float(value))
    except ValueError:
        return None


def post_json(url: str, headers: dict, payload: dict, timeout: float = 900, tries: int = 7) -> dict:
    data = json.dumps(payload).encode("utf-8")
    last: Exception | None = None
    for attempt in range(tries):
        req = urllib.request.Request(
            url, data=data, method="POST", headers={"content-type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = redact(e.read().decode("utf-8", "replace"))
            last = APIError(e.code, body)
            if e.code in RETRY_STATUS and attempt < tries - 1:
                time.sleep(_retry_after(e.headers) or min(90.0, 2.0 ** attempt + random.random() * 2))
                continue
            raise last from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = APIError(0, redact(str(e)))
            if attempt < tries - 1:
                time.sleep(min(90.0, 2.0 ** attempt + random.random() * 2))
                continue
            raise last from None
    raise last  # pragma: no cover


def get_json(url: str, headers: dict | None = None, timeout: float = 60) -> dict:
    """A plain GET returning parsed JSON, for the discovery endpoints (provider
    model lists, OpenRouter prices). Raises APIError on an HTTP error, like
    post_json, so a caller reports it rather than crashing."""
    req = urllib.request.Request(url, method="GET", headers={"accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise APIError(e.code, redact(e.read().decode("utf-8", "replace"))) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise APIError(0, redact(str(e))) from None


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
