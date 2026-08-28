# ADR-006: Independent Semantic Versioning and Immutable Revisions

## Status

Proposed — 2026-08-28

## Context

Parser, model, Canonical IR, pipeline, validator, merge and chunk behavior change at different rates. One application version cannot explain or reproduce output. In-place upgrades would invalidate citations and comparisons.

## Decision

Version independently:

```text
IR schema, adapter contract, adapter/parser/model, pipeline,
normalizer, validator ruleset, merge algorithm, chunk schema/chunker/tokenizer,
renderer, config hash and benchmark dataset/runner.
```

Apply SemVer to public wire/behavior contracts, pin immutable dependency/model/container digests, and create immutable IR/chunk/report revisions. Schema migrations are pure, tested and create new artifacts/revisions with lineage. Unknown unnamespaced fields are rejected; bounded namespaced extensions are preserved.

## Alternatives

### Single application version

Easy display but cannot identify component changes or selective invalidation. Rejected.

### Mutable document row updated in place

Simple reads but destroys reproducibility, citations and rollback. Rejected.

### Timestamp-only/versionless JSON

Cannot validate compatibility or automate migrations. Rejected.

### Accept any fields for forward compatibility

Avoids rejections but silently changes semantics and enables unbounded parser leakage. Rejected; namespaced extensions provide controlled flexibility.

## Consequences

Positive: precise provenance, reproducible benchmarks, selective checkpoint invalidation and safe rollback.

Negative: version matrix and migrations require discipline; storage retains multiple revisions; promotion tooling must validate compatibility.

## Migration path

Support current major plus agreed prior minors, provide deterministic migration registry, retain prior active manifest for rollback and garbage-collect old revisions only under retention policy. Any coordinate/ID/meaning change requires a major schema ADR.

