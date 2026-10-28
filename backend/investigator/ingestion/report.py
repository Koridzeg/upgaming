"""Corpus statistics: duplicates, near-duplicates, and what each chunking strategy produces.

Usage: python -m investigator.ingestion.report
"""

import time
from collections import Counter, defaultdict

from investigator.datasets.hotpotqa import read_corpus, to_document
from investigator.datasets.synthetic import CORPUS_DIR
from investigator.ingestion.chunking import CHUNKERS
from investigator.ingestion.dedup import NEAR_DUPLICATE_MAX_DISTANCE as MAX_DISTANCE
from investigator.ingestion.dedup import hamming, near_duplicate_pairs, simhash
from investigator.ingestion.loaders import load_document
from investigator.ingestion.tokenizer import get_tokenizer
from investigator.text import content_hash


def percentile(values: list[int], p: float) -> int:
    return values[min(len(values) - 1, int(len(values) * p))]


def duplicate_report(name: str, documents: dict[str, str]) -> None:
    """documents: key -> text."""
    started = time.perf_counter()
    groups: dict[str, list[str]] = defaultdict(list)
    hashes = {key: content_hash(text) for key, text in documents.items()}
    for key, digest in hashes.items():
        groups[digest].append(key)
    exact = [keys for keys in groups.values() if len(keys) > 1]
    fingerprints = {key: simhash(text) for key, text in documents.items()}
    # Exact duplicates also have distance 0; report only pairs whose text really differs.
    pairs = [p for p in near_duplicate_pairs(fingerprints) if hashes[p[0]] != hashes[p[1]]]
    elapsed = time.perf_counter() - started

    print(f"\n[{name}] {len(documents):,} documents, dedup in {elapsed:.1f}s")
    print(f"  exact-duplicate groups: {len(exact)}")
    for keys in exact[:3]:
        print(f"    {keys}")
    print(f"  near-duplicate pairs (SimHash distance <= {MAX_DISTANCE}): {len(pairs)}")
    for a, b, distance in pairs[:5]:
        print(f"    d={distance}  {a!r}  ~  {b!r}")


def chunk_report(name: str, documents: list, tokenizer) -> None:
    for strategy, chunker in CHUNKERS.items():
        started = time.perf_counter()
        per_document = [chunker(document, tokenizer) for document in documents]
        elapsed = time.perf_counter() - started
        chunks = [c for doc_chunks in per_document for c in doc_chunks]
        tokens = sorted(c.token_count for c in chunks)
        split = sum(len(doc_chunks) > 1 for doc_chunks in per_document)
        print(
            f"  {strategy:<10} chunks={len(chunks):>7,}  per doc={len(chunks) / len(documents):.2f}"
            f"  docs split={split / len(documents):.1%}  tokens p50={percentile(tokens, 0.5)}"
            f" p95={percentile(tokens, 0.95)} max={tokens[-1]}  ({elapsed:.1f}s)"
        )


def main() -> None:
    tokenizer = get_tokenizer()
    hotpot = [to_document(record) for record in read_corpus()]
    synthetic_files = sorted(CORPUS_DIR.iterdir())
    synthetic = [load_document(p.name, p.read_bytes()) for p in synthetic_files]

    duplicate_report("hotpotqa", {d.title: d.text for d in hotpot})
    by_name = {p.name: d for p, d in zip(synthetic_files, synthetic, strict=True)}
    duplicate_report("synthetic", {name: d.text for name, d in by_name.items()})

    draft = simhash(by_name["postmortem-INC-2026-0311-draft.docx"].text)
    final = simhash(by_name["postmortem-INC-2026-0311.pdf"].text)
    print(f"  draft vs final postmortem: SimHash distance {hamming(draft, final)} bits")

    print("\n[chunking] hotpotqa")
    chunk_report("hotpotqa", hotpot, tokenizer)
    print("[chunking] synthetic")
    chunk_report("synthetic", synthetic, tokenizer)

    kinds = Counter(block.kind for d in synthetic for block in d.blocks)
    print(f"\n  synthetic block kinds: {dict(kinds)}")


if __name__ == "__main__":
    main()
