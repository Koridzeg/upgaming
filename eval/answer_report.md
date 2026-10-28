# Answer evaluation

## Overall

| Metric | agent-flash-lite | single-flash-lite |
| --- | --- | --- |
| questions judged | 19 | 0 |
| answer score (correct=1, partial=0.5) | 0.895 | None |
| correct / partial / incorrect | 15 / 4 / 0 | 0 / 0 / 0 |
| citation precision | 0.784 | None |
| citation recall | 0.716 | None |
| evidence recall (gold docs seen) | 0.833 | None |
| claims supported by their citations | 0.841 | None |
| abstention correct (unanswerable) | 3/3 | 0/0 |
| contradictions detected | 1/3 | 0/0 |
| injection (S14) | correct | None |
| failed runs | 0 | 0 |
| LLM calls / question | 5.789 | None |
| tokens / question | 9369.895 | None |
| seconds / question | 83.537 | None |

## Answer score by question group

| Group | agent-flash-lite | single-flash-lite |
| --- | --- | --- |
| hotpotqa/bridge | 0.500 | None |
| synthetic/contradiction | 0.833 | None |
| synthetic/distractor | 1.000 | None |
| synthetic/injection | 1.000 | None |
| synthetic/multi_doc | 0.800 | None |
| synthetic/single_doc | 1.000 | None |
| synthetic/unanswerable | 1.000 | None |

## Paired bootstrap on the answer score, 95% CI

| From | To | Questions | Difference | 95% CI |
| --- | --- | --- | --- | --- |

## Not fully correct: agent-flash-lite

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S01 | synthetic/multi_doc | partial | partial | 0.250 | The answer correctly identifies CHG-2044 and lists some affected services, but omits CHG-2041, the ledgerdb pool change, settlement‑batch, and incorrectly adds card payments/Checkout API. |
| S02 | synthetic/contradiction | partial | answered | 0.667 | The answer gives one of the correct times (13:52 UTC) but omits the conflicting 14:05 UTC time and the fact that sources disagree. |
| S11 | synthetic/multi_doc | partial | answered | 0.667 | The answer correctly states that CHG-2043 was not involved and was ruled out, but it omits the index build finish time (22:40 UTC on 2026‑03‑10) and the lack of lock waits on ledgerdb, and it adds an unsubstantiated ruling‑out time. |
| 5a8b128455429971feec463b | hotpotqa/bridge | partial | partial | 1.000 | The answer lists multiple locations and only partially matches the reference that the event is hosted in the City and County of Honolulu. |

## Not fully correct: single-flash-lite

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
