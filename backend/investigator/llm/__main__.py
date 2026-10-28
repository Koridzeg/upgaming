"""Check the configured LLM providers against the real APIs.

Usage:
    python -m investigator.llm            # tool-call round trip + streaming, per provider
    python -m investigator.llm --models   # model ids your keys can use
"""

import argparse
import asyncio
import time

from investigator.config import get_settings
from investigator.llm.client import LLMClient, client_from_settings
from investigator.llm.errors import LLMError
from investigator.llm.types import Message, ToolSpec

MULTIPLY = ToolSpec(
    name="multiply",
    description="Multiply two integers exactly.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)


async def check(client: LLMClient) -> None:
    for provider in client.providers:
        single = LLMClient([provider])  # no fallback: test this provider on its own
        print(f"\n== {provider.name} ({provider.model})")
        try:
            messages = [
                Message("system", "Use the multiply tool for arithmetic."),
                Message("user", "What is 1234 times 5678?"),
            ]
            first = await single.complete(messages, tools=[MULTIPLY])
            call = first.tool_calls[0] if first.tool_calls else None
            print(f"tool call: {call.name if call else None}({call.arguments if call else ''})"
                  f"  {first.latency_ms:.0f} ms")  # fmt: skip
            if call and call.arguments:
                result = call.arguments["a"] * call.arguments["b"]
                messages += [first.as_message(), Message("tool", str(result), tool_call_id=call.id)]
                final = await single.complete(messages, tools=[MULTIPLY])
                print(f"answer: {final.content!r}  {final.latency_ms:.0f} ms")

            started, pieces = time.perf_counter(), []
            async for delta in single.stream(
                [Message("user", "Count from 1 to 5, comma separated.")]
            ):
                pieces.append(delta)
            elapsed = 1000 * (time.perf_counter() - started)
            print(f"stream: {len(pieces)} chunks -> {''.join(pieces)!r}  {elapsed:.0f} ms")
            print(f"usage: {single.usage()[provider.name]}")
        except LLMError as exc:
            print(f"FAILED: {exc}")


async def list_models(client: LLMClient) -> None:
    for provider in client.providers:
        try:
            models = await provider.list_models()
            print(f"{provider.name}: {len(models)} models")
            for model in models:
                print(f"  {model}{'   <- configured' if model.endswith(provider.model) else ''}")
        except LLMError as exc:
            print(f"{provider.name}: FAILED: {exc}")


async def main_async(models: bool) -> None:
    try:
        client = client_from_settings(get_settings())
    except ValueError as exc:  # no key configured: a setup problem, not a crash
        raise SystemExit(f"error: {exc}") from None
    try:
        await (list_models(client) if models else check(client))
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the configured LLM providers.")
    parser.add_argument("--models", action="store_true", help="list available model ids")
    asyncio.run(main_async(parser.parse_args().models))


if __name__ == "__main__":
    main()
