# Test Strategy

| Field | Value |
|---|---|
| Status | Proposed |
| Tooling baseline | pytest, Hypothesis, Ruff, pyright/mypy, JSON Schema validator |

## 1. Quality objective

Parser implementations may change; Canonical IR behavior cannot change silently. Tests therefore concentrate at boundaries and pure domain transformations, with a smaller number of real parser/GPU tests.

Every implementation phase must leave the repository installable, its CLI smoke command runnable, all previously enabled tests passing and architecture contracts valid.

## 2. Test layers

### 2.1 Unit tests

Fast, offline, no GPU/network/files outside temp directory:

- ID, coordinates, transforms and canonical serialization;
- normalization mappings from recorded parser envelopes;
- quality rules/scoring/recommendation coalescing;
- fallback matching, split/merge/conflict/dedup/order rebuild;
- chunk semantic packing and token accounting with pinned tiny tokenizer fixture;
- state transitions, retry classification, config validation;
- storage key/path/retention logic and API mappers.

Use property-based tests for geometry, graphs, tables, IDs and serialization. Mutation testing may be introduced for critical merge/rule modules after MVP baseline.

### 2.2 Schema tests

- Pydantic model -> generated JSON Schema is deterministic; committed diff reviewed.
- Positive/negative JSON fixtures for every entity/discriminated union.
- Strict unknown-field, bounds, NaN/Infinity, extension namespace/depth/size rejection.
- Referential/domain invariants beyond JSON Schema.
- Canonical hash and Unicode/coordinate precision golden vectors.
- Monolithic vs sharded packaging reassembly/semantic-digest equivalence, bounded-reader memory and missing/corrupt shard rejection.
- Backward read, migration, round-trip and downgrade mapping tests for supported schema versions.

### 2.3 Parser adapter contract tests

Shared suite from `PARSER_ADAPTER_SPEC.md` runs against:

1. deterministic fake adapter in every CI run;
2. each real adapter with recorded raw fixtures offline;
3. smoke subset against pinned real model/container on scheduled GPU CI.

Contract failures block an adapter version even when its quality benchmark improves. Truthful capability discovery, scope enforcement, partial errors, transforms and raw artifact preservation are mandatory.

### 2.4 Integration tests

Exercise real infrastructure adapters with small deterministic fake parser:

- CLI/API -> job -> worker -> storage -> IR -> validate -> chunk -> export;
- SQLite transactions/leases/idempotency/cancel/resume;
- local artifact atomic writes/checksums/cleanup;
- FastAPI multipart streaming/error/resources/ETag;
- OpenTelemetry/log/metric emission and redaction.

V1 adds S3/MinIO and PostgreSQL contract/integration matrices in containers.

### 2.5 Golden and regression tests

The dataset/metrics/gates in `EVALUATION_SPEC.md` are authoritative. Tests include:

- tiny redistributable CPU smoke corpus per pull request;
- protected quality subset nightly;
- full parser/fallback/performance/fault suite for release/promotion;
- visual overlays/diffs stored as artifacts for actionable failures.

Golden expected outputs are semantic annotations/metrics, not an unreviewable snapshot of an entire parser JSON. Exact IR snapshots are reserved for deterministic normalization/schema fixtures.

### 2.6 Failure-injection tests

Inject at application ports/process boundaries:

- timeout/OOM/crash/invalid/partial parser output;
- worker killed before/after artifact seal and checkpoint/manifest transaction;
- stale lease/fencing, duplicate delivery and concurrent cancellation;
- disk full/permission/checksum/slow store and SQLite busy;
- corrupt/encrypted/extreme-dimension PDF;
- fallback candidate conflict/no gain/out-of-scope geometry;
- short/duplicate parser batches, blocked lease renewal during native call and supervisor kill/recycle;
- exporter/tokenizer failure.

Assertions include legal terminal state, unchanged baseline on failed merge, checkpoint reuse, bounded retry, no leaked active lease/scratch, structured error and telemetry.

### 2.7 Security tests

Follow `SECURITY_SPEC.md`: malicious input corpus, path/tenant/SSRF/resource limits, output active content, secret/text telemetry leakage and supply-chain gates. Security corpus runs in isolated CI infrastructure.

## 3. Contract ownership

| Contract | Test owner |
|---|---|
| Canonical IR wire/domain invariants | `tests/schema`, `tests/unit/ir` |
| Parser envelope/capabilities/errors | `tests/contract/parsers` |
| Storage semantics | `tests/contract/storage` |
| Job/state/lease/checkpoint | `tests/unit/application`, `tests/integration/jobs` |
| Quality score/issues | `tests/unit/quality`, Golden defects |
| Fallback merge | `tests/unit/fallback`, targeted golden set |
| Chunk/provenance | `tests/unit/chunking`, RAG golden annotations |
| API/CLI | OpenAPI/CLI snapshots plus integration tests |
| Observability/redaction | telemetry contract tests |

## 4. Fixtures and test doubles

- Recorded raw parser outputs include parser/version/license/source digest and are immutable.
- The fake parser supports deterministic good, partial, timeout, crash, OOM and malformed modes without parser-name branches in application logic.
- Time, UUID generation, worker heartbeat and resource readings use injectable clocks/providers.
- Filesystem tests use pytest-provided unique temp roots and assert all paths remain below them.
- Golden inputs are checksummed and license/privacy classified; missing protected assets skip only with explicit suite-incomplete status, never a false pass.

## 5. Determinism and flakiness policy

- Seed randomized tests and record failing examples.
- Freeze locale/timezone/config and pin dependencies/models/tokenizers.
- Do not use arbitrary sleeps for concurrency; use barriers/fake clocks/eventual assertions with bounded deadlines.
- A flaky test is quarantined only with owner, issue, expiry and preserved gate visibility.
- Re-running a quality failure to obtain a pass is prohibited. Infrastructure retries are reported separately and bounded.
- GPU numeric tolerances are explicit per metric/field; structural/provenance invariants have no tolerance.

## 6. CI tiers

### Pull request — target under 10 minutes

- formatting/lint/type check;
- unit/property/schema tests;
- fake adapter and local storage contracts;
- API/OpenAPI/CLI smoke;
- tiny CPU golden set;
- docs links/required-section/Mermaid/JSON examples lint where practical.

### Nightly

- real adapters with pinned recorded/live small models where resources allow;
- protected quality subset and fallback golden set;
- integration/failure-injection/security non-destructive suites;
- dependency/license/vulnerability scans.

### Release/promotion

- full Golden Dataset and paired baseline report;
- reference GPU performance/capacity suite;
- sandbox and recovery tests;
- schema/API compatibility and migration matrix;
- SBOM/model/container digest/license approval;
- backup/restore and rollback rehearsal.

## 7. Coverage and gates

Line coverage is diagnostic, not the primary goal. Initial minimum 85% for pure domain/application modules and 100% branch coverage for explicit job state transition table/error retry classification where feasible. Adapter vendor-call wrappers may have lower line coverage but must pass contract/error fixture tests.

Release blockers:

- any IR page/provenance/referential hard invariant failure;
- parser/storage contract failure;
- illegal state transition or non-idempotent replay;
- fallback non-target corruption/no-positive-gain apply;
- protected regression gate failure;
- security/tenant/supply-chain gate failure;
- incomplete benchmark run misreported as pass.

## 8. Test data privacy and cleanup

- Never place customer documents in Git or broad CI artifacts without approval.
- Logs and assertion diffs redact text for sensitive fixtures; authorized visual diffs use controlled storage.
- Test object stores/databases have explicit unique namespaces and verified cleanup roots.
- Generated artifacts have retention limits; protected sources and annotations follow their dataset policy.

## 9. Definition of a tested phase

A phase is complete only when:

1. its declared tests and prior suite pass;
2. new public/schema contracts have positive/negative fixtures;
3. failure/retry/idempotency behavior is tested, not only happy path;
4. observability fields and redaction are tested for new stages;
5. docs/ADR/config examples are updated;
6. repository smoke command succeeds from a clean environment;
7. no test requires internet or real GPU unless tagged and excluded from the default suite.
