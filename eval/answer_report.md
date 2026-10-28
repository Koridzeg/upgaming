# Answer evaluation

## Overall

| Metric | agent-120b | single-120b | agent-20b |
| --- | --- | --- | --- |
| questions judged | 2 | 2 | 2 |
| answer score (correct=1, partial=0.5) | 0.500 | 0.750 | 0.250 |
| correct / partial / incorrect | 0 / 2 / 0 | 1 / 1 / 0 | 0 / 1 / 1 |
| citation precision | 0.750 | 0.417 | 0.750 |
| citation recall | 0.375 | 0.292 | 0.375 |
| evidence recall (gold docs seen) | 0.708 | 0.417 | 0.708 |
| claims supported by their citations | 1.000 | 1.000 | 0.375 |
| abstention correct (unanswerable) | 0/0 | 0/0 | 0/0 |
| contradictions detected | 0/1 | 0/1 | 0/1 |
| injection (S14) | None | None | None |
| failed runs | 0 | 0 | 1 |
| LLM calls / question | 4 | 2 | 4.500 |
| tokens / question | 10378.500 | 4118.500 | 10816.500 |
| seconds / question | 50.700 | 28.493 | 82.230 |

## Answer score by question group

| Group | agent-120b | single-120b | agent-20b |
| --- | --- | --- | --- |
| synthetic/contradiction | 0.500 | 0.500 | 0.000 |
| synthetic/multi_doc | 0.500 | 1.000 | 0.500 |

## Paired bootstrap on the answer score, 95% CI

| From | To | Questions | Difference | 95% CI |
| --- | --- | --- | --- | --- |
| single-120b | agent-120b | 2 | -0.250 | [-0.500, +0.000] |
| agent-20b | agent-120b | 2 | +0.250 | [+0.000, +0.500] |

## Not fully correct: agent-120b

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S01 | synthetic/multi_doc | partial | answered | 0.750 | The answer correctly identifies the root cause changes (CHG-2044 and CHG-2041) and the documents, but it fails to list the specific affected services (merchant-api, payment-gateway, fraud-scoring, notification-service, settlement-batch) required by the reference, instead naming only 'Card Payments service' and 'Checkout API'. |
| S02 | synthetic/contradiction | partial | answered | 0.667 | The answer correctly identifies one of the conflicting timestamps (13:52 UTC) but fails to report the contradiction with the other sources (14:05 UTC) as required by the reference. |

## Not fully correct: single-120b

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S02 | synthetic/contradiction | partial | answered | 0.333 | The answer provides one of the conflicting timestamps (14:05 UTC) but fails to mention the discrepancy with the postmortem (13:52 UTC) or that sources disagree, which is a key requirement of the reference answer. |

## Not fully correct: agent-20b

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S01 | synthetic/multi_doc | partial | answered | 0.750 | The answer correctly identifies the two system changes (CHG-2044 and CHG-2041) and the supporting documents, but it omits two of the five affected services listed in the reference (notification-service and settlement-batch). |
| S02 | synthetic/contradiction | failed | failed | 0.667 | the investigation did not complete |
