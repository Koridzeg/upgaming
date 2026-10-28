---
title: Postmortem INC-2026-0311 - Card payment processing delays
author: Priya Nair, Daniel Okafor
date: 2026-03-18
doc_type: postmortem
status: final
render: pdf
---

# Postmortem: INC-2026-0311 Card payment processing delays

Authors: Priya Nair (Payments Platform), Daniel Okafor (Risk ML)
Reviewed by: Hannah Lindqvist (VP Engineering)
Date: 2026-03-18
Status: Final. Supersedes the draft of 2026-03-14.

## Summary

Between 13:52 and 16:40 UTC on 2026-03-11, card payments were delayed or failed. Two changes combined to cause the incident. CHG-2044 rolled out fraud model v3.3, which raised fraud-scoring p99 latency from about 80 ms to about 450 ms. CHG-2041, made two days earlier, had reduced the payment-gateway database connection pool from 200 to 60 connections. Neither change caused problems on its own.

## Impact

- Impact window: 13:52 to 16:40 UTC (2 hours 48 minutes). Impact started 13 minutes before the first page at 14:05.
- 12,400 card payments were delayed by more than 30 seconds.
- 1,140 payments failed with timeout errors.
- notification-service built up a backlog of about 30,000 receipt events. Receipts and merchant webhooks were delayed by up to 2 hours.
- The settlement-batch run scheduled for 02:00 UTC on 2026-03-12 started 47 minutes late while ledger reconciliation of retried payments completed.

## Affected services

| Service | Effect |
| --- | --- |
| merchant-api | Checkout requests timed out after 30 seconds (HTTP 504) |
| payment-gateway | Requests queued waiting for a ledgerdb connection |
| fraud-scoring | p99 latency rose to about 450 ms after CHG-2044 |
| notification-service | Receipt and webhook backlog, delayed up to 2 hours |
| settlement-batch | Nightly run started 47 minutes late |

## Root cause

payment-gateway holds a ledgerdb connection while it waits for the synchronous fraud-scoring call (known technical debt TD-112). The number of connections in use is therefore roughly the request rate multiplied by the time each request holds a connection.

At the afternoon peak of about 320 payments per second, with fraud-scoring at 450 ms, the gateway needed about 320 x 0.45 = 144 connections. The pool allowed 60 after CHG-2041, so requests queued until merchant-api timed out after 30 seconds.

Before CHG-2044, the same traffic needed about 320 x 0.08 = 26 connections, which fit within 60. With the original pool of 200, 144 connections would also have fit. The failure required both changes.

The v3.3 rollout went to 100% of traffic at 09:30 UTC without the canary stage required by the fraud model rollout runbook, so the latency regression was not caught before the afternoon peak.

## What was ruled out

- CHG-2043, the ledger-service index migration on 2026-03-10, was investigated and ruled out. The index build finished at 22:40 UTC the previous day and no lock waits were observed on ledgerdb.
- No external dependency or card network issue was involved.

## Detection

The first page was the merchant-api 5xx alert at 14:05 UTC. A fraud-scoring latency warning fired at 13:49 UTC but did not page. There was no alert on connection pool saturation, which explains the 13-minute detection lag.

## Resolution

- 15:20 UTC: fraud model rolled back to v3.2 (CHG-2047). fraud-scoring p99 returned to about 85 ms.
- 16:35 UTC: payment-gateway connection pool restored to 200 (CHG-2048).
- 16:40 UTC: incident resolved.

## Action items

| ID | Action | Owner | Due | Status |
| --- | --- | --- | --- | --- |
| AI-1 | Alert when the gateway pool is above 90% utilised for 2 minutes | Marco Silva | 2026-03-25 | Done |
| AI-2 | Release the ledgerdb connection before calling fraud-scoring (fix TD-112) | Priya Nair | 2026-04-15 | Open |
| AI-3 | Enforce the canary stage for fraud model rollouts in the deploy pipeline | Daniel Okafor | 2026-03-31 | Open |
| AI-4 | Require a named approver for payment-gateway configuration changes; CHG-2041 has no approver on record | Lea Brandt | 2026-04-30 | Open |
