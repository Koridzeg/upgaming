"""The LLM client against a mocked HTTP transport: every failure mode, no network."""

import asyncio
import json

import httpx2 as httpx
import pytest

from investigator.llm.client import COOLDOWN, FAILURE_THRESHOLD, LLMClient
from investigator.llm.errors import AllProvidersFailed, AuthenticationFailed, RequestRejected
from investigator.llm.provider import OpenAICompatibleProvider
from investigator.llm.types import Message, ToolSpec

QUESTION = [Message("user", "What is 2 + 2?")]


def ok(content="4", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "model": "model-x",
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        },
    )


class Scripted:
    """A transport handler that replies with the given responses (or exceptions) in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def make(name: str, handler: Scripted, **kwargs):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider = OpenAICompatibleProvider(
        name,
        "https://llm.test/v1",
        "secret-key",
        "model-x",
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
        **kwargs,
    )
    return provider, sleeps


def test_success_parses_the_reply_and_sends_an_openai_request():
    handler = Scripted(ok("4"))
    provider, _ = make("groq", handler)
    tool = ToolSpec("search", "Search documents.", {"type": "object", "properties": {}})

    response = asyncio.run(LLMClient([provider]).complete(QUESTION, tools=[tool]))

    assert (response.content, response.provider, response.usage.prompt_tokens) == ("4", "groq", 12)
    sent = json.loads(handler.requests[0].content)
    assert sent["model"] == "model-x"
    assert sent["messages"] == [{"role": "user", "content": "What is 2 + 2?"}]
    assert sent["tools"][0]["function"]["name"] == "search"
    assert handler.requests[0].headers["authorization"] == "Bearer secret-key"


def test_tool_calls_are_parsed_and_bad_arguments_reported_not_raised():
    calls = [
        {"id": "a1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}},
        {"type": "function", "function": {"name": "search", "arguments": '{"q": '}},  # cut off
    ]
    provider, _ = make("groq", Scripted(ok(None, calls)))

    response = asyncio.run(provider.complete(QUESTION))

    good, bad = response.tool_calls
    assert (good.id, good.arguments, good.error) == ("a1", {"q": "x"}, None)
    assert bad.id == "call_1"  # the provider omitted it; a tool result must reference one
    assert bad.arguments is None and "invalid JSON" in bad.error


def test_short_rate_limit_waits_exactly_as_asked_then_succeeds():
    handler = Scripted(httpx.Response(429, headers={"retry-after": "2"}), ok())
    provider, sleeps = make("groq", handler)

    assert asyncio.run(provider.complete(QUESTION)).content == "4"
    assert sleeps == [2.0]


def test_gemini_style_retry_delay_in_the_body_is_honoured():
    body = {"error": {"code": 429, "details": [{"retryDelay": "3s"}]}}
    provider, sleeps = make("gemini", Scripted(httpx.Response(429, json=body), ok()))

    asyncio.run(provider.complete(QUESTION))

    assert sleeps == [3.0]


def test_long_rate_limit_moves_to_the_next_provider_without_waiting():
    groq, groq_sleeps = make("groq", Scripted(httpx.Response(429, headers={"retry-after": "120"})))
    gemini, _ = make("gemini", Scripted(ok("from gemini")))

    response = asyncio.run(LLMClient([groq, gemini]).complete(QUESTION))

    assert (response.provider, response.content) == ("gemini", "from gemini")
    assert groq_sleeps == []  # 120 s exceeds max_wait: don't wait, fall back


def test_server_errors_and_timeouts_are_retried_with_growing_backoff():
    handler = Scripted(httpx.Response(503), httpx.ReadTimeout("slow"), ok())
    provider, sleeps = make("groq", handler, max_retries=2)

    assert asyncio.run(provider.complete(QUESTION)).content == "4"
    assert 1.0 <= sleeps[0] <= 2.0 and 2.0 <= sleeps[1] <= 4.0  # 2^attempt x jitter in [1, 2]


def test_bad_key_is_not_retried_but_falls_back():
    groq, groq_sleeps = make("groq", Scripted(httpx.Response(401)))
    gemini, _ = make("gemini", Scripted(ok()))

    assert asyncio.run(LLMClient([groq, gemini]).complete(QUESTION)).provider == "gemini"
    assert groq_sleeps == []


def test_garbage_body_is_retried_once_then_falls_back():
    groq_handler = Scripted(httpx.Response(200, text="<html>proxy error</html>"))
    groq, groq_sleeps = make("groq", groq_handler, max_retries=1)
    gemini, _ = make("gemini", Scripted(ok()))

    assert asyncio.run(LLMClient([groq, gemini]).complete(QUESTION)).provider == "gemini"
    assert len(groq_handler.requests) == 2 and len(groq_sleeps) == 1


def test_when_everything_fails_every_error_is_reported():
    groq, _ = make("groq", Scripted(httpx.Response(401)))
    gemini, _ = make("gemini", Scripted(httpx.Response(400, text="context too long")))

    with pytest.raises(AllProvidersFailed) as failure:
        asyncio.run(LLMClient([groq, gemini]).complete(QUESTION))

    kinds = [type(e) for e in failure.value.errors]
    assert kinds == [AuthenticationFailed, RequestRejected]


def test_circuit_breaker_skips_a_failing_provider_then_probes_it_after_cooldown():
    now = [1000.0]
    groq_handler = Scripted(httpx.Response(500))
    groq, _ = make("groq", groq_handler, max_retries=0)
    gemini, _ = make("gemini", Scripted(ok()))
    client = LLMClient([groq, gemini], clock=lambda: now[0])

    async def ask_times(n):
        for _ in range(n):
            await client.complete(QUESTION)

    asyncio.run(ask_times(FAILURE_THRESHOLD))
    assert len(groq_handler.requests) == FAILURE_THRESHOLD  # tried every time until it opened

    asyncio.run(ask_times(5))
    assert len(groq_handler.requests) == FAILURE_THRESHOLD  # open: skipped, straight to gemini

    now[0] += COOLDOWN + 1
    asyncio.run(ask_times(1))
    assert len(groq_handler.requests) == FAILURE_THRESHOLD + 1  # cooldown over: one probe


def sse(*chunks: str) -> httpx.Response:
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}" for c in chunks]
    return httpx.Response(200, text="\n\n".join([*lines, "data: [DONE]"]) + "\n\n")


def test_streaming_yields_text_and_falls_back_before_the_first_token():
    groq, _ = make("groq", Scripted(httpx.Response(503)), max_retries=0)
    gemini, _ = make("gemini", Scripted(sse("Hel", "lo", "!")))

    async def collect():
        return [delta async for delta in LLMClient([groq, gemini]).stream(QUESTION)]

    assert asyncio.run(collect()) == ["Hel", "lo", "!"]


def test_no_configured_provider_is_a_clear_error():
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        LLMClient([])
