# Observability Specification

| Field | Value |
|---|---|
| Status | Proposed |
| Signals | Structured logs, Prometheus metrics, OpenTelemetry traces |

## 1. Objectives

Operators must be able to answer:

- Which stage/page/scope is slow or failing?
- Which parser/model/pipeline version produced an entity/revision?
- Did fallback improve quality and how often is it invoked?
- Is backpressure caused by GPU, CPU, storage, queue, memory or disk?
- Can a particular chunk/citation be traced without placing document text in telemetry?

Telemetry is diagnostic metadata, not an alternate provenance store. Canonical provenance and immutable reports remain authoritative.

## 2. Correlation model

Every event/span carries, where applicable:

```text
service.name, service.version, environment
trace_id, span_id, correlation_id
tenant_hash (optional controlled), job_id, attempt_id, document_id
stage, scope_kind, page_number or page_batch (logs/traces only)
pipeline_version, adapter_id, adapter_version, parser_version, model_revision
```

High-cardinality values (`job_id`, `document_id`, `page_number`, error message) are forbidden as Prometheus labels. IDs are permitted in protected logs/traces with retention/access control. Tenant names, filenames and text are never default telemetry fields.

## 3. Structured logging

JSON log envelope:

```json
{
  "timestamp": "2026-08-28T08:23:08.120Z",
  "level": "INFO",
  "event": "fallback.merge.applied",
  "service": "docparser-worker",
  "service_version": "0.1.0",
  "correlation_id": "req_...",
  "trace_id": "...",
  "job_id": "job_...",
  "attempt_id": "attempt_...",
  "document_id": "doc_...",
  "stage": "MERGING",
  "scope_kind": "TABLE",
  "page_number": 23,
  "adapter_id": "paddleocr_vl",
  "duration_ms": 142,
  "quality_before": 0.64,
  "quality_after": 0.91,
  "outcome": "success"
}
```

Required event families:

- `job.received`, `job.state_changed`, `job.cancel_requested`, `job.terminal`;
- `preflight.completed`;
- `parser.worker_ready`, `parser.request_started/completed/failed`, `parser.worker_recycled`;
- `normalize.completed`, `validation.completed`;
- `fallback.plan_created`, `fallback.target_started`, `fallback.merge.applied/rejected/conflict`;
- `chunk.completed`, `export.completed`;
- `checkpoint.committed/reused/invalidated`;
- `artifact.sealed/verify_failed/deleted`;
- `security.input_rejected`, `security.limit_exceeded`;
- `benchmark.completed`, `regression.detected`.

Rules:

- Use event codes/fields, not interpolated prose, for queries.
- Stack traces appear only on unexpected/internal failures in restricted logs.
- Extracted text, raw parser response, Authorization, signed URLs, full filenames and user metadata are redacted.
- Sampling never drops security/audit, terminal failure or merge-decision events. Success page spans may be sampled at scale.

Every `job.terminal` event is a complete bounded execution summary with `job_id`, `document_id`, terminal state, pipeline/adapter/parser/model versions, `start_time`, `end_time`, `page_count`, successful/failed/fallback page counts, quality score/status, total/stage latency, retry count, final error code and active revision ID. Failed-page identities remain in the durable job/report and may be included in logs only as a bounded range/list.

## 4. Metrics

Names use base units and bounded labels.

### 4.1 Work and latency

```text
docparser_jobs_total{state,pipeline_profile}
docparser_job_duration_seconds{terminal_state,pipeline_profile,document_class}
docparser_stage_duration_seconds{stage,outcome}
docparser_pages_total{stage,outcome,document_class}
docparser_page_processing_duration_seconds{stage,adapter_id,outcome}
docparser_queue_page_equivalents{resource_class}
docparser_queue_oldest_age_seconds{resource_class}
docparser_active_workers{resource_class,adapter_id}
```

Histograms have buckets chosen from measured workload and support P50/P95/P99 dashboards. `documents/hour` and `pages/sec` are derived rates, not separate gauges.

### 4.2 Quality and fallback

```text
docparser_quality_score{document_class,pipeline_profile}
docparser_quality_issues_total{rule_id,severity,document_class}
docparser_fallback_targets_total{scope_kind,adapter_id,outcome,reason_group}
docparser_fallback_page_ratio{document_class,pipeline_profile}
docparser_fallback_area_ratio{document_class,pipeline_profile}
docparser_fallback_quality_delta{scope_kind,adapter_id}
docparser_whole_document_fallback_total{reason_group,outcome}
docparser_partial_documents_total{reason_group}
```

Rule IDs are a bounded deployed set. Issue IDs/entity IDs are never labels.

### 4.3 Reliability/storage/resources

```text
docparser_errors_total{error_code,stage,retryable}
docparser_retries_total{error_code,stage}
docparser_checkpoint_reuse_total{stage,outcome}
docparser_worker_crashes_total{resource_class,adapter_id}
docparser_gpu_utilization_ratio{worker_slot}
docparser_gpu_memory_bytes{worker_slot,kind="used|reserved|total"}
docparser_process_resident_memory_bytes{service}
docparser_artifact_io_duration_seconds{operation,backend,outcome}
docparser_artifact_bytes_total{class,operation}
docparser_storage_free_bytes{backend}
docparser_sqlite_busy_total{operation}
docparser_lease_expirations_total{resource_class}
```

`worker_slot` is a small configured ordinal, not a volatile device UUID. Cost/page is derived from measured runtime/resource-rate configuration and is labeled as an estimate.

## 5. Distributed traces

Root span: `document.process`. Child spans:

```text
admission
preflight
primary.parse
  parser.batch
normalize
validate
fallback.plan
  fallback.parse
  fallback.normalize
  fallback.match_merge
  fallback.revalidate
postprocess.chunk
export
manifest.commit
```

Span attributes follow OpenTelemetry semantic conventions where available and custom `docparser.*` keys otherwise. Events record checkpoint IDs, issue counts and merge outcome. Page-level spans are sampled/batched for large documents; failed pages always retain spans.

Trace propagation crosses API, scheduler and worker dispatch through trusted metadata. Parser subprocesses receive only trace identifiers, never baggage containing tenant/user data.

## 6. Audit trail

Audit events are durable database records, distinct from operational logs:

- submission, download, cancellation, retry, reprocess, active-revision switch and deletion;
- pipeline/model/license policy change;
- operator access to raw artifacts;
- security rejection and retention/legal-hold changes.

Fields: event ID/time, actor/tenant, action, resource IDs, outcome, policy/config version, correlation ID and safe reason. Audit records are append-only and access-controlled.

## 7. Dashboards and alerts

### MVP dashboards

1. Throughput/latency by stage and document class.
2. Queue/backpressure, worker readiness, GPU/VRAM/RAM and disk.
3. Completion/partial/failure/retry and top stable error codes.
4. Quality score, issue rules, fallback target/page/area rates and quality deltas.

### Initial alerts

- no ready primary/fallback GPU worker for configured grace period;
- queue oldest age/page-equivalents beyond capacity target;
- failure/retry/whole-document fallback rate deviation from rolling baseline;
- repeated OOM, worker crash or lease expiry;
- disk critical watermark, checksum failure or SQLite busy saturation;
- provenance/page-count hard-gate failure (should be near zero and release-blocking);
- fallback applied with non-positive post-validation delta (correctness incident).

Thresholds are environment-specific, version-controlled and tested in staging. Alerts link to runbooks by stable error/event code.

## 8. SLO measurement

Service-level indicators exclude explicitly rejected invalid inputs but report them separately:

- accepted-job terminal success/approved-partial ratio;
- end-to-end job latency by page-count/document-class bucket;
- queue delay vs processing delay;
- provenance/page-completeness invariant rate;
- checkpoint recovery success after transient faults.

Quality is not reduced to availability. Golden/regression metrics are release indicators; runtime rule/score distributions detect drift. Both must be visible.

## 9. Cardinality, privacy and retention

- Metrics labels are reviewed through a fixed allow-list and cardinality test.
- Logs/traces use configurable sampling and shorter retention than audit/provenance where appropriate.
- Document/tenant IDs may be hashed for centralized diagnostics; support tooling resolves them under authorization.
- No body/source/chunk text is sent to telemetry backends.
- Self-hosted deployment must work with telemetry disabled/exported locally; core correctness never depends on the collector.

## 10. Tests

- Schema tests for required structured fields/event names.
- Redaction tests with canary secrets, filenames, signed URLs and document text.
- Metrics cardinality snapshot tests and no-ID-label static checks.
- Trace parent/child and error status tests across worker boundary.
- Fault injection asserts terminal/error/checkpoint events and counters.
- Dashboard queries and alert rules lint/test against fixture metrics.
