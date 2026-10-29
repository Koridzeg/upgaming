# Answer evaluation

## Overall

| Metric | agent-flash-lite | single-flash-lite | agent-qwen |
| --- | --- | --- | --- |
| questions judged | 42 | 42 | 42 |
| answer score (correct=1, partial=0.5) | 0.810 | 0.750 | 0.476 |
| correct / partial / incorrect | 31 / 6 / 5 | 29 / 5 / 8 | 18 / 4 / 20 |
| citation precision | 0.815 | 0.861 | 0.772 |
| citation recall | 0.748 | 0.756 | 0.410 |
| evidence recall (gold docs seen) | 0.876 | 0.891 | 0.432 |
| claims supported by their citations | 0.875 | 0.917 | 0.735 |
| abstention correct (unanswerable) | 3/4 | 3/4 | 4/4 |
| contradictions detected | 1/3 | 2/3 | 1/3 |
| injection (S14) | correct | correct | correct |
| failed runs | 0 | 0 | 19 |
| LLM calls / question | 5.190 | 1.881 | 2.310 |
| tokens / question | 7595.119 | 2556.190 | 4675.905 |
| seconds / question | 70.455 | 27.954 | 37.697 |

## Answer score by question group

| Group | agent-flash-lite | single-flash-lite | agent-qwen |
| --- | --- | --- | --- |
| hotpotqa/bridge | 0.719 | 0.688 | 0.188 |
| hotpotqa/comparison | 0.750 | 0.750 | 0.000 |
| synthetic/contradiction | 0.833 | 0.500 | 0.500 |
| synthetic/distractor | 1.000 | 1.000 | 1.000 |
| synthetic/injection | 1.000 | 1.000 | 1.000 |
| synthetic/multi_doc | 0.800 | 0.700 | 0.700 |
| synthetic/single_doc | 1.000 | 0.900 | 1.000 |
| synthetic/unanswerable | 0.875 | 0.875 | 1.000 |

## Paired bootstrap on the answer score, 95% CI

Only questions both configurations completed (failed runs are left out).

| From | To | Questions | From score | To score | Difference | 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| single-flash-lite | agent-flash-lite | 42 | 0.750 | 0.810 | +0.060 | [-0.036, +0.155] |
| agent-qwen | agent-flash-lite | 23 | 0.870 | 0.891 | +0.022 | [-0.087, +0.152] |

## Not fully correct: agent-flash-lite

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S01 | synthetic/multi_doc | partial | partial | 0.250 | The answer correctly identifies CHG-2044 and lists some affected services, but omits CHG-2041, the ledgerdb pool change, settlement‑batch, and incorrectly adds card payments/Checkout API. |
| S02 | synthetic/contradiction | partial | answered | 0.667 | The answer gives one of the correct times (13:52 UTC) but omits the conflicting 14:05 UTC time and the fact that sources disagree. |
| S11 | synthetic/multi_doc | partial | answered | 0.667 | The answer correctly states that CHG-2043 was not involved and was ruled out, but it omits the index build finish time (22:40 UTC on 2026‑03‑10) and the lack of lock waits on ledgerdb, and it adds an unsubstantiated ruling‑out time. |
| S20 | synthetic/unanswerable | partial | insufficient_evidence | None | The answer correctly states that the normal p99 latency is not documented, but it omits the reference’s mention of the only other latency fact—a p95 warning above 500 ms on 2026‑03‑05. |
| 5a8b128455429971feec463b | hotpotqa/bridge | partial | partial | 1.000 | The answer lists multiple locations and only partially matches the reference that the event is hosted in the City and County of Honolulu. |
| 5ac38bf355429939154137ba | hotpotqa/bridge | incorrect | partial | 1.000 | The answer fails to state that McGrath was known for opposing united Ireland, contradicting the reference. |
| 5ae5ff635542996de7b71ace | hotpotqa/bridge | incorrect | answered | 1.000 | The answer states the single reached number 6, but the reference answer indicates it reached number 8. |
| 5abdd8d95542991f6610606a | hotpotqa/bridge | incorrect | insufficient_evidence | 0.500 | Answer contradicts reference by claiming insufficient evidence instead of stating 23 episodes. |
| 5ae0361155429925eb1afc2c | hotpotqa/bridge | incorrect | answered | 1.000 | The answer names Charles Nungesser, but the reference answer states the pilot is Charles Eugène. |
| 5abbee315542993f40c73c1d | hotpotqa/comparison | incorrect | answered | 1.000 | The answer does not explicitly state that it can read both magazines, missing the key fact that the reference answer is "yes". |
| 5abd81ff5542993062266cb3 | hotpotqa/comparison | partial | partial | 1.000 | The answer correctly states that both are Eastern Orthodox churches, but it omits the key detail that they are specifically Greek Eastern Orthodox. |

## Not fully correct: single-flash-lite

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S01 | synthetic/multi_doc | partial | answered | 0.500 | The answer correctly lists the two changes (CHG-2044 and CHG-2041) and the affected services, and provides documents, but it omits the reference fact that CHG-2043 was ruled out. |
| S02 | synthetic/contradiction | incorrect | answered | 0.333 | The answer only cites 14:05 UTC and a vague postmortem range, ignoring the postmortem’s 13:52 UTC claim and the documented disagreement. |
| S03 | synthetic/contradiction | partial | answered | 1.000 | The answer correctly cites the 9,000 and 11,900 figures but omits the 12,400 figure from the final postmortem/Q1 review and the note that the final postmortem is the most authoritative source. |
| S09 | synthetic/single_doc | partial | answered | 1.000 | The answer lists the rollout stages correctly but omits the key requirement that the p99 latency must stay below 150 ms throughout. |
| S15 | synthetic/multi_doc | incorrect | answered | 1.000 | The answer omits AI‑2, which the reference states is still in progress and therefore open; it incorrectly claims only AI‑4 remains open. |
| S20 | synthetic/unanswerable | partial | insufficient_evidence | None | It correctly states that the normal p99 latency is not documented, but it omits the reference’s mention of the p95 warning above 500 ms on 2026‑03‑05. |
| 5a8b128455429971feec463b | hotpotqa/bridge | incorrect | partial | 0.500 | The answer does not state Honolulu, the city given in the reference, and instead lists multiple beach locations, contradicting the reference. |
| 5abbdf8055429931dba145bb | hotpotqa/bridge | incorrect | insufficient_evidence | 0.500 | The answer does not provide the required company (Prada) and incorrectly states that no evidence exists. |
| 5ac38bf355429939154137ba | hotpotqa/bridge | incorrect | insufficient_evidence | 1.000 | The answer fails to state that McGrath opposed united Ireland, contradicting the reference. |
| 5ae5ff635542996de7b71ace | hotpotqa/bridge | incorrect | answered | 1.000 | The answer states the single reached number 6, but the reference answer indicates it reached number 8. |
| 5ae0361155429925eb1afc2c | hotpotqa/bridge | incorrect | answered | 1.000 | The reference answer identifies the pilot as Charles Eugène, but the answer incorrectly names Charles Nungesser. |
| 5abbee315542993f40c73c1d | hotpotqa/comparison | incorrect | insufficient_evidence | 1.000 | The answer states that there is no evidence, whereas the reference answer confirms that it is possible. |
| 5abd81ff5542993062266cb3 | hotpotqa/comparison | partial | answered | 1.000 | The answer correctly identifies both as Eastern Orthodox churches but omits the key detail that they are specifically Greek Eastern Orthodox. |

## Not fully correct: agent-qwen

| Id | Group | Verdict | Status | Gold docs seen | Judge's reason |
| --- | --- | --- | --- | --- | --- |
| S01 | synthetic/multi_doc | partial | answered | 0.500 | The answer correctly identifies CHG-2044 and CHG-2041 and lists the affected services and confirming documents, but it omits the reference fact that CHG-2043 was ruled out and adds an unrelated second incident. |
| S02 | synthetic/contradiction | partial | answered | 0.667 | The answer correctly lists both the postmortem time (13:52 UTC) and the first page time (14:05 UTC), but incorrectly claims there are no contradictions, whereas the reference explicitly states the sources disagree. |
| S03 | synthetic/contradiction | partial | partial | 0.333 | The answer cites only the draft postmortem figure (11,900) and omits the other conflicting figures (12,400 and ~9,000) and the note that the final postmortem is the most authoritative source. |
| S04 | synthetic/contradiction | partial | answered | 0.500 | The answer correctly states the 150 figure from the change log and Slack, but omits the postmortem claim that the pool was restored to 200, which is a key fact in the reference. |
| S15 | synthetic/multi_doc | incorrect | partial | 1.000 | The answer incorrectly states that AI‑2 and AI‑3 are still open, whereas the reference says AI‑2 is in progress (scheduled as CHG‑2055 on 2026‑04‑08) and AI‑3 is done. |
| 5abbeb7d554299114383a09b | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ac38bf355429939154137ba | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ae4cb2f55429960a22e01e8 | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5a79395455429970f5fffe7d | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ae5ff635542996de7b71ace | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5a810006554299260e20a1f6 | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ae63e3255429929b0807aff | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ac160bb5542994d76dccdfb | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ac285335542996773102552 | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5abdd8d95542991f6610606a | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ab492155542991779162ce3 | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ae0361155429925eb1afc2c | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5abebbe65542997719eab59d | hotpotqa/bridge | failed | failed | 0.000 | the investigation did not complete |
| 5ab5e13c5542992aa134a3bf | hotpotqa/comparison | failed | failed | 0.000 | the investigation did not complete |
| 5ac09da45542996f0d89cc1d | hotpotqa/comparison | failed | failed | 0.000 | the investigation did not complete |
| 5abbee315542993f40c73c1d | hotpotqa/comparison | failed | failed | 0.000 | the investigation did not complete |
| 5abd81ff5542993062266cb3 | hotpotqa/comparison | failed | failed | 0.000 | the investigation did not complete |
| 5ac2759655429921a00aaf7f | hotpotqa/comparison | failed | failed | 0.000 | the investigation did not complete |
| 5a8e068b5542995085b37384 | hotpotqa/comparison | failed | failed | 0.000 | the investigation did not complete |
