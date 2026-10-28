---
title: Incident Report INC-2026-0311 - Card payment processing delays
author: Marco Silva
date: 2026-03-11
doc_type: incident_report
status: initial
render: pdf
---

# Incident Report INC-2026-0311: Card payment processing delays

Prepared by: Marco Silva (Incident Commander)
Date: 2026-03-11, 18:00 UTC
Status: Initial report. A full postmortem will follow.

## Summary

On 2026-03-11 card payments submitted through the Checkout API were delayed and some failed with timeout errors. The incident started at 14:05 UTC and was resolved at 16:40 UTC. Fraud-scoring latency was elevated after the fraud model v3.3 rollout earlier that day. The model was rolled back and the payment-gateway database connection pool was enlarged.

## Severity and impact

- Severity: SEV-2
- Start: 14:05 UTC (first page)
- Mitigated: 15:20 UTC (fraud model rolled back)
- Resolved: 16:40 UTC
- Duration: 2 hours 35 minutes
- Approximately 9,000 card payments were delayed by more than 30 seconds.
- About 1,100 payments failed with timeout errors and had to be retried by merchants.

## Affected services

- merchant-api: the Checkout API returned HTTP 504 timeouts
- payment-gateway: requests queued waiting for database connections
- fraud-scoring: p99 latency above 400 ms

## Timeline (UTC)

| Time | Event |
| --- | --- |
| 14:05 | Paged: merchant-api 5xx rate above 2% |
| 14:07 | Incident declared, Marco Silva incident commander |
| 14:31 | Ledger index migration CHG-2043 ruled out |
| 14:48 | Fraud-scoring p99 latency identified as elevated since the v3.3 rollout |
| 15:02 | Decision to roll back the fraud model |
| 15:20 | Fraud model rolled back to v3.2 (CHG-2047) |
| 16:35 | payment-gateway connection pool raised (CHG-2048) |
| 16:40 | All metrics normal, incident resolved |

## Next steps

- Postmortem owner: Priya Nair.
- Root cause analysis, including the role of the connection pool change CHG-2041, will be covered in the postmortem.
