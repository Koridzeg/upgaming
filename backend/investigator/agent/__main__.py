"""Run an investigation and watch it work.

Usage: python -m investigator.agent "question" [--no-verify] [--source-type upload]
"""

import argparse
import asyncio
import textwrap

from investigator.agent.investigation import DatabaseRetriever, Investigator
from investigator.config import get_settings
from investigator.db import create_pool
from investigator.embedding import get_embedder
from investigator.llm.client import client_from_settings
from investigator.retrieval.search import SearchConfig


async def main_async(question: str, verify: bool, source_types: list[str] | None) -> None:
    settings = get_settings()
    pool = await create_pool(settings)
    llm = client_from_settings(settings)
    retriever = DatabaseRetriever(pool, get_embedder(), SearchConfig(source_types=source_types))
    try:
        async for event in Investigator(llm, retriever, verify=verify).stream(question):
            data = event.data
            if event.type in ("search", "read_more"):
                label = data.get("query") or data.get("evidence_id")
                found = ", ".join(f"{f['id']} {f['title'][:40]}" for f in data["found"])
                print(f"[{event.type}] {label}\n    -> {found or 'nothing new'}")
            elif event.type in ("warning", "error", "tool_error"):
                print(f"[{event.type}] {data}")
            elif event.type == "verification":
                for claim in data["claims"]:
                    print(f"[verify] {claim['verdict']:<11} {claim['claim'][:90]}")
            elif event.type == "final":
                print_result(data)
    finally:
        await llm.aclose()
        await pool.close()


def print_result(r: dict) -> None:
    print(f"\n=== {r['status'].upper()}\n" + textwrap.fill(r["answer"], 100))
    for c in r["contradictions"]:
        print(f"\nCONTRADICTION: {c['topic']}")
        for p in c["positions"]:
            print(f"  - {p['claim']} {p['evidence']}")
        if c.get("assessment"):
            print(f"  assessment: {c['assessment']}")
    if r["missing"]:
        print(f"\nMISSING: {r['missing']}")
    print("\nCITATIONS")
    for c in r["citations"]:
        print(f"  {c['id']}: {c['title']} ({c['status'] or '-'}, {c['date'] or '-'})")
    print(
        f"\n{r['llm_calls']} LLM calls, {r['prompt_tokens']:,} + {r['completion_tokens']:,} "
        f"tokens, {len(r['searches'])} searches, {r['evidence_seen']} passages, "
        f"{r['duration_ms'] / 1000:.1f}s, models {r['models']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an investigation.")
    parser.add_argument("question")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--source-type", action="append")
    args = parser.parse_args()
    asyncio.run(main_async(args.question, not args.no_verify, args.source_type))


if __name__ == "__main__":
    main()
