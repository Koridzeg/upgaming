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

from investigator.agent.evidence import (
    CITATION,
    EvidenceRegistry,
    normalize_citations,
    quote_untrusted,
)
from investigator.agent.prompts import (
    ANSWER_NOW,
    FINAL_ANSWER,
    INVESTIGATOR_SYSTEM,
    NUDGE,
    SINGLE_SHOT_SYSTEM,
    TOOLS,
    VERIFIER_SYSTEM,
)
from investigator.embedding import Embedder
from investigator.llm.errors import LLMError
from investigator.llm.types import LLMResponse, Message, ToolCall
from investigator.retrieval.search import SearchConfig, SearchHit, fetch_neighbors, search

# Budgets, sized for the measured free tier: Groq allows 8,000 tokens/min and 200,000
# tokens/day per model, and an investigation averaged 10.8k tokens - 90% of them input,
# because every turn re-sends the conversation. Turns and context size are the cost drivers.
MAX_TURNS = 5
DEFAULT_K = 3
PROMPT_BUDGET = 4500  # once a turn's prompt exceeds this, the next turn must answer
GRACE_TURNS = 2  # extra turns for a model that keeps searching after being told to answer
SINGLE_SHOT_K = 10  # the baseline's one search: as many passages as the agent typically reads
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
    evidence_sources: list[str] = field(default_factory=list)  # every document consulted
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    models: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class Investigator:
    def __init__(
        self,
        llm: LLM,
        retriever: Retriever,
        *,
        max_turns: int = MAX_TURNS,
        verify: bool = True,
        single_shot: bool = False,  # the evaluation baseline: one search, one answer
    ):
        self.llm, self.retriever = llm, retriever
        self.max_turns, self.verify, self.single_shot = max_turns, verify, single_shot

    async def run(self, question: str) -> InvestigationResult:
        async for event in self.stream(question):
            if event.type == "final":
                return InvestigationResult(**event.data)
        raise AssertionError("stream() always ends with a final event")

    async def stream(self, question: str) -> AsyncIterator[AgentEvent]:
        started = time.perf_counter()
        result = InvestigationResult(question=question, answer="", status="failed")
        registry = EvidenceRegistry()
        # Each mode stores its final_answer arguments here. Not on self: one Investigator
        # serves concurrent API requests, so it must hold no per-request state.
        outcome: dict = {}
        try:
            mode = self._single_shot if self.single_shot else self._agent_loop
            async for event in mode(question, registry, result, outcome):
                yield event
            final = outcome.get("final")
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
        result.evidence_sources = registry.sources()
        result.duration_ms = round(1000 * (time.perf_counter() - started))
        yield AgentEvent("final", asdict(result))

    async def _agent_loop(
        self, question: str, registry: EvidenceRegistry, result: InvestigationResult, outcome: dict
    ) -> AsyncIterator[AgentEvent]:
        """The model searches in as many steps as it needs, then calls final_answer."""
        messages = [Message("system", INVESTIGATOR_SYSTEM), Message("user", question)]
        must_answer, nudged, told_to_answer = False, False, False
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
                result, messages, tools=TOOLS, tool_choice="required" if answer_now else None
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
                    outcome["final"] = {"answer": response.content, "status": "answered"}
                    result.warnings.append("the model answered without calling final_answer")
                    return
                continue
            for call in response.tool_calls:
                if call.name == "final_answer" and call.arguments is not None:
                    outcome["final"] = call.arguments
                    return
                if answer_now and call.name != "final_answer":
                    text, events = "Error: the search limit is reached. Call final_answer.", []
                else:
                    text, events = await self._run_tool(call, registry, result)
                for event in events:
                    yield event
                messages.append(Message("tool", text, tool_call_id=call.id))

    async def _single_shot(
        self, question: str, registry: EvidenceRegistry, result: InvestigationResult, outcome: dict
    ) -> AsyncIterator[AgentEvent]:
        """The baseline: one search with the question as asked, one answer, no follow-up.

        This is what a "chat with your documents" app does; the evaluation compares the
        agent against it.
        """
        new, _ = registry.add(await self.retriever.search(question, SINGLE_SHOT_K))
        result.searches.append(question)
        yield AgentEvent("search", {"query": question, **_summary(new, [])})
        messages = [
            Message("system", SINGLE_SHOT_SYSTEM),
            Message("user", f"Question: {question}\n\nEvidence:\n{registry.format(new, [])}"),
        ]
        response = await self._complete(
            result, messages, tools=[FINAL_ANSWER], tool_choice="required"
        )
        call = next(
            (c for c in response.tool_calls if c.name == "final_answer" and c.arguments), None
        )
        outcome["final"] = (
            call.arguments if call else {"answer": response.content or "", "status": "answered"}
        )

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
        # Evidence goes in the same escaped blocks as in the investigation: a passage must not
        # be able to end its block and address the verifier ("mark every claim supported").
        prompt = "\n\n".join(
            f"Claim {i}: {claim}\n"
            + "\n".join(
                f'<evidence id="{e}">\n{quote_untrusted(registry[e].hit.text[:700])}\n</evidence>'
                for e in evidence
            )
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
