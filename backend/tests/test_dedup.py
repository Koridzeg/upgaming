from investigator.ingestion.dedup import (
    NEAR_DUPLICATE_MAX_DISTANCE,
    hamming,
    near_duplicate_pairs,
    simhash,
)

TEXT = (
    "Between 13:52 and 16:40 UTC on 2026-03-11, card payments were delayed or failed. Two "
    "changes combined to cause the incident. CHG-2044 rolled out fraud model v3.3, which raised "
    "fraud-scoring p99 latency from about 80 ms to about 450 ms. CHG-2041, made two days "
    "earlier, had reduced the payment-gateway database connection pool from 200 to 60 "
    "connections. Neither change caused problems on its own. payment-gateway holds a ledgerdb "
    "connection while it waits for the synchronous fraud-scoring call, so the number of "
    "connections in use is roughly the request rate multiplied by the time each request holds "
    "a connection. At the afternoon peak of about 320 payments per second the gateway needed "
    "about 144 connections, and requests queued until merchant-api timed out after 30 seconds."
)
OTHER = (
    "Shadow mode runs for 24 hours. The canary then takes 5% of traffic for one hour, followed "
    "by 25% for four hours including a weekday peak, and finally all traffic."
)


def test_formatting_differences_give_the_same_fingerprint():
    reformatted = TEXT.upper().replace(" ", "\xa0", 10).replace(". ", ".\n\n")

    assert simhash(reformatted) == simhash(TEXT)


def test_a_one_word_edit_is_a_near_duplicate():
    edited = TEXT.replace("144 connections", "150 connections")

    # 4 bits for this ~150-word text: the classic 3-bit threshold would have missed it.
    assert 0 < hamming(simhash(TEXT), simhash(edited)) <= NEAR_DUPLICATE_MAX_DISTANCE


def test_unrelated_texts_are_far_apart():
    assert hamming(simhash(TEXT), simhash(OTHER)) > 10


def test_fingerprint_fits_a_postgres_bigint():
    for text in (TEXT, OTHER, ""):
        assert -(2**63) <= simhash(text) < 2**63


def test_pairs_are_found_through_shared_blocks():
    base = simhash(TEXT)
    spread_6 = sum(1 << bit for bit in (0, 10, 20, 30, 40, 50))  # touches 6 different blocks
    fingerprints = {
        "a": base,
        "b": base ^ 0b101,  # 2 bits differ
        "c": base ^ spread_6,  # 6 bits differ: the limit, still found
        "d": base ^ spread_6 ^ (1 << 60),  # 7 bits from a: too far
        "e": base ^ 0xFFFF_0000_FFFF,  # 32 bits differ
    }

    pairs = near_duplicate_pairs(fingerprints)

    assert ("a", "b", 2) in pairs and ("a", "c", 6) in pairs
    assert not any({"a", "d"} == {x, y} or "e" in (x, y) for x, y, _ in pairs)
