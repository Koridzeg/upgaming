---
title: Q1 2026 Reliability Review
author: Hannah Lindqvist
date: 2026-04-06
doc_type: review
status: final
render: docx
---

# Q1 2026 Reliability Review

Author: Hannah Lindqvist (VP Engineering)
Date: 2026-04-06

## Incidents in Q1 2026

| Incident | Date | Severity | Summary |
| --- | --- | --- | --- |
| INC-2026-0217 | 2026-02-17 | SEV-3 | Slow card authorisations caused by the acquiring partner NetAcq |
| INC-2026-0311 | 2026-03-11 | SEV-2 | Card payment delays: fraud model v3.3 latency combined with a reduced gateway connection pool |
| INC-2026-0324 | 2026-03-24 | SEV-3 | Notification delays after the Kafka broker upgrade |

## INC-2026-0311 follow-up

INC-2026-0311 delayed 12,400 card payments, the largest impact this quarter. The gateway connection pool was raised to 150 during the incident (CHG-2048) and has stayed at 150.

Action item status as of 2026-04-06:

- AI-1, pool saturation alert: Done.
- AI-2, release the ledgerdb connection before calling fraud-scoring: In progress, scheduled for release as CHG-2055 on 2026-04-08.
- AI-3, enforce the fraud model canary stage: Done on 2026-03-30.
- AI-4, named approver for gateway configuration changes: Open.

## Availability

- Checkout API availability in Q1 2026 was 99.93%, against a target of 99.95%.
- Two of the three incidents were caused by internal changes.
