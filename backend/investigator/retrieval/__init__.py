"""Retrieval: dense search, BM25 keyword search, fusion and re-ranking."""

from investigator.ingestion.chunking import CHUNKERS


def strategy_literal(strategy: str) -> str:
    """The strategy as a quoted SQL literal, after checking it against the known strategies.

    Queries must contain the strategy as a literal, not a $n parameter: only then can the
    planner match `WHERE strategy = 'structural'` to the partial vector index for it. Checking
    against a fixed whitelist is what makes putting it into SQL text safe.
    """
    if strategy not in CHUNKERS:
        raise ValueError(f"Unknown chunking strategy {strategy!r}; known: {sorted(CHUNKERS)}")
    return f"'{strategy}'"
