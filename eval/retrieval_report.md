# Retrieval evaluation

HotpotQA: 150 tune / 150 test questions, seeded and stratified by type. Synthetic corpus: 17 answerable questions.
Selected on the tune split by all@10: **hybrid, keyword weight 1.0, title boost 0.6, MMR lambda 1.0**.

## Test split

| Configuration | recall@5 | recall@10 | all@10 | all@20 | mrr | ndcg@10 | sentence_recall@10 | tokens@10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dense only / structural | 0.753 | 0.827 | 0.687 | 0.760 | 0.900 | 0.785 | 0.817 | 891 |
| BM25 only / structural | 0.740 | 0.810 | 0.627 | 0.707 | 0.895 | 0.751 | 0.806 | 828 |
| hybrid RRF / structural | 0.800 | 0.870 | 0.740 | 0.800 | 0.925 | 0.815 | 0.867 | 871 |
| hybrid + re-rank (initial guess) / structural | 0.797 | 0.860 | 0.720 | 0.800 | 0.943 | 0.816 | 0.856 | 867 |
| hybrid tuned / structural | 0.797 | 0.873 | 0.747 | 0.800 | 0.936 | 0.817 | 0.869 | 871 |
| dense only / fixed | 0.750 | 0.813 | 0.667 | 0.740 | 0.873 | 0.758 | 0.777 | 872 |
| BM25 only / fixed | 0.740 | 0.810 | 0.627 | 0.700 | 0.903 | 0.754 | 0.798 | 897 |
| hybrid RRF / fixed | 0.773 | 0.857 | 0.713 | 0.807 | 0.919 | 0.794 | 0.836 | 871 |
| hybrid + re-rank (initial guess) / fixed | 0.780 | 0.860 | 0.720 | 0.793 | 0.938 | 0.807 | 0.836 | 867 |
| hybrid tuned / fixed | 0.797 | 0.867 | 0.733 | 0.813 | 0.931 | 0.809 | 0.846 | 874 |

## Paired bootstrap, 95% confidence intervals

| From | To | Metric | Difference | 95% CI |
| --- | --- | --- | --- | --- |
| dense only / structural | hybrid tuned / structural | all@10 | +0.060 | [+0.000, +0.127] |
| BM25 only / structural | hybrid tuned / structural | all@10 | +0.120 | [+0.067, +0.180] |
| hybrid RRF / structural | hybrid tuned / structural | all@10 | +0.007 | [-0.013, +0.033] |
| hybrid tuned / fixed | hybrid tuned / structural | all@10 | +0.013 | [+0.000, +0.033] |
| hybrid tuned / fixed | hybrid tuned / structural | sentence_recall@10 | +0.023 | [+0.009, +0.041] |
| hybrid tuned / fixed | hybrid tuned / structural | tokens@10 | -3.840 | [-14.900, +7.387] |

## Failure analysis (tuned setting, structural chunks, test split)

- all@10 by question type: {'bridge': 0.683, 'comparison': 1.0} (questions: {'bridge': 120, 'comparison': 30})
- Gold documents missing from the top 10: 38
- ... never retrieved by either retriever (not in the top 50): 40%
- ... whose title the question names: 0%

| Question | Type | Missed document | Named in question | In candidates |
| --- | --- | --- | --- | --- |
| Which team does the player named 2015 Diamond Head Classic's MVP play for? | bridge | Buddy Hield | False | True |
| Who has more victories Chelsea or Manchester United? | bridge | Wayne Rooney | False | True |
| Whitford Lodge once held a branch of the bank that later became which national bank? | bridge | TD Bank, N.A. | False | False |
| Aside from the Apple Remote, what other device can control the program Apple Remote was originally designed to interact with? | bridge | Front Row (software) | False | True |
| Who published the film magazine that Peter Biskind was executive editor of from 1986 to 1996? | bridge | Premiere (magazine) | False | True |
| Whose former city palace did Erna Flegel work at? | bridge | Reich Chancellery | False | True |
| How many records did the boy band who released "Said and Done" sell worldwide as of 2013? | bridge | Boyzone | False | False |
| Who does the current PFA Young Player of the Year currently play for? | bridge | Dele Alli | False | False |

## Synthetic corpus (structural chunks)

| Configuration | recall@10 | all@10 | mrr |
| --- | --- | --- | --- |
| hybrid RRF | 0.838 | 0.647 | 0.717 |
| hybrid tuned | 0.838 | 0.647 | 0.717 |
