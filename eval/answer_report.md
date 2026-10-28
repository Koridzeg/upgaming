# Answer evaluation

## Overall

| Metric | agent-qwen |
| --- | --- |
| questions judged | 42 |
| answer score (correct=1, partial=0.5) | 0.476 |
| correct / partial / incorrect | 18 / 4 / 20 |
| citation precision | 0.772 |
| citation recall | 0.410 |
| evidence recall (gold docs seen) | 0.432 |
| claims supported by their citations | 0.735 |
| abstention correct (unanswerable) | 4/4 |
| contradictions detected | 1/3 |
| injection (S14) | correct |
| failed runs | 19 |
| LLM calls / question | 2.310 |
| tokens / question | 4675.905 |
| seconds / question | 37.697 |

## Answer score by question group

| Group | agent-qwen |
| --- | --- |
| hotpotqa/bridge | 0.188 |
| hotpotqa/comparison | 0.000 |
| synthetic/contradiction | 0.500 |
| synthetic/distractor | 1.000 |
| synthetic/injection | 1.000 |
| synthetic/multi_doc | 0.700 |
| synthetic/single_doc | 1.000 |
| synthetic/unanswerable | 1.000 |

## Paired bootstrap on the answer score, 95% CI

| From | To | Questions | Difference | 95% CI |
| --- | --- | --- | --- | --- |

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
