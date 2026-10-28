"""One OpenAI-compatible chat-completions endpoint (Groq and Gemini both offer one).

Turns HTTP into typed results and typed errors (see errors.py), and retries the errors worth
retrying on this provider: exponential backoff with jitter, or exactly the wait the provider
asked for in a rate-limit response.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx2 as httpx

from investigator.llm.errors import (
    AuthenticationFailed,
    InvalidResponse,
    LLMError,
    ProviderUnavailable,
    RateLimited,
    RequestRejected,
)
from investigator.llm.types import LLMResponse, Message, ToolCall, ToolSpec, Usage

logger = logging.getLogger(__name__)

# Gemini puts the wait into the error body ("retryDelay": "17s") instead of a header.
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,  # for failures: 5xx, timeouts, malformed responses
        max_rate_limit_waits: int = 4,  # for 429s that say how long to wait
        max_wait: float = 20.0,  # longer rate-limit waits go to the next provider instead
        signed_tool_calls: bool = False,  # Gemini 3: tool calls in history need signatures
        transport: httpx.AsyncBaseTransport | None = None,  # tests pass a MockTransport
        sleep: Callable[[float], Awaitable] = asyncio.sleep,
    ):
        self.name, self.model = name, model
        self.max_retries, self.max_wait = max_retries, max_wait
        self.max_rate_limit_waits = max_rate_limit_waits
        self.signed_tool_calls = signed_tool_calls
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,  # "auto" (default), "required" or "none"
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        body = self._body(messages, tools, temperature, max_tokens, json_mode)
        if tools and tool_choice:
            body["tool_choice"] = tool_choice
        retries = _Retries(self)
        while True:
            started = time.perf_counter()
            try:
                response = await self._post(body)
                result = self._parse(response.json(), started)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                error: LLMError = InvalidResponse(f"unexpected response: {exc}", self.name)
            except LLMError as exc:
                error = exc
            else:
                logger.info(
                    "llm call provider=%s model=%s ms=%.0f prompt_tokens=%d completion_tokens=%d",
                    self.name, self.model, result.latency_ms,
                    result.usage.prompt_tokens, result.usage.completion_tokens,
                )  # fmt: skip
                return result
            await retries.wait_or_raise(error)

    async def stream(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """Yield the answer text as it is generated (no tools; used for the final answer).

        Retries happen only before the first token: once text has reached the user, a retry
        would repeat it, so a mid-stream failure is raised instead.
        """
        body = self._body(messages, None, temperature, max_tokens, json_mode=False)
        body["stream"] = True
        retries = _Retries(self)
        while True:
            started_output = False
            try:
                async with self._http.stream("POST", "/chat/completions", json=body) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise self._error(response)
                    async for line in response.aiter_lines():
                        # Server-sent events: 'data: {json}' per chunk, 'data: [DONE]' at the end.
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        choices = json.loads(payload).get("choices") or [{}]
                        delta = choices[0].get("delta", {}).get("content")
                        if delta:
                            started_output = True
                            yield delta
                    return
            except httpx.TimeoutException as exc:
                error: LLMError = ProviderUnavailable(f"timeout: {exc}", self.name)
            except httpx.TransportError as exc:
                error = ProviderUnavailable(f"connection failed: {exc}", self.name)
            except LLMError as exc:
                error = exc
            if started_output:
                raise error
            await retries.wait_or_raise(error)

    async def list_models(self) -> list[str]:
        response = await self._http.get("/models")
        if response.status_code != 200:
            raise self._error(response)
        return sorted(m["id"] for m in response.json().get("data", []))

    async def aclose(self) -> None:
        await self._http.aclose()

    def _body(self, messages, tools, temperature, max_tokens, json_mode) -> dict:
        wire = [m.to_openai() for m in messages]
        if self.signed_tool_calls:
            wire = _flatten_unsigned_tool_calls(wire)
        body: dict = {
            "model": self.model,
            "messages": wire,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [t.to_openai() for t in tools]
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def _post(self, body: dict) -> httpx.Response:
        try:
            response = await self._http.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(f"timeout: {exc}", self.name) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailable(f"connection failed: {exc}", self.name) from exc
        if response.status_code != 200:
            raise self._error(response)
        return response

    def _error(self, response: httpx.Response) -> LLMError:
        status, text = response.status_code, response.text[:500]
        if status == 429:
            return RateLimited(f"429 {text}", self.name, retry_after=_retry_after(response))
        if status in (401, 403):
            return AuthenticationFailed(f"{status}: check the API key", self.name)
        if status >= 500:
            return ProviderUnavailable(f"{status} {text}", self.name)
        if '"tool_use_failed"' in text or '"output_parse_failed"' in text:
            # Groq validates the model's own output and answers 400 when it is malformed (seen
            # live: gpt-oss calling a non-existent tool 'commentary', sending null for a string
            # field, or writing unparseable output). A bad *generation*, not a bad request:
            # worth a retry.
            return InvalidResponse(f"model produced invalid output: {text}", self.name)
        return RequestRejected(f"{status} {text}", self.name)

    def _parse(self, data: dict, started: float) -> LLMResponse:
        choice = data["choices"][0]
        message = choice["message"]
        usage = data.get("usage") or {}
        return LLMResponse(
            content=message.get("content"),
            tool_calls=[
                _tool_call(call, i) for i, call in enumerate(message.get("tool_calls") or [])
            ],
            finish_reason=choice.get("finish_reason"),
            usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
            provider=self.name,
            model=data.get("model", self.model),
            latency_ms=1000 * (time.perf_counter() - started),
        )


class _Retries:
    """Whether, and how long, to wait before the next attempt of one request.

    Two separate budgets, because the two situations differ:
    - a 429 that says how long to wait (Groq's token budget refills within seconds): waiting is
      cheap and keeps the stronger model, so up to max_rate_limit_waits such waits;
    - a failure (5xx, timeout, garbage): exponential backoff with jitter, max_retries times.
    """

    def __init__(self, provider: "OpenAICompatibleProvider"):
        self.provider = provider
        self.failures = 0
        self.waits = 0

    async def wait_or_raise(self, error: LLMError) -> None:
        p = self.provider
        if isinstance(error, RateLimited) and error.retry_after is not None:
            if error.retry_after > p.max_wait or self.waits >= p.max_rate_limit_waits:
                raise error  # a long wait is worse than asking the next model
            self.waits += 1
            delay = error.retry_after
        else:
            if not error.retryable or self.failures >= p.max_retries:
                raise error
            # Backoff 1-2 s, then 2-4 s... The randomness spreads out clients that failed
            # together, so they don't all retry in the same instant.
            delay = 2**self.failures * random.uniform(1.0, 2.0)
            self.failures += 1
        logger.warning("retrying %s in %.1fs after: %s", p.name, delay, error)
        await p._sleep(delay)


def _tool_call(call: dict, index: int) -> ToolCall:
    function = call["function"]
    raw = function.get("arguments") or "{}"
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
        error = None if isinstance(arguments, dict) else "arguments must be a JSON object"
    except json.JSONDecodeError as exc:
        arguments, error = None, f"invalid JSON arguments: {exc}"
    return ToolCall(
        id=call.get("id") or f"call_{index}",  # some providers omit ids; tool results need one
        name=function["name"],
        arguments=arguments if error is None else None,
        raw_arguments=raw if isinstance(raw, str) else json.dumps(raw),
        error=error,
        extra={k: v for k, v in call.items() if k not in ("id", "type", "function")},
    )


def _flatten_unsigned_tool_calls(messages: list[dict]) -> list[dict]:
    """Rewrite tool calls that carry no signature as plain text.

    Gemini 3 rejects (400) a history containing tool calls without its thought_signature -
    exactly what a conversation has after another provider made those calls and Gemini takes
    over as the fallback. As plain text ("earlier tool call ... and its result") the same
    information is accepted by any model.
    """
    flattened, unsigned = [], set()
    for message in messages:
        calls = message.get("tool_calls") or []
        if calls and not all("extra_content" in call for call in calls):
            unsigned.update(call["id"] for call in calls)
            described = "; ".join(
                f"{c['function']['name']}({c['function']['arguments']})" for c in calls
            )
            text = f"{message.get('content') or ''}\n[Earlier tool calls: {described}]".strip()
            flattened.append({"role": "assistant", "content": text})
        elif message["role"] == "tool" and message.get("tool_call_id") in unsigned:
            content = f"[Result of tool call {message['tool_call_id']}]\n{message['content']}"
            flattened.append({"role": "user", "content": content})
        else:
            flattened.append(message)
    return flattened


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            return None  # an HTTP date; rare enough to fall back to backoff
    match = _RETRY_DELAY.search(response.text)
    return float(match.group(1)) if match else None
