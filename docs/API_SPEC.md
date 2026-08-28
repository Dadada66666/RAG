# API and CLI Specification

| Field | Value |
|---|---|
| Status | Proposed |
| HTTP API version | `/v1` |
| Style | Asynchronous jobs over JSON/multipart; OpenAPI generated and contract-tested |

## 1. Principles

- API resources expose documents, immutable revisions, jobs and artifacts—not parser-private schemas.
- Submission success means accepted/deduplicated, not parsed correctly. Normal success is HTTP `202`.
- All mutating requests are tenant-scoped, authenticated in service deployments and idempotent where applicable.
- Canonical/derived large payloads are streamed or downloaded as artifacts; they are not embedded in job status.
- API, CLI and worker call the same application use cases.

## 2. Versioning and headers

Required/standard headers:

```text
Authorization: Bearer ...          # service mode; auth provider is deployment-specific
Idempotency-Key: opaque-client-key # POST /documents and reprocess
Traceparent: ...                    # optional W3C trace propagation
Content-Type: multipart/form-data or application/json
Accept: application/json
```

Responses include `X-Request-ID`; immutable resources include `ETag`. Breaking API changes use `/v2`; additive fields may appear in `/v1`, and clients must ignore unknown response fields. Canonical IR media/schema version is independent of HTTP API version.

## 3. Resource schemas

### `DocumentResource`

```json
{
  "document_id": "doc_...",
  "status": "PROCESSING",
  "source": {"filename": "report.pdf", "media_type": "application/pdf", "size_bytes": 243120},
  "active_revision_id": null,
  "latest_job_id": "job_...",
  "created_at": "2026-08-28T08:20:00Z",
  "updated_at": "2026-08-28T08:20:00Z",
  "links": {"self": "/v1/documents/doc_...", "job": "/v1/jobs/job_..."}
}
```

Document status is a projection: `PROCESSING`, `AVAILABLE`, `PARTIAL`, `FAILED`, `DELETED`. Job state is authoritative for execution.

### `JobResource`

```json
{
  "job_id": "job_...",
  "document_id": "doc_...",
  "state": "VALIDATING",
  "state_version": 7,
  "attempt": 1,
  "progress": {
    "stage": "VALIDATING",
    "page_count": 100,
    "completed_pages": 100,
    "successful_pages": 100,
    "failed_pages": [],
    "fallback_pages": [23],
    "percent_estimate": 72
  },
  "quality": {"score": 0.74, "status": "DEGRADED", "report_id": "qrep_..."},
  "error": null,
  "created_at": "2026-08-28T08:20:00Z",
  "started_at": "2026-08-28T08:20:01Z",
  "ended_at": null,
  "links": {"self": "/v1/jobs/job_...", "document": "/v1/documents/doc_..."}
}
```

`percent_estimate` is monotonic within an attempt but approximate because fallback work is data-dependent. Page counts and terminal state are not approximate.

## 4. Endpoints

### `POST /v1/documents`

MVP accepts `multipart/form-data`:

| Part | Required | Meaning |
|---|:---:|---|
| `file` | Yes | Streamed PDF; filename is display-only |
| `request` | No | JSON with `pipeline_profile`, `allow_partial`, `retention_class`, `client_metadata` |

MVP does not fetch arbitrary URLs. A future source-connector endpoint may accept allow-listed object references after SSRF design review.

Success:

- `202 Accepted` new job;
- `200 OK` when the same idempotency key/request already completed;
- `202 Accepted` when it resolves to the same active job.

```json
{
  "document_id": "doc_...",
  "job_id": "job_...",
  "deduplicated": false,
  "state": "RECEIVED",
  "links": {"document": "/v1/documents/doc_...", "job": "/v1/jobs/job_..."}
}
```

Errors include `413` size/page-known limit, `415` invalid media type, `422` invalid options, `409` reused idempotency key with different request, `429` quota/backpressure and `503` admission unavailable.

### `GET /v1/documents/{document_id}`

Returns document projection and active revision/export links authorized for the tenant. Supports `If-None-Match`.

### `GET /v1/documents/{document_id}/revisions`

Paginated immutable revision summaries: revision/schema/pipeline versions, quality, job, created time and active flag.

### `GET /v1/documents/{document_id}/revisions/{revision_id}`

Returns revision metadata and artifact links, not the entire IR.

### `GET /v1/documents/{document_id}/revisions/{revision_id}/content`

Query `format=ir-json|markdown|html|chunks-jsonl|quality-report|merge-report`. Streams authorized artifact or returns a short-lived download redirect depending backend. `ir-json` uses Canonical IR media type.

### `POST /v1/documents/{document_id}/reprocess`

Creates a new job/revision using an approved pipeline profile. It never overwrites the active revision. Parser-specific options are not public in MVP. Requires `Idempotency-Key`.

### `DELETE /v1/documents/{document_id}`

Marks document for policy-governed deletion and returns `202`. It does not promise immediate physical removal; legal hold returns `409`. Deletion scope/irreversibility is explicit in the response.

### `GET /v1/jobs/{job_id}`

Returns `JobResource`. Clients poll with bounded exponential backoff and `ETag`; future webhooks are additive.

### `POST /v1/jobs/{job_id}/cancel`

Idempotently requests cancellation. Returns `202` with state `CANCELLING`, or `200` for already `CANCELLED`. Terminal non-cancelled jobs return `409 JOB.TERMINAL`.

### `POST /v1/jobs/{job_id}/retry`

Operator/authorized endpoint. Creates a new attempt from compatible checkpoints only for retryable/exhausted/internal failures or explicitly approved recovery. It does not change source/pipeline semantics; use reprocess for that.

### Operational endpoints

- `GET /health/live`: process liveness only.
- `GET /health/ready`: database/store access and role readiness; GPU parser readiness is reported separately and may affect admission policy.
- `GET /metrics`: Prometheus endpoint on an operator-only listener/network.

## 5. Problem response and error mapping

Use `application/problem+json` with stable taxonomy fields:

```json
{
  "type": "https://docs.example/errors/INPUT.INVALID_MIME",
  "title": "Unsupported document media type",
  "status": 415,
  "detail": "The uploaded object is not an accepted PDF.",
  "instance": "/v1/requests/req_...",
  "error_code": "INPUT.INVALID_MIME",
  "retryable": false,
  "correlation_id": "req_...",
  "errors": []
}
```

Do not expose stack traces, parser native messages, filesystem paths, model endpoints, source text or credentials. Field-validation errors use bounded JSON pointers.

| Condition | HTTP status |
|---|---:|
| Authentication/authorization failure | 401/403 |
| Resource not found within tenant | 404 |
| State/idempotency conflict | 409 |
| Size limit | 413 |
| Media type | 415 |
| Schema/option validation | 422 |
| Quota/backpressure | 429 + `Retry-After` |
| Temporary storage/scheduler unavailable | 503 + `Retry-After` |

Parser/quality failure after acceptance is represented in the job terminal resource, not retroactively as an HTTP failure for the original POST.

## 6. Pagination, consistency and caching

- Cursor pagination uses opaque signed/validated cursors and stable sort `(created_at, id)`.
- List reads are eventually current within the primary metadata store transaction boundary; resource reads after a successful mutation are read-your-write on the same service.
- Immutable revisions/artifacts use strong ETags and long private cache controls. Job/document projections use short/no-cache plus ETags.
- The API never lists object-store prefixes to discover resources.

## 7. Authorization and tenant isolation

Every resource lookup includes tenant predicate before returning existence. Roles:

- `document.submit`, `document.read`, `document.delete`;
- `job.cancel`, `job.retry`;
- `benchmark.run/read` and `admin.pipeline` separated from normal tenants.

Parser selection/model configuration, raw parser outputs and detailed security diagnostics are operator-only by default. Audit all mutating actions and artifact downloads without logging content.

## 8. CLI contract

CLI maps to local application services by default and may use remote API through an explicit profile later.

```bash
docparser parse example.pdf --config configs/default.yaml --wait
docparser status JOB_ID --json
docparser cancel JOB_ID
docparser export DOCUMENT_ID --revision REVISION_ID --format chunks-jsonl --output ./out
docparser benchmark --suite tests/golden/manifest.yaml --candidate docling
docparser validate-ir path/to/document-ir.json
```

Exit codes:

```text
0 success/COMPLETED
2 invalid CLI/config/input
3 PARTIAL (only with --allow-partial)
4 FAILED/non-retryable
5 retryable infrastructure failure
130 cancelled/interrupted
```

Human output is concise; `--json` emits a stable CLI envelope. Secrets and extracted document text are not printed by default. `parse --wait` handles Ctrl+C by asking the local job to cancel or detaching according to explicit flag; it never leaves ambiguous ownership.

## 9. OpenAPI and contract tests

- Generated OpenAPI is committed and diff-reviewed.
- Request/response examples validate in CI.
- Tests cover idempotency replay/conflict, tenant non-disclosure, upload streaming limits, cancellation races, retry legality, ETag and range/download behavior.
- SDK generation, if adopted, consumes the committed OpenAPI; SDK types do not become domain models.

