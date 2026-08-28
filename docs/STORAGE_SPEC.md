# Storage Architecture Specification

| Field | Value |
|---|---|
| Status | Proposed |
| MVP | Local filesystem artifacts + SQLite metadata |
| V1 path | S3/MinIO artifacts + PostgreSQL metadata |

## 1. Separation of responsibilities

Storage is split into three ports because one generic `Storage` interface would hide incompatible semantics:

1. **ArtifactStore:** immutable byte objects, range reads, checksum verification and lifecycle metadata.
2. **JobRepository:** transactional job/attempt/state/idempotency/manifest records.
3. **LeaseCheckpointStore:** fenced leases and compatible unit checkpoints; may share the same SQLite/PostgreSQL implementation with `JobRepository`.

Large parser payloads/page images/IR/chunks never live in database rows. The database stores bounded metadata and immutable artifact references.

## 2. Artifact classes

| Class | Examples | Mutability | Default retention |
|---|---|---|---|
| Original | uploaded PDF | Immutable | Tenant policy/legal hold |
| Intermediate | page images, crops, raw parser output, OCR fragments, diagnostics | Immutable | Short/medium, configurable |
| Canonical | IR revisions, schema migration outputs, quality/merge reports | Immutable | At least as long as active document revision |
| Derived | Markdown, HTML, JSONL chunks, benchmark reports | Immutable/rebuildable | Configurable; manifest retained |
| Ephemeral | worker scratch, partial uploads, uncommitted temp objects | Mutable until sealed | Minutes/hours; aggressive cleanup |

## 3. Artifact port

```python
class ArtifactStore(Protocol):
    def begin_write(self, request: BeginArtifactWrite) -> ArtifactWriter: ...
    def stat(self, artifact_id: str) -> ArtifactMetadata: ...
    def open_read(self, artifact_id: str, byte_range: ByteRange | None = None) -> BinaryIO: ...
    def verify(self, artifact_id: str, expected_digest: str) -> None: ...
    def create_read_url(self, artifact_id: str, expires_in: int, actor: Actor) -> str | None: ...
    def mark_for_deletion(self, artifact_id: str, reason: str) -> None: ...
```

The writer supports streaming hash/size limits and `commit()`/`abort()`. `commit()` seals an immutable object only after digest verification. Overwriting an existing artifact ID is prohibited; same content may be deduplicated internally within a tenant/policy boundary.

`ArtifactMetadata` includes artifact ID/class/media type/encoding, tenant/document/job ownership, SHA-256, size, creation time, producing component/version, source artifact IDs, retention class, encryption key reference and state (`WRITING`, `SEALED`, `QUARANTINED`, `DELETION_PENDING`, `DELETED`).

## 4. Logical key layout

User filenames never form paths. IDs are opaque and validated.

```text
data/
  tenants/{tenant_id}/
    documents/{document_id}/
      source/{artifact_id}.pdf
      artifacts/
        preflight/{artifact_id}.json
        pages/{render_key}/{artifact_id}.png
        parsers/{parser_run_id}/{artifact_id}.json
        diagnostics/{artifact_id}.json
      canonical/
        ir/{revision_id}/manifest.json
        ir/{revision_id}/pages/{page_range}-{artifact_id}.jsonl
        ir/{revision_id}/entities/{kind}-{artifact_id}.jsonl
        quality/{quality_report_id}/{artifact_id}.json
        merge/{merge_operation_id}/{artifact_id}.json
      exports/
        {revision_id}/markdown/{artifact_id}.md
        {revision_id}/html/{artifact_id}.html
        {revision_id}/chunks/{chunk_manifest_id}/chunks.jsonl
      manifests/
        document.json
```

This is a logical prefix convention usable by local filesystem and object storage. Object identity is the database/manifest reference plus digest, not a guessed path.

## 5. Local filesystem adapter

- Root is resolved once from validated absolute configuration; every target is checked to remain under it.
- Writes use a same-filesystem random temp file, `fsync`, checksum, atomic rename and parent-directory sync where supported.
- Files/directories use least privilege; worker scratch is per job/attempt with no symlink following.
- Manifest pointers are changed only in a SQLite transaction after sealed artifacts exist.
- Startup reconciliation finds expired `WRITING` objects and unreachable sealed objects after a grace period.
- Disk quotas reserve headroom before page rendering. Backpressure starts before the filesystem reaches critical fullness.

Local artifact data may be backed up separately from SQLite, but a consistent snapshot has a single `snapshot_epoch`: create a SQLite online backup/checkpoint, emit the immutable artifact reachability manifest/digests referenced by that database snapshot, and record both under the epoch. Restore is incomplete until every active reference is verified; newer unreachable immutable objects may be recovered separately but cannot be inferred active.

## 6. S3/MinIO adapter path

- Use multipart uploads with explicit size/checksum and abort stale uploads.
- Seal through a final immutable object key or versioned object plus metadata record; never rely on list-after-write for correctness.
- Conditional writes (`If-None-Match`/version checks) enforce immutability.
- Server-side encryption is required; per-tenant keys are optional future policy.
- Presigned URLs are short-lived, actor-authorized, response-content-type constrained and never logged.
- Lifecycle policies delete ephemeral/intermediate artifacts only after metadata eligibility and grace period.
- Bucket versioning/object lock is policy-dependent; application revisions remain immutable regardless.

The domain sees the same artifact ID/digest. Migration can copy and verify objects, update backend location metadata transactionally, then garbage-collect old copies.

## 7. Metadata model

MVP SQLite tables, conceptually:

```text
documents(id, tenant_id, source_digest, active_revision_id, created_at, deleted_at)
artifacts(id, tenant_id, document_id, class, backend, locator, digest, size, state, ...)
jobs(id, tenant_id, document_id, state, state_version, config_hash, idempotency_key, ...)
attempts(id, job_id, number, stage, error_code, started_at, ended_at, dead_lettered_at)
leases(resource_id, owner_id, fence, expires_at)
checkpoints(id, job_id, stage, scope_hash, compatibility_hash, output_artifact_ids, status, ...)
revisions(id, document_id, number, previous_id, ir_artifact_id, quality_report_id, digest, ...)
manifests(id, document_id, revision_id, kind, artifact_id, active, ...)
audit_events(id, tenant_id, actor, action, resource, outcome, created_at, correlation_id)
```

Unique constraints:

- `(tenant_id, idempotency_key)` for request idempotency policy window;
- `(document_id, revision_number)`;
- checkpoint semantic key/compatibility hash;
- one active revision/manifest kind through transactional constraints;
- artifact ID and digest consistency.

The source digest is not globally exposed or used to deduplicate across tenants unless explicit privacy policy permits it.

## 8. Manifest and commit protocol

```text
write temp object -> stream hash/limits -> seal immutable artifact -> verify stat/digest
-> begin metadata transaction -> verify lease fence/base revision
-> insert artifact/revision/checkpoint records -> switch active manifest pointer
-> append audit event -> commit
```

Readers resolve through the active manifest and verify expected digest on first/cache-sensitive read. They never scan directories to infer truth.

Large Canonical IR uses the lossless packaging profile from `DOCUMENT_IR_SPEC.md`. Shard size is bounded by entity count/bytes and chosen without changing logical IDs or semantic digest. Writers stream shards; readers may page through them using manifest descriptors and a bounded global-reference index.

Crash cases:

- Before seal: expired temp cleaned.
- After seal, before metadata: unreachable sealed object cleaned after grace period.
- During metadata transaction: rollback, old manifest remains active.
- After commit: operation is complete; repeated request returns existing record.

## 9. Idempotency and duplicate submission

Document identity is stable for exact bytes within a tenant. Job idempotency is a client key plus normalized semantic request hash. Behavior:

- same key + same request hash -> return existing job;
- same key + different hash -> `409 IDEMPOTENCY_KEY_REUSED`;
- same source/config without client key -> configurable coalescing to active/completed job, never cross-tenant;
- changed pipeline/config creates a distinct job/revision while reusing authorized immutable source.

Idempotency retention must cover the maximum client retry window. Cancellation does not permit reuse of the same key for a different request.

## 10. Retention, deletion and cleanup

- Retention policies are per tenant/artifact class and recorded at creation.
- Deletion is two phase: revoke active references/mark pending, then asynchronous physical removal after grace/legal-hold checks.
- Document deletion traverses the manifest/reference graph. Shared content is removed only when no authorized references remain.
- Scratch cleanup occurs on worker finally blocks and supervisor reconciliation; material deletions are audited.
- Raw parser outputs may contain sensitive text and receive the same classification/encryption as originals.
- Backups have a separate retention/deletion policy and are included in compliance documentation.

## 11. Quotas and backpressure

Admission estimates source, rendered pixel and intermediate expansion. Enforce:

- per-file/page/pixel limits;
- tenant active jobs and stored bytes;
- host scratch/artifact high/critical watermarks;
- maximum raw artifact/IR/entity counts;
- page-cache budget and LRU/lifecycle eligibility.

At high watermark, pause new rendering/submission while allowing commits/cleanup. At critical watermark, fail safely before partial writes and alert.

## 12. Security

- Artifact locators are never accepted directly from untrusted clients.
- All port calls carry tenant/actor authorization context.
- Local adapter rejects traversal, symlinks/reparse points and alternate data streams.
- MIME and safe content-disposition are set on downloads; HTML/SVG exports are served as attachments unless sanitized.
- Encryption in transit and at rest is required for service deployment; key material is outside config files and IR.
- Integrity verification and audit events are mandatory for source, canonical and manifest operations.

## 13. Tests and operations

- Shared contract tests for local and S3-compatible adapters.
- Atomicity/crash tests around seal/transaction/manifest switch.
- Checksum mismatch, short write, disk-full, permission and stale multipart tests.
- Tenant authorization/path traversal/symlink and presigned URL tests.
- Migration copy/verify/cutover/rollback test.
- Backup/restore exercise validates active manifests and random artifact samples.
- Metrics: stored bytes/artifacts by class, write/read latency, checksum failures, temp/unreachable count, GC/deletion lag, disk watermark and database busy/transaction latency.
