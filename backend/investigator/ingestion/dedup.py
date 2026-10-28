"""Duplicate detection.

Exact duplicates: equal `content_hash` (SHA-256 of the normalised text). Enforced by a UNIQUE
index; they carry no new information, so they are not stored.

Near duplicates: SimHash (Charikar, 2002; used for web-scale dedup by Manku et al., 2007). Each
document becomes a 64-bit fingerprint such that similar texts get fingerprints differing in few
bits. They are kept and linked, not dropped: a draft and its final version are near duplicates,
and the few words that differ (a corrected number) can be exactly the contradiction an
investigation needs to see.

Threshold: the classic 3 bits was tuned on long web pages. Measured on HotpotQA paragraphs, a
one-word edit stays within 3 bits only 6-38% of the time (for 10-200 words), but unrelated
paragraphs are a median 32 bits apart (minimum 21 in a 500-pair sample). Hence 6 bits.
"""

import hashlib
from collections import Counter

import numpy as np

from investigator.text import normalize_text

BITS = 64
MASK = (1 << BITS) - 1
SHINGLE_WORDS = 3
NEAR_DUPLICATE_MAX_DISTANCE = 6


def simhash(text: str) -> int:
    """64-bit SimHash over word 3-grams, returned as a signed int (Postgres bigint)."""
    words = normalize_text(text).lower().split()
    shingles = Counter(
        " ".join(words[i : i + SHINGLE_WORDS])
        for i in range(max(1, len(words) - SHINGLE_WORDS + 1))
    )
    hashes = np.fromiter(
        (
            int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "big")
            for s in shingles
        ),
        dtype=np.uint64,
        count=len(shingles),
    )
    counts = np.fromiter(shingles.values(), dtype=np.int64, count=len(shingles))
    # Every shingle votes on every bit: +count where its hash has a 1, -count where it has a 0.
    # Similar documents share most shingles, so most bit votes come out the same.
    bits = ((hashes[:, None] >> np.arange(BITS, dtype=np.uint64)) & np.uint64(1)).astype(np.int64)
    votes = (counts[:, None] * (2 * bits - 1)).sum(axis=0)  # shape (64,)
    fingerprint = sum(1 << int(bit) for bit in np.flatnonzero(votes > 0))
    return fingerprint - (1 << BITS) if fingerprint >= 1 << (BITS - 1) else fingerprint


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & MASK).bit_count()


def near_duplicate_pairs(
    fingerprints: dict[str, int], max_distance: int = NEAR_DUPLICATE_MAX_DISTANCE
) -> list[tuple[str, str, int]]:
    """All pairs within `max_distance` bits, without comparing every pair with every other.

    Pigeonhole: split the 64 bits into max_distance + 1 blocks. Two fingerprints that differ in
    at most max_distance bits cannot touch every block, so at least one block is identical and
    both land in the bucket (block number, block value). Only members of a bucket are compared,
    a whole bucket at a time with numpy: XOR every pair, count the differing bits.
    """
    keys = list(fingerprints)
    values = np.array([fingerprints[key] & MASK for key in keys], dtype=np.uint64)
    edges = np.linspace(0, BITS, max_distance + 2).astype(int)  # block boundaries, in bits

    pairs: dict[tuple[str, str], int] = {}
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        block = (values >> np.uint64(low)) & np.uint64((1 << (high - low)) - 1)
        order = np.argsort(block, kind="stable")
        bucket_starts = np.flatnonzero(np.diff(block[order])) + 1
        for bucket in np.split(order, bucket_starts):
            if len(bucket) < 2:
                continue
            members = values[bucket]
            distances = np.bitwise_count(members[:, None] ^ members[None, :])
            for i, j in np.argwhere(np.triu(distances <= max_distance, k=1)):
                a, b = sorted((keys[bucket[i]], keys[bucket[j]]))
                pairs[(a, b)] = int(distances[i, j])
    return sorted((a, b, d) for (a, b), d in pairs.items())
