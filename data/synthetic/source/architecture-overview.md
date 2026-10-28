---
title: Kestrel Pay Platform Architecture Overview
author: Hannah Lindqvist
date: 2026-01-20
doc_type: architecture
status: current
render: docx
---

# Kestrel Pay Platform Architecture Overview

Owner: Hannah Lindqvist (VP Engineering)
Last updated: 2026-01-20

## Payment request path

1. A merchant calls merchant-api, the public Checkout API. merchant-api checks the merchant's API key with auth-service.
2. merchant-api forwards the payment to payment-gateway and waits up to 30 seconds for a response.
3. payment-gateway takes a connection from its ledgerdb pool, calls fraud-scoring synchronously (timeout 2 seconds), sends the authorisation to the acquiring partner NetAcq, and records the result through ledger-service.
4. On success, payment-gateway publishes a payment.completed event to Kafka.
5. notification-service consumes payment.completed events and sends receipts and merchant webhooks.
6. settlement-batch runs nightly at 02:00 UTC and settles the previous day's payments with partner banks.

## Services

| Service | Owner team | Responsibility |
| --- | --- | --- |
| merchant-api | Checkout | Public REST API for merchants |
| auth-service | Identity | Merchant API key authentication |
| payment-gateway | Payments Platform | Card authorisation; owns the ledgerdb connection pool |
| fraud-scoring | Risk ML | Real-time fraud risk model served over gRPC |
| ledger-service | Ledger | Double-entry ledger on ledgerdb (PostgreSQL) |
| notification-service | Messaging | Receipts and merchant webhooks from Kafka |
| settlement-batch | Ledger | Nightly settlement with partner banks |

## Known technical debt

- TD-112: payment-gateway holds its ledgerdb connection for the whole request, including the synchronous fraud-scoring call. Slow fraud-scoring responses therefore increase the number of connections in use. Owner: Payments Platform.
- TD-118: notification-service has no back-pressure. A slow Kafka consumer builds a backlog instead of rejecting work.

## Capacity (January 2026)

- Peak traffic: about 320 card payments per second, between 13:00 and 17:00 UTC on weekdays.
- ledgerdb max_connections: 400, shared by payment-gateway and ledger-service.
- payment-gateway ledgerdb pool max_size: 200.
