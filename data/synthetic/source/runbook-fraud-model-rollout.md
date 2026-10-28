---
title: Runbook - Fraud model rollout
author: Daniel Okafor
date: 2025-11-10
doc_type: runbook
status: current
---

# Runbook: Fraud model rollout

Every new fraud-scoring model version must go through these stages, in order:

1. Shadow mode for 24 hours: the new model scores live traffic, but its scores are not used.
2. Canary: 5% of traffic for 1 hour.
3. 25% of traffic for 4 hours, including at least one weekday peak period (13:00 to 17:00 UTC).
4. 100% of traffic.

## Latency budget

fraud-scoring p99 latency must stay below 150 ms at every stage. payment-gateway calls fraud-scoring synchronously, so fraud-scoring latency adds directly to payment latency.

If p99 exceeds 150 ms at any stage, roll back to the previous model version.

## Rollback

Run `modelctl rollback fraud-scoring`. A rollback takes about 5 minutes to reach all replicas.
