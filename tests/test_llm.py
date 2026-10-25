"""payguard.llm: the OpenAI-compatible client that replaced LiteLLM. No network: httpx.MockTransport."""

import json

import httpx
import pytest

from payguard import llm


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_routes_by_provider_and_sends_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"aligned": true}'}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    out = await llm.chat("groq/openai/gpt-oss-20b", [{"role": "user", "content": "hi"}], json_mode=True,
                         client=client(handler))
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk_test"
    assert seen["body"]["model"] == "openai/gpt-oss-20b" and seen["body"]["response_format"] == {"type": "json_object"}
    assert out.message == {"content": '{"aligned": true}', "role": "assistant"} and out.prompt_tokens == 12


async def test_tool_calls_pass_through(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "m")
    call = {"id": "c1", "type": "function", "function": {"name": "fetch_payment", "arguments": '{"payment_id": "pay_1"}'}}
    out = await llm.chat("mistral/ministral-14b-2512", [], tools=[{"type": "function"}], client=client(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}]})))
    assert out.message["tool_calls"] == [call]


@pytest.mark.parametrize("status,error", [(429, llm.RateLimited), (503, llm.Unavailable), (400, llm.LLMError)])
async def test_errors(monkeypatch, status, error):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    with pytest.raises(error) as info:
        await llm.chat("gemini/gemini-3.5-flash-lite", [], client=client(
            lambda r: httpx.Response(status, headers={"retry-after": "7"}, text="slow down")))
    if status == 429:
        assert info.value.retry_after == 7.0


async def test_connection_failure_is_retryable(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")

    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(llm.Unavailable):
        await llm.chat("groq/openai/gpt-oss-20b", [], client=client(boom))


def test_missing_key_and_unknown_provider(monkeypatch):
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    assert not llm.has_key("nvidia_nim/moonshotai/kimi-k2-instruct")
    assert llm.has_key("ollama/qwen3:4b")  # local, no key
    monkeypatch.setenv("GROQ_API_KEY", "xxx")
    assert not llm.has_key("groq/openai/gpt-oss-20b")  # the .env.example placeholder doesn't count
    with pytest.raises(llm.LLMError, match="unknown model provider"):
        llm.resolve("openrouter/some-model")
