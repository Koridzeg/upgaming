---
title: Postmortem INC-2026-0311 (DRAFT)
author: Priya Nair
date: 2026-03-14
doc_type: postmortem
status: draft
render: docx
---

# DRAFT Postmortem: INC-2026-0311 Card payment processing delays

Author: Priya Nair
Date: 2026-03-14
Status: Draft for review. Numbers are preliminary.

## Summary

On 2026-03-11 card payments were delayed or failed between roughly 14:00 and 16:40 UTC. The root cause is still under investigation. The leading hypothesis is the fraud-scoring latency increase after the fraud model v3.3 rollout (CHG-2044).

## Impact (preliminary)

- Approximately 11,900 card payments delayed by more than 30 seconds.
- Around 1,100 payments failed with timeouts.
- Receipt notifications were delayed.

## Affected services

- merchant-api
- payment-gateway
- fraud-scoring
- notification-service

## Open questions

- Did the connection pool reduction in CHG-2041 contribute?
- Was the ledger index migration CHG-2043 involved? Initial checks during the incident suggest not.
- Why did the v3.3 rollout skip the canary stage?
