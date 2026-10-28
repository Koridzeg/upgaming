---
title: Incident Report INC-2026-0324 - Delayed payment notifications
author: Samir Haddad
date: 2026-03-24
doc_type: incident_report
status: final
render: pdf
---

# Incident Report INC-2026-0324: Delayed payment notifications

Prepared by: Samir Haddad (Messaging team)
Date: 2026-03-24
Status: Final

## Summary

From 22:10 UTC on 2026-03-23 to 01:15 UTC on 2026-03-24, payment receipt emails and merchant webhooks were delayed by up to 3 hours. The cause was a consumer group rebalancing loop in notification-service after the Kafka broker upgrade CHG-2051.

## Impact

- Severity: SEV-3
- About 41,000 notifications were delayed. None were lost.
- Card payments were processed normally. payment-gateway and merchant-api were not affected.

## Affected services

- notification-service

## Root cause

CHG-2051 upgraded the Kafka brokers from version 3.6 to 3.7. The notification-service consumers used a session timeout shorter than the new broker's rebalance delay, so they were repeatedly removed from the consumer group and processing stalled.

## Resolution

The consumer session timeout was raised from 10 to 45 seconds at 00:40 UTC. The backlog had drained by 01:15 UTC.
