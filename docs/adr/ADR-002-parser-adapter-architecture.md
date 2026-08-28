# ADR-002: Parser Adapter Architecture

## Status

Proposed — 2026-08-28

## Context

MinerU, PaddleOCR-VL, Docling, Marker, Surya and commercial/self-built parsers differ in inputs, scopes, runtime, errors and output schemas. Router conditions on parser names would spread coupling and make capabilities untestable.

## Decision

Define a synchronous worker-side `DocumentParser` Protocol with `descriptor()`, `health()` and `parse(ParseRequest) -> ParseResult`. Use explicit capability discovery, scope/resource declarations, structured errors, raw artifact preservation and adapter contract tests. Keep parser imports/private schema/option mapping inside `adapters/parsers/<name>`; normalize separately into Canonical IR.

Model lifecycle belongs to a long-lived `ParserRuntime`, so a model loads once per worker rather than once per document.

## Alternatives

### Common lowest-denominator Markdown interface

Simple but cannot preserve tables, geometry, partial failures or confidence semantics. Rejected.

### Let each adapter emit Canonical IR directly

Fewer objects but mixes vendor mapping with document-wide normalization/ID/section policy and makes cross-adapter consistency harder. Rejected.

### Remote HTTP microservice per parser from day one

Strong isolation but adds deployment/network/version complexity on a single host. Deferred; a remote adapter can implement the same port later.

### Base-class inheritance hierarchy

Encourages accidental shared vendor behavior and fragile overrides. Rejected in favor of a Protocol and composition.

## Consequences

Positive: truthful routing, reusable contract suite, parser isolation/replacement and controlled model residency.

Negative: neutral envelope plus normalizer adds mapping effort; capabilities can still lie unless contract-tested; adapter version pins require maintenance.

## Migration path

Add adapters without domain changes, shadow benchmark, canary via config and preserve prior pinned worker images. Contract major changes use a new adapter contract version with compatibility wrappers.

