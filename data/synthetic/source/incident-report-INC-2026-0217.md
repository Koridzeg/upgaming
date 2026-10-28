---
title: Incident Report INC-2026-0217 - Card authorisation latency
author: Lea Brandt
date: 2026-02-17
doc_type: incident_report
status: final
render: pdf
---

# Incident Report INC-2026-0217: Card authorisation latency

Prepared by: Lea Brandt (Incident Commander)
Date: 2026-02-17
Status: Final

## Summary

On 2026-02-17 between 09:10 and 10:25 UTC, card authorisations were slow because our acquiring partner NetAcq had a degradation in its authorisation service. Kestrel Pay systems were healthy; payment-gateway was waiting on NetAcq responses.

## Impact

- Severity: SEV-3
- About 2,300 card payments took longer than 10 seconds.
- No payments failed; the merchant-api timeout of 30 seconds was not reached.

## Affected services

- payment-gateway: slow responses from the upstream dependency NetAcq
- merchant-api: slower Checkout API responses

## Root cause

External: a degradation of the NetAcq authorisation service, confirmed by the NetAcq status page. No Kestrel Pay change was involved.

## Follow-up

- Evaluate a secondary acquiring partner for failover. Owner: Lea Brandt.
