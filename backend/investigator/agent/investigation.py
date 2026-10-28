"""The investigation loop: the LLM searches in as many steps as it needs, then answers.

    question -> [LLM -> tool calls (search / read_more) -> evidence -> LLM -> ...]
             -> final_answer -> citation check -> verifier -> result

The LLM decides what to search next - that is what multi-hop needs: the second search can use
a name only the first search revealed. Our code keeps it honest:
- evidence gets ids; citations to ids never shown are removed and reported;
- the last allowed turn may only call final_answer, so the loop always ends with an answer;
- a second LLM pass (the verifier) checks each cited claim against the cited text;
- provider failures end the run with status "failed", never an unhandled exception.

`stream()` yields events as they happen (for live progress in the UI); `run()` returns the
final result.
"""

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field, replace
from typing import Protocol

import asyncpg

from investigator.agent.evidence import CITATION, EvidenceRegistry, normalize_citations
from investigator.agent.prompts import (
    ANSWER_NOW,
    INVESTIGATOR_SYSTEM,
    NUDGE,
    TOOLS,
    VERIFIER_SYSTEM,
)
from investigator.embedding import Embedder
from investigator.llm.errors import LLMError
from investigator.llm.types import LLMResponse, Message, ToolCall
from investigator.retrieval.search import SearchConfig, SearchHit, fetch_neighbors, search

# Budgets, sized for the free tiers measured in Step 9 (Groq: 8,000 tokens/min per model).
# Every turn re-sends the whole conversation, so turns and context size are the cost drivers.
MAX_TURNS = 6
DEFAULT_K = 4
PROMPT_BUDGET = 6000  # once a turn's prompt exceeds this, the next turn must answer
GRACE_TURNS = 2  # extra turns for a model that keeps searching after being told to answer
STATUSES = ("answered", "partial", "insufficient_evidence")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_EVIDENCE_ID = re.compile(r"E\d+")


class Retriever(Protocol):
    async def search(self, query: str, k: int) -> list[SearchHit]: ...

    async def neighbors(self, chunk_id: int) -> list[SearchHit]: ...


class DatabaseRetriever:
    def __init__(self, pool: asyncpg.Pool, embedder: Embedder, config: SearchConfig | None = None):
        self.pool, self.embedder = pool, embedder
        self.config = config or SearchConfig()

    async def search(self, query: str, k: int) -> list[SearchHit]:
        async with self.pool.acquire() as conn:
            return await search(conn, self.embedder, query, replace(self.config, k=k))

    async def neighbors(self, chunk_id: int) -> list[SearchHit]:
        async with self.pool.acquire() as conn:
            return await fetch_neighbors(conn, chunk_id)


class LLM(Protocol):  # what the loop needs from investigator.llm.client.LLMClient
    async def complete(self, messages: list[Message], **kwargs) -> LLMResponse: ...


@dataclass
class AgentEvent:
    type: str  # search | read_more | tool_error | verification | warning | final | error
    data: dict


@dataclass
class InvestigationResult:
    question: str
    answer: str
    status: str  # answered | partial | insufficient_evidence | failed
    citations: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    missing: str | None = None
    verification: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    evidence_seen: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    models: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class Investigator:
    def __init__(
        self, llm: LLM, retriever: Retriever, *, max_turns: int = MAX_TURNS, verify: bool = True
    ):
        self.llm, self.retriever = llm, retriever
        self.max_turns, self.verify = max_turns, verify

    async def run(self, question: str) -> InvestigationResult:
        async for event in self.stream(question):
            if event.type == "final":
                return InvestigationResult(**event.data)
        raise AssertionError("stream() always ends with a final event")

    async def stream(self, question: str) -> AsyncIterator[AgentEvent]:
        started = time.perf_counter()
        result = InvestigationResult(question=question, answer="", status="failed")
        registry = EvidenceRegistry()
        messages = [Message("system", INVESTIGATOR_SYSTEM), Message("user", question)]
        try:
            final, must_answer, nudged, told_to_answer = None, False, False, False
            for turn in range(self.max_turns + GRACE_TURNS):
                # The last turn, or any turn after the context grew past budget, must answer.
                # All tools stay offered: Groq rejects (400) a call to a tool that isn't in the
                # request, and models do try to search once more (seen live). The instruction
                # says "answer now", and a search on such a turn gets an error back instead.
                answer_now = must_answer or turn >= self.max_turns - 1
                if answer_now and not told_to_answer:
                    messages.append(Message("user", ANSWER_NOW))
                    told_to_answer = True
                response = await self._complete(
                    result,
                    messages,
                    tools=TOOLS,
                    tool_choice="required" if answer_now else None,
                )
                messages.append(response.as_message())
                must_answer = must_answer or response.usage.prompt_tokens > PROMPT_BUDGET
                if not response.tool_calls:
                    # Plain text (or nothing) instead of final_answer. Measured: the smaller
                    # model did this, with no citations. Ask once for the structured form.
                    if not nudged:
                        nudged = must_answer = told_to_answer = True
                        messages.append(Message("user", NUDGE))
                        continue
                    if response.content:
                        final = {"answer": response.content, "status": "answered"}
                        result.warnings.append("the model answered without calling final_answer")
                        break
                    continue
                for call in response.tool_calls:
                    if call.name == "final_answer" and call.arguments is not None:
                        final = call.arguments
                        break
                    if answer_now and call.name != "final_answer":
                        text, events = "Error: the search limit is reached. Call final_answer.", []
                    else:
                        text, events = await self._run_tool(call, registry, result)
                    for event in events:
                        yield event
                    messages.append(Message("tool", text, tool_call_id=call.id))
                if final is not None:
                    break
            if final is None:
                result.warnings.append(f"no final answer after {self.max_turns} turns")
                final = {"answer": "", "status": "insufficient_evidence",
                         "missing": "The investigation ran out of steps."}  # fmt: skip

            self._finish(result, final, registry)
            for warning in result.warnings:
                yield AgentEvent("warning", {"message": warning})
            if self.verify and result.citations:
                await self._verify(result, registry)
                yield AgentEvent("verification", {"claims": result.verification})
        except LLMError as exc:
            result.status = "failed"
            result.answer = (
                "The investigation could not be completed: the language model is unavailable."
            )
            result.warnings.append(str(exc))
            yield AgentEvent("error", {"message": str(exc)})
        result.evidence_seen = len(registry)
        result.duration_ms = round(1000 * (time.perf_counter() - started))
        yield AgentEvent("final", asdict(result))

    async def _complete(self, result: InvestigationResult, messages, **kwargs) -> LLMResponse:
        response = await self.llm.complete(messages, max_tokens=4096, **kwargs)
        result.llm_calls += 1
        result.prompt_tokens += response.usage.prompt_tokens
        result.completion_tokens += response.usage.completion_tokens
        if response.provider not in result.models:
            result.models.append(response.provider)
        return response

    async def _run_tool(
        self, call: ToolCall, registry: EvidenceRegistry, result: InvestigationResult
    ) -> tuple[str, list[AgentEvent]]:
        if call.arguments is None:  # malformed JSON from the model: let it correct itself
            message = f"Error: {call.error}. Call {call.name} again with valid JSON arguments."
            return message, [AgentEvent("tool_error", {"tool": call.name, "error": call.error})]

        if call.name == "search":
            query = str(call.arguments.get("query", "")).strip()
            if not query:
                return "Error: 'query' must not be empty.", []
            try:
                k = min(max(int(call.arguments.get("k", DEFAULT_K)), 1), 8)
            except (TypeError, ValueError):
                k = DEFAULT_K
            new, seen = registry.add(await self.retriever.search(query, k))
            result.searches.append(query)
            event = AgentEvent("search", {"query": query, **_summary(new, seen)})
            return registry.format(new, seen), [event]

        if call.name == "read_more":
            evidence_id = str(call.arguments.get("evidence_id", "")).strip()
            if evidence_id not in registry:
                return f"Error: unknown evidence id {evidence_id!r}.", []
            new, seen = registry.add(
                await self.retriever.neighbors(registry[evidence_id].hit.chunk_id)
            )
            event = AgentEvent("read_more", {"evidence_id": evidence_id, **_summary(new, seen)})
            return registry.format(new, seen), [event]

        return f"Error: unknown tool {call.name!r}.", []

    def _finish(self, result: InvestigationResult, final: dict, registry: EvidenceRegistry) -> None:
        answer = normalize_citations(str(final.get("answer", "")).strip())
        valid, invalid = registry.check_citations(answer)
        if invalid:
            # A citation to evidence the model never saw is a fabrication: remove it, say so.
            answer = CITATION.sub(
                lambda m: "" if f"E{m.group(1)}" in invalid else m.group(0), answer
            )
            result.warnings.append(f"removed citations to evidence that was never shown: {invalid}")
        status = final.get("status")
        result.status = status if status in STATUSES else "answered"
        if result.status == "answered" and not valid:
            result.warnings.append("the answer cites no evidence")

        result.answer = answer
        result.citations = [registry[c].describe() for c in valid]
        result.missing = final.get("missing") or None
        for item in final.get("contradictions") or []:
            positions = []
            for position in item.get("positions") or []:
                # Ids may come as "【E2】" or "[E2]" here too (seen live): keep just "E2".
                ids = [_EVIDENCE_ID.search(str(e)) for e in position.get("evidence") or []]
                cited = [m.group(0) for m in ids if m and m.group(0) in registry]
                positions.append({"claim": position.get("claim", ""), "evidence": cited})
            result.contradictions.append(
                {"topic": item.get("topic", ""), "positions": positions,
                 "assessment": item.get("assessment")}
            )  # fmt: skip

    async def _verify(self, result: InvestigationResult, registry: EvidenceRegistry) -> None:
        """Second opinion: does each cited sentence say what its evidence says?"""
        claims = []
        for sentence in _SENTENCE_END.split(result.answer):
            cited = [f"E{n}" for n in CITATION.findall(sentence) if f"E{n}" in registry]
            if cited:
                claims.append((CITATION.sub("", sentence).strip(), list(dict.fromkeys(cited))))
        claims = claims[:12]  # bound the verifier's prompt
        prompt = "\n\n".join(
            f"Claim {i}: {claim}\n"
            + "\n".join(f"[{e}] {registry[e].hit.text[:700]}" for e in evidence)
            for i, (claim, evidence) in enumerate(claims, start=1)
        )
        try:
            response = await self._complete(
                result,
                [Message("system", VERIFIER_SYSTEM), Message("user", prompt)],
                json_mode=True,
            )
            verdicts = json.loads(response.content or "{}").get("claims", [])
        except (LLMError, json.JSONDecodeError, AttributeError) as exc:
            result.warnings.append(f"verification could not run: {exc}")
            return
        for verdict in verdicts:
            try:
                claim, evidence = claims[int(verdict["claim"]) - 1]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            result.verification.append(
                {"claim": claim, "evidence": evidence, "verdict": verdict.get("verdict"),
                 "reason": verdict.get("reason")}
            )  # fmt: skip
        unsupported = [v for v in result.verification if v["verdict"] == "unsupported"]
        if unsupported:
            result.warnings.append(f"{len(unsupported)} claim(s) not supported by their citations")


def _summary(new, seen) -> dict:
    return {
        "found": [{"id": e.id, "title": e.hit.title, "section": e.hit.context,
                   "suspicious": e.suspicious} for e in new],
        "repeated": [e.id for e in seen],
    }  # fmt: skip
