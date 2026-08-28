# ADR-004: Split Artifact and Metadata Storage Abstractions

## Status

Proposed — 2026-08-28

## Context

The system stores large immutable originals/raw/page/canonical/derived bytes and small transactional job/lease/manifest records. A filesystem-only metadata convention lacks atomic state/idempotency; a database is unsuitable for large blobs. MVP should not require cloud services.

## Decision

Use separate `ArtifactStore`, `JobRepository` and `LeaseCheckpointStore` ports. MVP implements immutable local filesystem artifacts with checksum/atomic rename and SQLite WAL transactional metadata. V1 adds S3/MinIO and PostgreSQL adapters with unchanged logical artifact IDs/manifests.

## Alternatives

### Everything in filesystem JSON

Low setup but weak concurrent compare-and-set, queries, leases and cancellation. Rejected.

### Everything in PostgreSQL/blob columns

Transactional but operationally heavy for MVP and inefficient for page/raw artifacts. Rejected.

### PostgreSQL + S3 from day one

Scalable and familiar but violates single-host/simple-first constraint and increases test/ops burden. Deferred.

### One generic storage interface

Hides object immutability vs transactional record semantics. Rejected.

## Consequences

Positive: minimal MVP operations, crash-safe manifests, backend replacement and clear lifecycle/retention.

Negative: SQLite has write/single-host ceilings; local backup must coordinate DB/artifacts; ports and contract tests add code.

## Migration path

Copy/verify immutable objects to S3, migrate relational schema to PostgreSQL, transactionally switch locations, then remove old copies after grace period. Job truth never moves to broker messages.

