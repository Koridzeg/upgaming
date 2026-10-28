# Synthetic incident corpus

A small, hand-written document set for a fictional payment processor, **Kestrel Pay**. All
companies, people, systems and events here are fictional.

It complements HotpotQA with what HotpotQA lacks: several file formats with real metadata,
contradictions between sources, deliberately missing facts, a superseded draft, an exact
duplicate file, decoy incidents, and a prompt-injection document.

- `source/` — what we write. Markdown files whose front matter says `render: pdf` or
  `render: docx` are converted to that format; everything else is already final.
- `corpus/` — the generated documents (`python -m investigator.datasets.synthetic`). These are
  the files you upload to the app.
- `questions.json` — evaluation questions with expected answers and sources.

## Planted test cases

| Case | Where |
| --- | --- |
| Impact start: 13:52 vs 14:05 UTC | postmortem vs incident report and status page |
| Delayed payments: 12,400 vs ~9,000 vs ~11,900 | postmortem vs incident report vs draft |
| Pool raised to 200 vs 150 | postmortem vs change log, Slack, Q1 review |
| Missing approver for CHG-2041 | change log (`approver: null`) |
| Red herring: ledger index migration CHG-2043 | Slack (suspected, then ruled out), postmortem |
| Decoy incidents | INC-2026-0217 (February), INC-2026-0324 (notifications only) |
| Superseded draft | `postmortem-INC-2026-0311-draft.docx` |
| Exact duplicate file | `postmortem-INC-2026-0311 (copy).pdf` |
| Prompt injection | `vendor-advisory-orchard-sdk.html` (visible and hidden instructions) |
