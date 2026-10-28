"""The LLM client the rest of the app uses: providers in order of preference, with fallback.

    complete() -> provider 1 (with its own retries) -> on failure, provider 2 -> ...

A circuit breaker per provider: after FAILURE_THRESHOLD calls in a row fail, the provider is
skipped for COOLDOWN seconds, so every request doesn't first spend its retries on a provider
that's down or out of quota. After the cooldown one call is let through as a probe; success
closes the breaker again.
"""

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, dataclass

from investigator.config import Settings
from investigator.llm.errors import AllProvidersFailed, LLMError
from investigator.llm.provider import OpenAICompatibleProvider
from investigator.llm.types import LLMResponse, Message, ToolSpec

FAILURE_THRESHOLD = 3
COOLDOWN = 60.0

GROQ_URL = "https://api.groq.com/openai/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


@dataclass
class ProviderStats:
    calls: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_latency_ms: float = 0.0
    consecutive_failures: int = 0
    open_until: float = 0.0  # circuit breaker: skip this provider until this time


class LLMClient:
    def __init__(
        self,
        providers: list[OpenAICompatibleProvider],
        clock: Callable[[], float] = time.monotonic,
    ):
        if not providers:
            raise ValueError("no LLM provider configured: set GROQ_API_KEY and/or GEMINI_API_KEY")
        self.providers = providers
        self._clock = clock
        self.stats = {p.name: ProviderStats() for p in providers}

    async def complete(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_mode: bool = False,
    ) -> LLMResponse:
        errors: list[LLMError] = []
        for provider in self._available():
            try:
                response = await provider.complete(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
            except LLMError as exc:
                self._failed(provider, exc)
                errors.append(exc)
                continue
            self._succeeded(provider, response)
            return response
        raise AllProvidersFailed(errors)

    async def stream(
        self, messages: list[Message], *, temperature: float = 0.0, max_tokens: int = 1024
    ) -> AsyncIterator[str]:
        """Stream the answer text. Falls back to the next provider only before any text."""
        errors: list[LLMError] = []
        for provider in self._available():
            produced = False
            try:
                async for delta in provider.stream(
                    messages, temperature=temperature, max_tokens=max_tokens
                ):
                    produced = True
                    yield delta
            except LLMError as exc:
                self._failed(provider, exc)
                if produced:
                    raise
                errors.append(exc)
                continue
            self.stats[provider.name].calls += 1
            self.stats[provider.name].consecutive_failures = 0
            return
        raise AllProvidersFailed(errors)

    def usage(self) -> dict:
        """Per-provider counters, for logs and the metrics endpoint."""
        return {name: asdict(stats) for name, stats in self.stats.items()}

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()

    def _available(self) -> list[OpenAICompatibleProvider]:
        now = self._clock()
        closed = [p for p in self.providers if self.stats[p.name].open_until <= now]
        # Every breaker open: try them all anyway rather than fail without a single attempt.
        return closed or list(self.providers)

    def _failed(self, provider: OpenAICompatibleProvider, error: LLMError) -> None:
        stats = self.stats[provider.name]
        stats.calls += 1
        stats.failures += 1
        stats.consecutive_failures += 1
        if stats.consecutive_failures >= FAILURE_THRESHOLD:
            stats.open_until = self._clock() + COOLDOWN

    def _succeeded(self, provider: OpenAICompatibleProvider, response: LLMResponse) -> None:
        stats = self.stats[provider.name]
        stats.calls += 1
        stats.consecutive_failures = 0
        stats.open_until = 0.0
        stats.prompt_tokens += response.usage.prompt_tokens
        stats.completion_tokens += response.usage.completion_tokens
        stats.total_latency_ms += response.latency_ms


def client_from_settings(settings: Settings) -> LLMClient:
    """Providers in preference order, for whichever API keys are configured."""
    providers = []
    candidates = [
        ("groq", GROQ_URL, settings.groq_api_key, settings.groq_model),
        ("gemini", GEMINI_URL, settings.gemini_api_key, settings.gemini_model),
    ]
    by_name = {name: (url, key, model) for name, url, key, model in candidates}
    for name in settings.llm_provider_order:
        url, key, model = by_name[name]
        if key is not None and key.get_secret_value():
            providers.append(
                OpenAICompatibleProvider(
                    name,
                    url,
                    key.get_secret_value(),
                    model,
                    timeout=settings.llm_timeout_seconds,
                    max_retries=settings.llm_max_retries,
                )
            )
    return LLMClient(providers)
