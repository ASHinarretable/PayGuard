"""A small client for OpenAI-compatible chat completions, on httpx.

Every model provider PayGuard uses (Groq, Mistral, Gemini, OpenRouter, Cloudflare Workers AI, NVIDIA NIM,
a local Ollama) serves the same
/chat/completions API, so this replaces a model-gateway library. PayGuard runs next to payment
credentials; the fewer third-party packages in that process, the less a compromised release can
reach (LiteLLM was backdoored on PyPI in March and again on 21 Oct 2026).

Model ids keep the "provider/model" form: groq/openai/gpt-oss-20b, mistral/ministral-14b-2512,
gemini/gemini-3.5-flash-lite, openrouter/google/gemma-4-31b-it:free,
cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct, nvidia_nim/moonshotai/kimi-k2-instruct, ollama/qwen3:4b.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

PROVIDERS: dict[str, tuple[str, str | None]] = {  # prefix -> (base url, api key env var)
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    # {VAR} in a base url is filled from the environment
    "cloudflare": ("https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1", "CLOUDFLARE_API_TOKEN"),
    "nvidia_nim": ("https://integrate.api.nvidia.com/v1", "NVIDIA_NIM_API_KEY"),
    "ollama": ("http://127.0.0.1:11434/v1", None),
}


URL_VAR = re.compile(r"\{([A-Z_]+)\}")


def _usable(value: str | None) -> bool:
    return bool(value) and not value.endswith("xxx")  # the .env.example placeholder doesn't count


class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RateLimited(LLMError):
    """429. retry_after is set when the provider says how long to wait."""


class Unavailable(LLMError):
    """5xx, timeouts and connection errors: worth retrying."""


@dataclass
class Completion:
    message: dict[str, Any]  # role, content, tool_calls (OpenAI shape)
    prompt_tokens: int = 0
    completion_tokens: int = 0


def resolve(model: str) -> tuple[str, str, str | None]:
    """'groq/openai/gpt-oss-20b' -> (base url, 'openai/gpt-oss-20b', api key)."""
    prefix, _, name = model.partition("/")
    if prefix not in PROVIDERS or not name:
        raise LLMError(f"unknown model provider in {model!r}; expected one of {', '.join(PROVIDERS)}")
    base, key_var = PROVIDERS[prefix]
    key = os.environ.get(key_var) if key_var else None
    if key_var and not key:
        raise LLMError(f"{key_var} is not set (needed for {model})")
    for var in URL_VAR.findall(base):
        if not os.environ.get(var):
            raise LLMError(f"{var} is not set (needed for {model})")
        base = base.replace("{" + var + "}", os.environ[var])
    return base, name, key


def has_key(model: str) -> bool:
    prefix = model.partition("/")[0]
    if prefix not in PROVIDERS:
        return False
    base, key_var = PROVIDERS[prefix]
    needed = ([key_var] if key_var else []) + URL_VAR.findall(base)
    return all(_usable(os.environ.get(var)) for var in needed)


def _retry_after(r: httpx.Response) -> float | None:
    value = r.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


async def chat(model: str, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
               temperature: float = 0, timeout: float = 60, json_mode: bool = False,
               client: httpx.AsyncClient | None = None) -> Completion:
    base, name, key = resolve(model)
    body: dict[str, Any] = {"model": name, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await client.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise Unavailable(f"{model}: {type(exc).__name__}") from exc
    finally:
        if own:
            await client.aclose()
    if r.status_code == 429:
        raise RateLimited(f"{model}: {r.text[:500]}", 429, _retry_after(r))
    if r.status_code >= 500:
        raise Unavailable(f"{model}: HTTP {r.status_code} {r.text[:300]}", r.status_code, _retry_after(r))
    if r.status_code >= 400:
        raise LLMError(f"{model}: HTTP {r.status_code} {r.text[:500]}", r.status_code)
    data = r.json()
    message = dict(data["choices"][0]["message"])
    message.setdefault("role", "assistant")
    usage = data.get("usage") or {}
    return Completion(message=message, prompt_tokens=int(usage.get("prompt_tokens") or 0),
                      completion_tokens=int(usage.get("completion_tokens") or 0))
