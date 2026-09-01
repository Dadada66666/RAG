# Parser Adapter and Selection Specification

| Field | Value |
|---|---|
| Status | Proposed |
| Contract version | `parser-adapter/1.0.0` |
| Research date | 2026-08-28 |
| Research policy | Official repositories, documentation, model cards and package registries only |

## 1. Boundary and dependency rule

The adapter converts a versioned `ParseRequest` into a parser-neutral `ParseResult` plus an immutable raw artifact. It does **not** produce Canonical Document IR directly; a dedicated normalizer maps the neutral extraction envelope and linked raw payload into IR. This separation keeps parser calls/test doubles distinct from semantic normalization.

Only `src/docparser/adapters/parsers/<adapter>/` may import parser packages or understand private parser fields. Router, validator, fallback, chunker, API and storage code may depend only on the adapter contract and Canonical IR.

Prohibited:

```python
if parser_name == "mineru":
    ...
```

Required:

```python
if request.scope.kind in descriptor.capabilities.supported_scopes:
    ...
```

Parser worker invocation is synchronous because it is CPU/GPU-bound and isolated in a worker process. Async orchestration is outside this interface.

## 2. Contract pseudocode

```python
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol

class ParserCapability(StrEnum):
    PDF_TEXT = "PDF_TEXT"
    OCR = "OCR"
    LAYOUT = "LAYOUT"
    TABLE = "TABLE"
    TABLE_SPANS = "TABLE_SPANS"
    CROSS_PAGE_TABLE = "CROSS_PAGE_TABLE"
    FIGURE = "FIGURE"
    FIGURE_CAPTION = "FIGURE_CAPTION"
    FORMULA = "FORMULA"
    READING_ORDER = "READING_ORDER"
    HEADING_HIERARCHY = "HEADING_HIERARCHY"
    REGION_INPUT = "REGION_INPUT"
    NATIVE_PDF_INPUT = "NATIVE_PDF_INPUT"
    IMAGE_INPUT = "IMAGE_INPUT"
    BATCH = "BATCH"

class ScopeKind(StrEnum):
    DOCUMENT = "DOCUMENT"
    PAGE = "PAGE"
    REGION = "REGION"
    TABLE = "TABLE"
    FIGURE = "FIGURE"
    BLOCK = "BLOCK"

class DocumentParser(Protocol):
    def descriptor(self) -> "ParserDescriptor": ...
    def health(self) -> "ParserHealth": ...
    def parse(self, request: "ParseRequest") -> "ParseResult": ...
```

`warm()` and `close()` belong to a worker-owned `ParserRuntime` lifecycle, not each request. The worker loads a configured immutable model once, runs a known-page self-test, advertises readiness, and drains before model replacement.

## 3. `ParserDescriptor` and capability discovery

```text
ParserDescriptor
├── adapter_contract_version
├── adapter_id, adapter_version
├── parser_name, parser_version
├── model_descriptors[] {name, revision, digest, license_approval_id}
├── capabilities
│   ├── features[]
│   ├── supported_inputs[]
│   ├── supported_scopes[]
│   ├── languages[] / language_policy
│   ├── coordinate_spaces[]
│   ├── returns_confidence
│   ├── supports_batch
│   ├── deterministic_level
│   └── limitations[]
├── resource_profile
│   ├── device_types[], min_vram_bytes, recommended_vram_bytes
│   ├── max_pixels, max_pages_per_call, max_batch_pages
│   └── model_residency_key
└── option_schema
```

Discovery is configuration plus runtime truth. Capability claims bind to the exact parser/model/backend/option profile and carry known limitations; `TABLE` from one profile cannot be reused as proof for another. Runtime readiness, resource fit and benchmark eligibility further filter each request. A descriptor claiming `REGION_INPUT` must pass region contract tests; a renderer-wrapper adapter may truthfully expose region support if it clips inputs, records the exact transform and prevents unrelated content from entering the result.

`option_schema` is JSON Schema for namespaced, allow-listed options. Request-supplied arbitrary model paths, URLs, prompts, code or environment variables are prohibited.

## 4. `ParseRequest`

Required fields:

| Field | Meaning |
|---|---|
| `contract_version`, `request_id`, `job_id`, `attempt_id` | Contract and correlation |
| `document_id`, `source_artifact` | Immutable source reference + digest/media type/size |
| `scope` | `ParseScope` with kind, page numbers and canonical bbox/entity ID as applicable |
| `input_artifacts` | Authorized rendered page/crop references with pixel dimensions/DPI/transforms |
| `required_capabilities` | Minimum capabilities for this call |
| `language_hints` | BCP 47 hints with confidence, advisory only |
| `document_profile_ref` | Immutable preflight profile reference |
| `options` | Validated adapter-specific options, namespaced |
| `deadline_at`, `resource_budget` | Time/pages/pixels/tokens/memory budget |
| `idempotency_key` | Hash of semantic inputs and adapter/model/config versions |
| `trace_context` | W3C trace IDs only; no credentials |

Scope rules:

- `DOCUMENT`: source artifact required; page set means all verified pages.
- `PAGE`: explicit sorted unique one-based pages.
- `REGION`: exactly one page and canonical bbox; rendered crop includes padding and affine transform.
- `TABLE`, `FIGURE`, `BLOCK`: entity ID, page/bbox and expected type required. The adapter sees an image/native page as needed, while the semantic target remains explicit.
- A request cannot broaden its own scope. Out-of-scope outputs are rejected by the adapter envelope validator.

## 5. `ParseResult` and `PageParseResult`

### `ParseResult`

```text
ParseResult
├── contract_version, request_id
├── parser_run
├── status: COMPLETE | PARTIAL | FAILED
├── requested_scope, completed_scopes[], failed_scopes[]
├── pages[]: PageParseResult
├── raw_output_artifact
├── warnings[]
├── errors[]: ParserError
├── metrics
└── result_digest
```

`COMPLETE` means every requested scope produced a syntactically valid extraction envelope. It does not mean semantic quality passed. `PARTIAL` names every failed scope. `FAILED` may still link diagnostics/raw output but contains no publishable implied success.

The envelope validator performs exact requested/completed/failed scope cardinality accounting. For batched pages, every requested page must appear exactly once as completed or failed and carries a fragment digest. A short/duplicate/out-of-order vendor batch cannot be checkpointed as complete.

### `PageParseResult`

Required: `page_number`, `status`, `source_width`, `source_height`, `source_coordinate_space`, `to_canonical_transform`, `elements`, `relationships`, `confidence_semantics`, `warnings`, `errors`, `raw_fragment_ref`.

The neutral `ExtractedElement` envelope carries:

- adapter-local `original_object_id`;
- coarse `element_type` from the adapter contract;
- source bbox/polygon and coordinate metadata;
- text/structured table fragment/asset reference as a discriminated content union;
- raw confidence plus semantics;
- parser-local parent/order/relationship hints;
- bounded namespaced metadata.

It deliberately does not claim final section hierarchy, canonical IDs, calibrated confidence or resolved cross-page table identity.

## 6. `ParserError`

```text
ParserError {
  code,
  category,
  message_safe,
  scope,
  retryable,
  recoverable,
  fatal,
  suggested_action,
  parser_native_code,
  details_redacted
}
```

Adapter mapping must convert vendor exceptions to stable codes:

```text
PARSER.TIMEOUT, PARSER.OOM, PARSER.CRASH, PARSER.UNAVAILABLE,
PARSER.UNSUPPORTED_SCOPE, PARSER.INVALID_OUTPUT,
PARSER.MODEL_MISSING, PARSER.LICENSE_NOT_APPROVED,
PARSER.RESOURCE_LIMIT, PARSER.CANCELLED
```

Unknown vendor exceptions map to `PARSER.INTERNAL` with retryability false until explicitly classified. Exception string parsing is not permitted for normal classification when typed/native codes exist.

## 7. Adapter obligations

Every adapter shall:

1. Validate request contract, capabilities, scope and budgets before invoking the parser.
2. Pin/record adapter, parser, model and dependency versions/digests.
3. Enforce scope and report partial page failures without dropping successful pages.
4. Persist full raw output and emit a bounded neutral envelope.
5. Declare coordinate origin/unit/rotation and provide a tested transform.
6. Preserve original parser IDs and confidence semantics; never invent confidence.
7. Sanitize parser metadata and paths before returning.
8. Map errors and cancellation to the shared taxonomy.
9. Emit page/phase timings, peak RAM/VRAM when measurable and batch statistics.
10. Pass common contract tests using CPU-safe fixtures or recorded raw outputs; GPU is not required for normalization tests.

Adapters shall not:

- mutate global process configuration per request;
- download models at request time in production;
- write outside assigned scratch/artifact handles;
- call external APIs unless the adapter and tenant policy explicitly authorize a remote capability;
- return Markdown as the only structured result;
- accept `HTTP 200` as proof of complete parse.

## 8. Contract test suite

The shared suite is executed against every adapter:

- descriptor schema, truthful capability/scope matrix and option rejection;
- deterministic request idempotency and immutable raw artifact digest;
- one-, multi-page and empty-page result cardinality;
- rotated page and clipped-region coordinate transform round trip;
- tables with row/column spans and unsupported-feature disclosure;
- partial page failure, timeout, OOM, malformed output, cancellation and crash mapping;
- no result outside requested page/region tolerance;
- bounded metadata and no secrets/absolute host paths;
- confidence `null` and semantic declaration when unavailable;
- parser upgrade recorded without leaking its schema beyond adapter fixtures.

## 9. Parser research snapshot

All findings are a dated snapshot, not a permanent architecture dependency. Official source links are provided so the selection can be refreshed before implementation/promotion.

| Candidate evaluated | Version snapshot | Official evidence and licensing |
|---|---|---|
| MinerU | stable `3.4.5` (2026-08-14); 4.0 alpha excluded | [PyPI release/history](https://pypi.org/project/mineru/), [official repository](https://github.com/opendatalab/MinerU), [custom MinerU license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md). License is based on Apache 2.0 but adds commercial thresholds and online-service attribution; legal review required. |
| PaddleOCR / PaddleOCR-VL | PaddleOCR `3.7.0`; PaddleOCR-VL `1.6`, 0.9B | [PyPI](https://pypi.org/project/paddleocr/), [official repository](https://github.com/PaddlePaddle/PaddleOCR), [PaddleOCR-VL 1.6 documentation](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.en.md), [deployment matrix](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md), [Apache-2.0 model card](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6). |
| Docling | stable `2.123.0` (2026-08-26) | [PyPI release history](https://pypi.org/project/docling/), [official repository](https://github.com/docling-project/docling), [official model catalog](https://github.com/docling-project/docling/blob/main/docs/usage/model_catalog.md). Code is MIT; each selected model/OCR engine requires separate license inventory. |
| Marker | stable `2.0.0` (2026-07-20) | [PyPI](https://pypi.org/project/marker-pdf/), [official repository](https://github.com/datalab-to/marker). Code is Apache-2.0, but official README states model weights use a modified OpenRAIL license with funding/revenue conditions; legal approval required. |
| Surya | stable `0.22.1` (2026-07-20) | [PyPI](https://pypi.org/project/surya-ocr/), [official repository](https://github.com/datalab-to/surya), [official model card](https://huggingface.co/datalab-to/surya-ocr-2). Code is Apache-2.0; model weights use modified OpenRAIL terms. Surya 2 introduced breaking API/output changes, so adapter pinning is essential. |

### 9.1 Capability comparison

Ratings are architecture-screening judgments from documented features, not local quality results. `Benchmark` means no responsible choice can be made without the project corpus.

| Dimension | MinerU 3.4.5 | PaddleOCR-VL 1.6 | Docling 2.123.0 | Marker 2.0.0 | Surya 0.22.1 |
|---|---|---|---|---|---|
| Text OCR | Strong pipeline/VLM options | Strong, OCR-focused | Pluggable engine; quality varies | Strong via Surya/native text | Strong core focus |
| Layout | Strong | Strong | Strong dedicated models | Strong | Strong core focus |
| Tables | Strong; cross-page claimed | Strong; spans/cross-page claimed | Strong TableFormer; cross-page needs validation | Strong; benchmark | Row/column recognition; full logical table assembly is consumer work |
| Equations | Supported | Strong documented formula recognition | Optional code/formula stage | Supported | LaTeX OCR supported |
| Figures/charts | Image/chart parsing available by effort/backend | Figure/chart/seal support documented | Detection/classification; description optional | Figure extraction/description options | Layout detection; not full semantic asset model |
| Reading order | Supported | Leading official benchmark claim; verify locally | Structured reading order | Supported | Dedicated capability |
| Chinese | Strong, multilingual OCR | Primary strength incl. rare Chinese claims | Depends on OCR backend/config | Via multilingual Surya; benchmark | 90+ language claim; benchmark Chinese slice |
| English | Strong | Strong | Strong | Strong | Strong |
| Scanned PDF | Strong with OCR/VLM | Strong, robust-distortion focus | Supported with configured OCR | Supported | Strong OCR component |
| Born-digital PDF | Strong native/hybrid | Good but raster/VLM path may cost more | Primary strength through native PDF extraction | Strong native text path | Component-level; separate PDF assembly needed |
| Region fallback fit | Adapter/render crop feasible | Good image/crop fit | Page/crop wrapper feasible | Feasible but heavyweight | Good component fit |
| Cross-page structure | Explicit recent support | Explicit recent support | Must benchmark/normalize | Must benchmark | Not end-to-end |

### 9.2 Runtime, operations and maintainability

| Dimension | MinerU | PaddleOCR-VL | Docling | Marker | Surya |
|---|---|---|---|---|---|
| GPU requirement | Backend-dependent; CPU/GPU documented | CPU possible; NVIDIA GPU recommended for service throughput | CPU/CUDA/MPS/XPU depending stage | GPU vLLM or CPU/Apple llama.cpp for Surya 2 | GPU vLLM or CPU/Apple llama.cpp |
| VRAM | Backend/model/effort dependent; benchmark required | 0.9B model, but end-to-end peak must be measured | Model-stage dependent; can run CPU | Official historical numbers are not portable to v2; benchmark required | 1.4 GB weight file is not peak VRAM; benchmark required |
| Latency/throughput | Hybrid effort trade-off; benchmark | Batching/service paths documented; benchmark | Good born-digital expectation; benchmark | Benchmark | Official GPU throughput exists on RTX 5090 but not portable |
| Batch support | Yes by backend | Yes, high-performance service options | Pipeline support; worker batching to verify | CLI/service patterns | Batch image calls |
| Code/model license | Custom code license + dependency/model review | Apache-2.0 code and model card; dependency review | MIT code; per-model review | Apache code, conditional model weights | Apache code, conditional model weights |
| Community/maintenance | High activity, rapid releases | Large mature project, active | LF AI project, very active/frequent releases | Active, one principal maintainer on PyPI | Active; recent major rewrite |
| Maintainability | Medium: multiple backends and custom license | Medium: broad stack/hardware matrix | High for modular standard pipeline | Medium-low for enterprise default due weight terms/version shift | Medium as focused component; not full parser |
| API stability | Medium-low; frequent releases and 4.0 alpha | Medium; pin pipeline/model separately | Medium; frequent releases require pin/contract tests | Low-medium after 2.0 major | Low-medium after Surya 2 breaking schema |

No VRAM, latency or throughput value is accepted into capacity planning until measured on the reference GPU with pinned Docker/model digests, page DPI and corpus slices. Vendor-reported benchmarks are recorded only as hypotheses.

### 9.3 Known limitations and validation focus

- **MinerU:** custom license conditions and fast-moving backends/releases; the chosen pipeline/hybrid/VLM backend is part of the capability identity. Validate source packaging because PyPI 3.4.5 exposes no source distribution for that release.
- **PaddleOCR-VL:** deployment matrix is broad but backend-specific; official guidance warns vLLM on compute capability 7.x can timeout/OOM. Validate native-text fidelity/cost on born-digital pages and exact region/table output structure.
- **Docling:** OCR quality depends on the configured engine/languages and each model has its own license. Cross-page tables, difficult financial grids, Chinese scans and optional formula stage require local gates rather than assuming the rich `DoclingDocument` is correct.
- **Marker:** official package documentation flags forms as a weakness and recommends optional LLM/forced OCR for some failures; the core platform will not enable external LLM repair by default. Model-weight terms are conditional.
- **Surya:** it is a component toolkit rather than a complete document-structure assembler, and Surya 2 introduced breaking API/output changes. An [open official issue](https://github.com/datalab-to/surya/issues/544) documents silent text loss inside `Diagram` regions in 0.22.1; any future adapter must add figure-text coverage tests.

## 10. Recommendation

### MVP primary candidate: Docling standard PDF pipeline

Reasoning:

- Best architectural fit for mostly born-digital PDFs: native PDF extraction, structured elements, layout, reading order and tables without requiring a VLM for every normal page.
- MIT codebase, active LF AI governance and modular model/OCR selection reduce—but do not eliminate—licensing and replacement risk.
- CPU-capable stages preserve degraded operation when GPU is unavailable and leave the single GPU available for expensive fallback.
- Its rich private `DoclingDocument` remains strictly inside the adapter; Canonical IR is still ours.

Primary configuration must explicitly enable/choose OCR for scanned pages and table accurate mode according to preflight/routing policy. Formula enrichment is optional and capability-declared; it must not silently activate an external LLM.

#### Phase 2.5 implemented development profile (2026-09-01)

`docling-standard` pins Docling `2.123.0` and adapter `0.1.0`. It enables RapidOCR with the Chinese `ch` profile on the Torch backend, accurate TableFormer structure with cell matching, and local code/formula enrichment. Remote services, external plug-ins and picture-description models are disabled. `device=auto` resolves to CUDA only when available and otherwise to CPU; explicit unavailable CUDA is a runtime error, not a document-quality failure. Page/model batch size is one in this correctness-first slice. This profile is development evidence only until model licenses and Golden Dataset thresholds are approved.

#### Phase 2.6 executable-scope and candidate amendment (2026-09-01)

`ParserDescriptor.supported_scopes` describes executable behavior, not an aspirational capability. `docling-standard` and `paddleocr-vl-1.6` currently advertise `DOCUMENT` only because both pinned adapters execute the complete PDF. A router must not schedule either adapter for `PAGE` fallback until true page-limited execution is implemented and contract-tested.

`paddleocr-vl-1.6` pins `paddleocr==3.7.0`, `paddlex==3.7.1`, PaddlePaddle `3.3.0`, adapter `0.1.0`, PP-DocLayoutV3, and PaddleOCR-VL-1.6-0.9B. It integrates the complete layout/crop/order/recognition/assembly pipeline rather than the bare VLM. PaddlePaddle is installed separately from the official device-specific wheel index; CUDA imports and SDK objects remain inside the adapter boundary.

Research date: 2026-09-01. Primary sources: [PaddleX 3.7 PaddleOCR-VL pipeline documentation](https://paddlepaddle.github.io/PaddleX/3.7/en/pipeline_usage/tutorials/ocr_pipelines/PaddleOCR-VL.html), [PaddleOCR-VL-1.6 official model note](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.en.md), and the [PaddleOCR 3.7.0 package release](https://pypi.org/project/paddleocr/3.7.0/). External benchmark claims are recorded only as research context; they do not promote a repository default.

The parser port uses one explicit error policy: a complete call failure before a usable neutral envelope raises `ParserExecutionError` containing one structured `ParserError`; page-local failures remain in a `PARTIAL` `ParseResult.errors`. Adapters do not return an ambiguous empty `FAILED` result and do not expose adapter-specific exception types as a second mechanism.

Paddle `parsing_res_list` is mapped as structured blocks. Table HTML is parsed deterministically into logical cells while preserving `rowspan` and `colspan`; absent cell geometry remains `null`. Markdown is a debug/derived artifact and is not the neutral contract.

### MVP selective fallback candidate: PaddleOCR-VL 1.6

Reasoning:

- Complementary strength on Chinese/English OCR, complex tables/formulas and distorted scanned pages.
- Compact 0.9B architecture and multiple local inference backends support a single-GPU deployment, subject to measured VRAM.
- Apache-2.0 code/model evidence is more enterprise-friendly than candidate stacks with conditional model-weight licenses.
- Page/crop inputs map naturally to page/region/table selective repair.

### Why not the others as the initial default

- **MinerU:** technically strong and retained as the first benchmark challenger, but the custom license adds service attribution/commercial-threshold obligations, and rapid multi-backend/version evolution increases integration surface. It may replace the default after legal approval and Golden Dataset superiority.
- **Marker:** attractive full parser, but conditional weight licensing and the recent v2 runtime/API transition are material enterprise risks. Benchmark after legal approval.
- **Surya:** strong focused OCR/layout/table component and useful future adapter, but not a complete document-structure pipeline; conditional weights and recent breaking schema reduce initial default value.

### Promotion gate

The recommendation is provisional. A candidate becomes production default only when:

1. code, weights and transitive license inventory are approved;
2. sandbox/offline model packaging and SBOM/security scan pass;
3. adapter contract tests pass;
4. protected Golden Dataset has no blocking slice regression;
5. reference-host P95 latency, peak RAM/VRAM and failure behavior meet budgets;
6. fallback complementarity reduces defects rather than merely increasing agreement;
7. a rollback-compatible pinned image/model digest exists.

## 11. Replacement procedure

1. Add adapter + recorded raw fixtures; do not edit domain rules for parser names.
2. Implement normalizer mapping and coordinate/confidence declarations.
3. Pass contract, schema and failure-injection tests.
4. Run shadow benchmark against current default on fixed corpus/hardware.
5. Review quality by protected slice and confidence intervals, plus license/security gates.
6. Record an ADR or ADR amendment, pin exact digests and canary by routing percentage.
7. Preserve old revisions and provide immediate config rollback.
