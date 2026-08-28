# ADR-001: Canonical Document IR

## Status

Proposed — 2026-08-28

## Context

Multiple parsers expose incompatible blocks, coordinates, tables, confidence and provenance. Business code tied to any one output cannot safely replace parsers, compare quality or produce citations. Markdown alone loses structure and spatial lineage.

Requirement: a serializable, testable, extensible and versioned contract that supports text/layout/tables/figures/equations/sections/chunks and source traceability.

## Decision

Own a Canonical Document IR independent of all parsers. Author strict runtime models in Pydantic v2 and generate/commit JSON Schema Draft 2020-12 as the wire contract. Use normalized top-left, post-rotation CropBox point coordinates; centralized provenance; immutable revisions; logical cross-page tables with page segments; namespaced bounded extensions; opaque versioned IDs.

Parser raw outputs remain immutable referenced artifacts. They are never the business contract.

## Alternatives

### Adopt a parser's native schema

Fast initially and may be rich, but replacement and multi-parser merge become vendor-schema migrations. Rejected.

### Markdown/HTML as canonical output

Easy consumption but loses table topology, reliable geometry, entity identity and provenance. Rejected as canonical; retained as derived exports.

### JSON Schema authored by hand only

Language-neutral but duplicates runtime model work and drifts easily. Rejected in favor of generated, committed schema.

### Python dataclasses only

Lightweight but insufficient runtime/wire validation and API schema generation. Allowed only for internal ephemeral values.

## Consequences

Positive:

- parsers and storage/export consumers are replaceable;
- merge, citation, regression and migration semantics are explicit;
- domain logic is GPU/vendor independent.

Negative:

- normalization is substantial engineering work;
- IR must intentionally omit/extension-map parser novelty;
- schema governance and migrations become permanent responsibilities.

## Migration path

Version with SemVer, ship pure migrations, preserve old immutable revisions and support current major plus prior minor versions per `DOCUMENT_IR_SPEC.md`. A future standard schema may be mapped at import/export boundaries without replacing internal contracts unless a new ADR proves benefit.

