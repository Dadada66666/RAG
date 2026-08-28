# ADR-005: Durable Single-Host Job Execution with Long-Lived Workers

## Status

Proposed — 2026-08-28

## Context

MVP needs API and CLI, crash resume, cancellation, retries and one GPU. Celery/Dramatiq/RQ/Arq add broker operations; spawning a parser process/model per document is prohibitively slow. Future deployment needs multiple workers/GPUs.

## Decision

Use SQLite-backed durable work/state records, fenced leases and page/scope checkpoints. A small scheduler dispatches bounded units to separate long-lived CPU/GPU worker processes/containers. Execution is at least once; immutable artifacts, idempotency and conditional commits provide exactly-once visible effects. Models load once per ready worker.

State truth is the metadata repository. MVP may use bounded in-process IPC for wakeups, but work is recoverable from SQLite after restart.

## Alternatives

### Celery + Redis/RabbitMQ

Mature but too much MVP operations/configuration and duplicated result state. V1 candidate if measured need.

### Dramatiq

Cleaner actor model and preferred future external-broker candidate, but still requires Redis/RabbitMQ and custom GPU placement/state truth.

### RQ or Arq

Simple Redis/async models but weaker fit for resource-class placement and no inherent benefit for GPU-bound work.

### Synchronous API/CLI only

Cannot robustly resume/cancel/observe 500-page work and ties client connection to processing. Rejected.

## Consequences

Positive: simple deployment, durable checkpoints, warm models, bounded backpressure and direct PostgreSQL/worker-pool evolution.

Negative: custom scheduler/lease logic is correctness-sensitive; SQLite constrains horizontal scale; one host remains an availability boundary.

## Migration path

Move metadata/leases to PostgreSQL, split scheduler service, replicate capability-advertising workers, then add Dramatiq/another broker only if dispatch polling is measured as a bottleneck. Preserve job IDs/checkpoint semantics/state machine.

