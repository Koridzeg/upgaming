---
title: Runbook - payment-gateway
author: Priya Nair
date: 2026-02-02
doc_type: runbook
status: current
---

# Runbook: payment-gateway

payment-gateway authorises card payments. It is called by merchant-api and depends on ledgerdb (through its connection pool), fraud-scoring and the acquiring partner NetAcq.

## Connection pool sizing

payment-gateway keeps a pool of connections to ledgerdb. Each request holds one connection from start to finish, including the synchronous fraud-scoring call (technical debt TD-112).

Connections needed at peak = peak requests per second x average time a request holds a connection.

- Default max_size: 200.
- Do not reduce max_size below 150 without a load test at peak traffic that includes fraud-scoring latency at its p99.
- When the pool is exhausted, requests wait for a free connection. merchant-api gives up after 30 seconds and returns HTTP 504 to the merchant.

## Alerts

- payment-gateway p95 latency above 2 s for 5 minutes: warning.
- merchant-api 5xx rate above 2%: critical, pages on-call.

## Common failure modes

| Symptom | Likely cause | First action |
| --- | --- | --- |
| Requests queue and the pool is at max_size | A slow dependency is holding connections | Check fraud-scoring and ledgerdb latency |
| Authorisations are slow but the pool is not saturated | Acquiring partner NetAcq is degraded | Check the NetAcq status page |
| Errors right after a deploy | Bad release | Roll back with `deployctl rollback payment-gateway` |
