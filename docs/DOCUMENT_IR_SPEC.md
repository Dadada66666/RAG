# Canonical Document IR Specification

| Field | Value |
|---|---|
| Status | Proposed — highest-priority Phase 1 contract |
| Schema family | `com.acme.docparser.document-ir` |
| Initial schema version | `1.0.0`; current writer version `1.2.0` |
| Serialization | Canonical UTF-8 JSON; generated JSON Schema Draft 2020-12 |
| Last updated | 2026-09-01 |

## 1. Purpose and invariants

Canonical Document IR is the stable boundary between extraction and every business consumer. It is not a lossless copy of any parser schema. Parser raw output remains an immutable artifact linked by provenance.

The following are hard invariants:

1. Pages are complete, ordered, one-based and unique from `1..page_count`.
2. Every spatial entity uses one declared canonical coordinate convention.
3. Every published content entity has at least one resolvable provenance record.
4. Cross-entity references point to existing IDs of the expected type.
5. Reading order is total within a page for flow content; excluded decorative blocks declare why.
6. Confidence is `null` when unknown; it is never fabricated as `1.0`.
7. Parser-private payloads are external artifacts; bounded, namespaced extensions are allowed.
8. A revision is immutable. Repair or migration creates a new revision with lineage.
9. Canonical JSON contains no NaN/Infinity, duplicate keys, implementation-specific objects or binary blobs.

## 2. Schema strategy

### Decision

Use **Pydantic v2 models as the Python authoring and runtime-validation model**, and commit a deterministically generated **JSON Schema Draft 2020-12 as the language-neutral wire contract**. Use small frozen dataclasses only for internal ephemeral algorithm values when validation/serialization is unnecessary.

| Option | Strength | Weakness | Decision |
|---|---|---|---|
| Pydantic | Runtime validation, discriminated unions, JSON Schema generation, FastAPI integration, rich errors | Python dependency; careless coercion can hide errors | Authoritative Python model with strict mode |
| JSON Schema | Language-neutral, contract/compatibility tooling, API validation | Awkward to hand-maintain alongside Python | Generated, reviewed and committed artifact |
| dataclass | Lightweight, stdlib, good for internal values | No built-in validation/versioned wire schema | Not the public IR model |

Rules:

- Models use strict validation and `extra="forbid"` except a single `extensions` field.
- Generated schema diff is reviewed in CI; hand edits to generated schema fail CI.
- Serialization aliases and enums are wire-stable. Python class names may change without changing wire names.
- All timestamps are RFC 3339 UTC with `Z`; all digests are lowercase `sha256:<64-hex>`.

## 3. Core document model

```text
DocumentIR
├── schema_version, document_id, revision_id, revision_number
├── source, metadata, processing
├── pages[]
│   └── blocks[]
├── sections[]
├── tables[] -> table_segments[] -> cells[]
├── figures[]
├── equations[]
├── references[]
├── chunks[]
├── relationships[]
├── provenance[]
└── quality_summary, extensions
```

### 3.1 `DocumentIR`

| Field | Type | Required | Constraints / meaning |
|---|---|:---:|---|
| `schema_version` | semver string | Yes | Wire schema version |
| `document_id` | ID | Yes | Stable for tenant + exact source bytes; opaque externally |
| `revision_id` | ID | Yes | Immutable IR revision identity |
| `revision_number` | integer | Yes | Starts at 0; monotonic within document/pipeline lineage |
| `previous_revision_id` | ID/null | Yes | Direct predecessor, if any |
| `created_at` | timestamp | Yes | Revision creation time; excluded from semantic content hash |
| `source` | `SourceDocument` | Yes | Original-object identity and safe metadata |
| `metadata` | `DocumentMetadata` | Yes | Language/title/author hints; values may include confidence/provenance |
| `processing` | `ProcessingManifest` | Yes | Reproducibility versions and run IDs |
| `page_count` | integer | Yes | `1..1000` by default policy |
| `pages` | array[`Page`] | Yes | Exactly `page_count` entries |
| `sections` | array[`Section`] | Yes | May be empty; forms a forest with synthetic document root implied |
| `tables` | array[`Table`] | Yes | May be empty |
| `figures` | array[`Figure`] | Yes | May be empty |
| `equations` | array[`Equation`] | Yes | May be empty |
| `references` | array[`ReferenceEntry`] | Yes | Bibliographic/cross-reference entities, may be empty |
| `chunks` | array[`Chunk`] | Yes | May be empty before chunking; versioned independently |
| `relationships` | array[`Relationship`] | Yes | Document-wide typed edges |
| `provenance` | array[`ProvenanceRecord`] | Yes | Deduplicated provenance registry |
| `quality_summary` | `QualitySummary` | Yes | Report reference and publication gate outcome |
| `extensions` | object | Yes | Namespaced bounded extension map; default `{}` |

### 3.2 `SourceDocument`

Required: `source_artifact_id`, `sha256`, `media_type`, `size_bytes`, `original_filename_safe`, `ingested_at`. Optional: `source_uri_redacted`, `pdf_version`, `encryption_status`. The original filename is sanitized display metadata, never a storage path. `source_uri_redacted` cannot contain credentials or signed query parameters.

### 3.3 `ProcessingManifest`

Required strings: `pipeline_version`, `normalizer_version`, `validator_ruleset_version`, `merge_version`, `chunker_version`, `renderer_version`, `config_hash`. Required arrays: `parser_runs`, `artifact_ids`. Each `ParserRunSummary` contains:

- `parser_run_id`, `adapter_id`, `adapter_version`, `parser_name`, `parser_version`;
- `model_ids[]` with immutable model name, revision/digest and license approval ID;
- `capabilities_used[]`, `scope`, `started_at`, `ended_at`, `device_class`;
- `determinism` (`DETERMINISTIC`, `BEST_EFFORT`, `NONDETERMINISTIC`) and bounded runtime metadata.

No secret, hostname, raw command line or user text belongs in this structure.

### 3.4 `QualitySummary` lifecycle

`QualitySummary` describes the validator lifecycle, not whether parser execution returned without an exception. Its status is one of `NOT_EVALUATED`, `PASS`, `DEGRADED`, or `FAIL`.

- Before the Quality Validator runs, the only valid representation is `status=NOT_EVALUATED`, `publishable=false`, `score=null`, and `quality_report_id=null`.
- Evaluated statuses require a resolvable `quality_report_id`. `score` is either `null` when the
  validator uses the discrete `score_model=NONE` policy, or a calibrated value in `[0,1]` when a
  separately versioned score model exists; publication policy decides `publishable`.
- Normalizers and parser adapters must never emit `PASS` or `score=1.0` merely to make an IR validate.
- `issue_counts` may be zero in `NOT_EVALUATED`; this means no quality rules have executed, not that the document has no defects.

## 4. Pages and blocks

### 4.1 `Page`

| Field | Type | Required | Notes |
|---|---|:---:|---|
| `page_id` | ID | Yes | Derived deterministic entity ID |
| `page_number` | integer | Yes | One-based |
| `width` / `height` | decimal number | Yes | Canonical points after effective rotation |
| `rotation_applied` | enum | Yes | `0`, `90`, `180`, `270`; transform into canonical space |
| `media_box_original` / `crop_box_original` | bbox/null | Yes | Original PDF coordinate-space facts |
| `blocks` | array[`Block`] | Yes | Includes excluded decorative blocks |
| `page_metadata` | object | Yes | Scan/text-density/render facts; bounded |
| `provenance_ids` | array[ID] | Yes | At least one |
| `extensions` | object | Yes | Namespaced |

### 4.2 `Block`

| Field | Type | Required | Notes |
|---|---|:---:|---|
| `block_id` | ID | Yes | Unique in document revision |
| `block_type` | `BlockType` | Yes | Stable canonical enum |
| `page_number` | integer | Yes | Must match containing page |
| `bbox` | bbox | Yes | Canonical page coordinates |
| `polygon` | array[point]/null | Yes | Optional tighter geometry, non-self-intersecting |
| `reading_order` | integer/null | Yes | Contiguous `0..n-1` for included flow blocks |
| `reading_order_status` | enum | Yes | `IN_FLOW`, `DECORATIVE`, `UNRESOLVED` |
| `text` | string/null | Yes | NFC Unicode; null for purely visual blocks |
| `text_spans` | array[`TextSpan`] | No | Default `[]`; character-level style/language/geometry/provenance |
| `text_direction` | enum | Yes | `LTR`, `RTL`, `TTB`, `MIXED`, `UNKNOWN` |
| `language` | string/null | Yes | BCP 47 hint |
| `confidence` | number/null | Yes | Calibrated `[0,1]`, unknown is null |
| `confidence_source` | enum/null | Yes | `PARSER`, `CALIBRATED`, `DERIVED` |
| `parent_block_id` | ID/null | Yes | Lists/list-items or nested structures only |
| `relationship_ids` | array[ID] | Yes | Typed edges in registry |
| `provenance_ids` | array[ID] | Yes | At least one |
| `content_ref` | ID/null | Yes | Table/figure/equation entity ID where applicable |
| `style` | `TextStyle`/null | Yes | Bounded normalized hints, not presentation fidelity |
| `extensions` | object | Yes | Namespaced |

`text` preserves semantic characters and paragraph boundaries; it is not lowercased, dehyphenated or whitespace-collapsed destructively. Search-normalized text is derived later. If dehyphenation occurs, provenance contains source character spans and transformation reason.

`TextSpan` has `start`, `end` (Unicode code-point offsets into `text`, half-open), optional `bbox`/`style`/BCP 47 `language`, and non-empty `provenance_ids`. When spans are present they are ordered, non-overlapping and within text length; gaps are allowed only for explicitly unstyled text. Span bboxes use canonical page space. Text spans enable precise mixed-language/style highlighting without fragmenting reading-order blocks.

An optional `semantic_fingerprint` may be computed from versioned normalized type/text/quantized geometry for diffing and cache hints. It is not an ID, uniqueness guarantee, authorization key or cross-version citation key.

### 4.3 `BlockType`

Initial closed enum:

```text
TITLE, HEADING, PARAGRAPH, LIST, LIST_ITEM, TABLE, FIGURE,
FIGURE_CAPTION, EQUATION, CODE, QUOTE, FOOTNOTE, HEADER, FOOTER,
PAGE_NUMBER, UNKNOWN
```

New semantic types are additive minor-version changes only when old readers may safely map them through an explicitly shipped compatibility map to `UNKNOWN`. Renaming/removing a value requires a major version.

## 5. Coordinate convention

Canonical page space is mandatory:

- origin `(0, 0)` is the top-left of the **effective CropBox after page rotation**;
- x increases right, y increases down;
- unit is PDF point (`1/72 inch`), stored as JSON number rounded to at most 4 decimals;
- bbox order is `[x0, y0, x1, y1]`, half-open for algorithmic intersection, with `0 <= x0 < x1 <= page.width` and `0 <= y0 < y1 <= page.height`;
- polygons use `[[x,y], ...]` in canonical space and must lie within the page tolerance;
- page numbers are one-based; array indexes are not external identifiers.

The normalizer records an affine transform from parser/raster source coordinates into canonical page space in each provenance record:

```text
x_canonical = a*x_source + c*y_source + e
y_canonical = b*x_source + d*y_source + f
transform = [a, b, c, d, e, f]
```

Raster crop/padding/DPI, PDF bottom-left coordinates and rotated page coordinates are therefore reversible. A transform round-trip error greater than `0.25 pt` or `0.1%` of page dimension (whichever is larger) is invalid.

## 6. Sections and relationships

### 6.1 `Section`

Required: `section_id`, `level` (`1..12`), `heading_block_id` (nullable only for synthetic sections), `parent_section_id`, `child_section_ids`, `content_block_ids`, `page_start`, `page_end`, `provenance_ids`. `heading_path` is derived, not persisted as competing truth. Section ranges must be nested or disjoint; partial overlap is invalid.

### 6.2 `Relationship`

```text
Relationship {
  relationship_id,
  type,
  source_id,
  target_id,
  confidence,
  provenance_ids,
  metadata,
  extensions
}
```

Initial types:

```text
CONTAINS, CAPTION_OF, CONTINUES_ON, FOOTNOTE_OF, REFERENCES,
READING_NEXT, DERIVED_FROM, SUPERSEDES, ALTERNATIVE_TO
```

`READING_NEXT` must agree with per-page `reading_order`; cross-page flow is represented only where confidently known. Relationships may connect different entity kinds according to a documented compatibility table enforced by schema/domain validators.

## 7. Tables

`Block(TABLE).content_ref` points to a `Table`. A logical cross-page table has one `table_id` and one or more page-local `TableSegment`s.

### 7.1 `Table`

Required: `table_id`, `logical_row_count`, `logical_column_count`, `segments`, `cells`, `caption_block_ids`, `header_row_indices`, `provenance_ids`, `confidence`, `extensions`.

### 7.2 `TableSegment`

Required: `segment_id`, `page_number`, `bbox`, `block_id`, `row_start`, `row_end_exclusive`, `provenance_ids`. Optional `continued_from_segment_id` / `continues_to_segment_id`. Segment row ranges refer to the logical table after repeated header reconciliation.

### 7.3 `TableCell`

| Field | Type | Required | Rules |
|---|---|:---:|---|
| `cell_id` | ID | Yes | Unique within revision |
| `row_index`, `column_index` | integer | Yes | Zero-based logical anchor |
| `row_span`, `column_span` | integer | Yes | >= 1 and within table dimensions |
| `text` | string | Yes | Empty allowed only when structurally intentional |
| `is_header` | boolean | Yes | Semantic hint |
| `page_number` | integer | Yes | Anchor page |
| `bbox` | bbox/null | Yes | Null only for inferred logical cells, with provenance |
| `source_block_ids` | array[ID] | Yes | May include OCR/text blocks subsumed by table |
| `confidence` | number/null | Yes | Unknown is null |
| `provenance_ids` | array[ID] | Yes | At least one |

Optional `fragments[]` records visual pieces of one logical cell as `{segment_id, page_number, bbox, provenance_ids}`. When present, every fragment belongs to a table segment and the cell's `page_number`/`bbox` are only its anchor convenience fields. This represents cross-page/visually split cells without inventing one bbox across pages.

Each fragment bbox must lie within or meaningfully overlap its referenced segment bbox in the same canonical page coordinate space. The V1.1 runtime permits a deterministic `0.25 pt` boundary tolerance for numeric rounding; disjoint fragments are invalid.

Cell occupied grids cannot overlap unless an explicit `ALTERNATIVE_TO` conflict entity is retained in a non-published candidate revision. Repeated headers across pages are represented once in logical rows and may preserve repeated visual cells via segment provenance.

## 8. Figures, equations and references

### `Figure`

Required: `figure_id`, `block_ids`, `caption_block_ids`, `page_numbers`, `asset_artifact_ids`, `provenance_ids`, `confidence`, `extensions`. Extracted images are artifact references, never base64 in IR. OCR text inside a figure remains a child/related block; caption text is not folded destructively into figure metadata.

### `Equation`

Required: `equation_id`, `block_id`, `text`, `format` (`LATEX`, `MATHML`, `PLAIN`, `UNKNOWN`), `label`, `provenance_ids`, `confidence`, `extensions`. The surrounding paragraph is related through `REFERENCES` or adjacency, not copied into the equation.

### `ReferenceEntry`

Required: `reference_id`, `label`, `raw_text`, `field_values`, `source_block_ids`, `provenance_ids`, `confidence`, `extensions`. Structured bibliographic fields are optional and must not replace `raw_text`.

## 9. Provenance-first model

### 9.1 `ProvenanceRecord`

| Field | Type | Required | Meaning |
|---|---|:---:|---|
| `provenance_id` | ID | Yes | Registry ID |
| `document_id` | ID | Yes | Must match document |
| `source_artifact_id` | ID | Yes | Original/render/raw output artifact |
| `page_number` | integer/null | Yes | Null only for document-level metadata |
| `bbox` | bbox/null | Yes | Canonical coordinates where spatial |
| `source_coordinate_space` | string/null | Yes | E.g. parser pixels or PDF user space |
| `source_bbox` | bbox/null | Yes | Original coordinates when available |
| `to_canonical_transform` | six numbers/null | Yes | Affine transform |
| `parser_run_id` | ID/null | Yes | Null for source-only/preflight facts |
| `source_parser` / `parser_version` | string/null | Yes | Explicit parser identity |
| `extraction_method` | enum | Yes | See below |
| `original_object_id` | string/null | Yes | Parser-local block/cell ID, bounded |
| `confidence` | number/null | Yes | Source confidence, not merged confidence |
| `char_range` | `[start,end]`/null | Yes | Range in source text artifact where applicable |
| `parent_provenance_ids` | array[ID] | Yes | Lineage chain |
| `operation` | string/null | Yes | Normalization/merge/chunk transform code |

Extraction methods:

```text
PDF_TEXT, OCR, LAYOUT_MODEL, TABLE_MODEL, FORMULA_MODEL, VLM,
DETERMINISTIC_INFERENCE, FALLBACK_REPLACEMENT, HUMAN_ANNOTATION, IMPORTED
```

The required trace is:

```text
chunk -> source_block_ids -> block.provenance_ids -> page/bbox
      -> source_artifact_id -> original PDF digest
      -> parser_run_id -> adapter/parser/model/config versions
```

The resolver must detect broken chains during validation. Provenance is append-only across a revision lineage: fallback adds records and `SUPERSEDES` relations; it does not rewrite history.

## 10. Confidence semantics

- Raw parser confidence and calibrated canonical confidence are different fields/sources.
- Adapter declarations document whether confidence is token-, block-, detector- or heuristic-level.
- Canonical `confidence` is comparable only after a versioned calibrator. Without calibration it may remain parser-local and is labeled `PARSER`.
- Merged confidence is not `max(primary, fallback)`. It is derived from calibrated confidence, validator evidence and agreement, with `confidence_source=DERIVED` and provenance.
- Threshold decisions must not treat `null` as zero or one; rules explicitly define missing-confidence behavior.

## 11. ID generation and stability

IDs are opaque lowercase strings with type prefixes and UUIDv5/UUIDv7 payloads; consumers must not parse their components.

| ID | Generation | Stability guarantee |
|---|---|---|
| `document_id` | UUIDv5 over server namespace + tenant ID + source SHA-256 | Stable for exact bytes within tenant; hash not exposed directly |
| `revision_id` | UUIDv7 at commit | Stable and immutable for that revision |
| `page_id` | UUIDv5 over document ID + page number | Stable across revisions of same source |
| primary entity IDs | UUIDv5 over revision lineage seed + kind + page + parser origin ID, else quantized geometry + ordinal | Stable within a normalized primary revision; no promise across pipeline major versions |
| replacement entity ID | Preserve baseline ID only for validated one-to-one semantic replacement; otherwise new deterministic merge-operation ID | Makes one-to-one citations stable; split/merge remains explicit |
| `chunk_id` | UUIDv5 over document ID + IR revision ID + chunker version/config hash + ordered source block IDs | Changes when source/revision/policy changes |

Quantized geometry is a last resort and includes deterministic page reading ordinal to avoid collisions. IDs are never assigned by array index alone. All collisions are hard normalization errors.

## 12. Extensions and parser metadata

`extensions` keys must be reverse-DNS or approved namespace strings such as `org.docling.layout_label` or `com.baidu.paddleocr.seal_type`. Values must be JSON, depth <= 5, <= 64 keys/entity, <= 16 KiB/entity and <= 1 MiB/document by default.

Allowed: bounded labels, model scores, source semantic hints needed for diagnosis. Forbidden: full raw parser output, image bytes, huge token arrays/logprobs, secrets, executable content or fields that duplicate canonical truth. Full raw output is referenced as an artifact in `ProcessingManifest`/provenance.

An extension cannot override or reinterpret canonical page, type, order, text, geometry, relationship or provenance semantics. If consumers need such a field for business logic, it must be promoted through a schema change rather than treated as a competing truth.

Unknown unnamespaced fields are rejected. Readers may preserve unknown namespaced extensions without interpreting them.

## 13. Serialization and canonical hashing

- Media type: `application/vnd.docparser.document-ir+json;version=1`.
- UTF-8, NFC strings, JSON booleans/null, no comments, no trailing commas.
- Canonical semantic digest uses RFC 8785-style deterministic JSON serialization after excluding `created_at` and explicitly declared operational fields.
- Large IR may additionally export JSONL shards, but the manifest and logical model are identical. Shard boundaries never change IDs.
- The sharded packaging profile has a versioned manifest containing schema/revision/document IDs, semantic digest, ordered shard descriptors (`kind`, page/range, artifact ID, digest, count) and a global-reference index. It must reassemble to the same logical instance and semantic digest as monolithic JSON. Consumers declare packaging support separately from schema support.
- Decimal coordinates serialize as JSON numbers with bounded precision; calculations use Decimal or stable rounding at the boundary.
- Compression is storage concern and declared by artifact metadata.

## 14. Versioning and migration policy

SemVer applies to the wire contract:

- **Patch:** documentation/constraint clarification that does not change accepted/rejected instances.
- **Minor:** additive optional/defaulted fields or enum additions with a shipped downgrade mapping.
- **Major:** removed/renamed fields, changed meanings/coordinates/requiredness, incompatible enum or ID behavior.

Compatibility rules:

1. Writers emit exactly one current version and never a mixture.
2. Readers support current major and at least the previous two minor versions during V1.
3. Migrations are pure, deterministic, idempotent functions with golden fixtures and provenance operation `SCHEMA_MIGRATION`.
4. Migration never invents confidence or geometry. Unrepresentable values become explicit `UNKNOWN`/null with an issue record.
5. Original revisions remain immutable. Migrated IR is a new artifact/revision linked to the source revision.
6. CI runs backward-read, forward-preserve-extension, round-trip and schema-diff tests.

Current compatibility note (2026-09-02): V1.2 permits evaluated summaries to retain
`score=null` for the discrete Quality Gate while still requiring `quality_report_id`. V1.1
semantics remain unchanged as a historical contract. Writers emit `1.2.0`; readers
deterministically migrate supported V1.0/V1.1 payloads to V1.2 without inventing or deleting
quality evidence. The schema remains in the V1 family path.

## 15. Complete JSON example

This example is intentionally small but contains every top-level entity family and a fallback replacement lineage.

```json
{
  "schema_version": "1.2.0",
  "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022",
  "revision_id": "rev_019d4020-0f42-7cc8-a37d-3f13e915d955",
  "revision_number": 1,
  "previous_revision_id": "rev_019d401c-aad0-7b50-a31c-c097c7595520",
  "created_at": "2026-08-28T08:30:00Z",
  "source": {
    "source_artifact_id": "art_019d4018-8c5d-7219-9a82-a15209455b53",
    "sha256": "sha256:84d89877f0d4041efb6bf91a16f0248f2fd573e6af05c19f96bedf3c4d0f1234",
    "media_type": "application/pdf",
    "size_bytes": 243120,
    "original_filename_safe": "bilingual-report.pdf",
    "source_uri_redacted": null,
    "pdf_version": "1.7",
    "encryption_status": "NOT_ENCRYPTED",
    "ingested_at": "2026-08-28T08:20:00Z"
  },
  "metadata": {
    "title": "2025 年度报告 / Annual Report 2025",
    "authors": ["Example Group"],
    "languages": ["zh-Hans", "en"],
    "created_date": null,
    "custom": {}
  },
  "processing": {
    "pipeline_version": "1.0.0",
    "normalizer_version": "1.0.0",
    "validator_ruleset_version": "1.0.0",
    "merge_version": "1.0.0",
    "chunker_version": "1.0.0",
    "renderer_version": "pdfium-7.1.0",
    "config_hash": "sha256:19b9e49342dd0a457b2802bd2eef6a81af3183cce2d90cd51c78e65dd45a5678",
    "parser_runs": [
      {
        "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b",
        "adapter_id": "docling",
        "adapter_version": "0.1.0",
        "parser_name": "docling",
        "parser_version": "2.123.0",
        "model_ids": [{"name": "docling-layout-heron", "revision": "8f39ad3", "digest": null, "license_approval_id": "lic-2026-014"}],
        "capabilities_used": ["PDF_TEXT", "LAYOUT", "TABLE", "READING_ORDER"],
        "scope": {"kind": "DOCUMENT", "page_numbers": [1, 2], "bbox": null},
        "started_at": "2026-08-28T08:21:00Z",
        "ended_at": "2026-08-28T08:22:10Z",
        "device_class": "cuda:sm_89",
        "determinism": "BEST_EFFORT",
        "runtime": {"precision": "fp16"}
      },
      {
        "parser_run_id": "prun_019d401f-614f-7bf9-8b44-30ac26b982ba",
        "adapter_id": "paddleocr_vl",
        "adapter_version": "0.1.0",
        "parser_name": "PaddleOCR-VL",
        "parser_version": "PaddleOCR-3.7.0/PaddleOCR-VL-1.6",
        "model_ids": [{"name": "PaddleOCR-VL-1.6-0.9B", "revision": "88dcf7d", "digest": null, "license_approval_id": "lic-2026-015"}],
        "capabilities_used": ["OCR", "TABLE"],
        "scope": {"kind": "REGION", "page_numbers": [2], "bbox": [42.0, 180.0, 553.0, 410.0]},
        "started_at": "2026-08-28T08:23:00Z",
        "ended_at": "2026-08-28T08:23:08Z",
        "device_class": "cuda:sm_89",
        "determinism": "BEST_EFFORT",
        "runtime": {"precision": "bf16", "temperature": 0}
      }
    ],
    "artifact_ids": [
      "art_019d4018-8c5d-7219-9a82-a15209455b53",
      "art_019d401b-2393-7aa0-aeb7-383824330016",
      "art_019d401f-dc90-74b8-9f58-8fb0ec5de72e"
    ]
  },
  "page_count": 2,
  "pages": [
    {
      "page_id": "page_41b4c9bf-bc12-5a14-8b2d-90764050b3d8",
      "page_number": 1,
      "width": 595.276,
      "height": 841.89,
      "rotation_applied": 0,
      "media_box_original": [0.0, 0.0, 595.276, 841.89],
      "crop_box_original": [0.0, 0.0, 595.276, 841.89],
      "blocks": [
        {
          "block_id": "blk_b4e9a11b-4922-55a7-946b-0c97fed92c22",
          "block_type": "TITLE",
          "page_number": 1,
          "bbox": [54.0, 64.0, 541.0, 101.0],
          "polygon": null,
          "reading_order": 0,
          "reading_order_status": "IN_FLOW",
          "text": "2025 年度报告 / Annual Report 2025",
          "text_direction": "LTR",
          "language": "zh-Hans",
          "confidence": 0.98,
          "confidence_source": "CALIBRATED",
          "parent_block_id": null,
          "relationship_ids": [],
          "provenance_ids": ["prov_a6fae653-74fd-5411-8f5f-9e9ce3cc1458"],
          "content_ref": null,
          "style": {"font_size_pt": 22.0, "bold": true, "italic": false, "monospace": false},
          "extensions": {"org.docling.layout_label": "TITLE"}
        },
        {
          "block_id": "blk_9d012b44-bd97-54f9-bc43-3a0f7120981f",
          "block_type": "FIGURE",
          "page_number": 1,
          "bbox": [72.0, 160.0, 523.0, 410.0],
          "polygon": null,
          "reading_order": 1,
          "reading_order_status": "IN_FLOW",
          "text": null,
          "text_direction": "UNKNOWN",
          "language": null,
          "confidence": 0.92,
          "confidence_source": "PARSER",
          "parent_block_id": null,
          "relationship_ids": ["rel_5dcb0968-0c8e-5126-b6d3-11dd947547f4"],
          "provenance_ids": ["prov_426bc604-346e-5748-aa14-d46bbb519fd6"],
          "content_ref": "fig_765167f0-882a-5754-912e-3758757b078c",
          "style": null,
          "extensions": {}
        },
        {
          "block_id": "blk_5d581c5d-ae63-5510-8976-6ab3ea3658fd",
          "block_type": "FIGURE_CAPTION",
          "page_number": 1,
          "bbox": [90.0, 418.0, 505.0, 440.0],
          "polygon": null,
          "reading_order": 2,
          "reading_order_status": "IN_FLOW",
          "text": "图 1：年度收入 / Figure 1: Annual revenue",
          "text_direction": "LTR",
          "language": "zh-Hans",
          "confidence": 0.96,
          "confidence_source": "CALIBRATED",
          "parent_block_id": null,
          "relationship_ids": ["rel_5dcb0968-0c8e-5126-b6d3-11dd947547f4"],
          "provenance_ids": ["prov_2438f099-a8a5-5362-ac77-d22446422a42"],
          "content_ref": null,
          "style": null,
          "extensions": {}
        }
      ],
      "page_metadata": {"document_type": "BORN_DIGITAL", "text_density": 0.22, "image_area_ratio": 0.36},
      "provenance_ids": ["prov_31fb7c0b-38ed-52f8-97aa-18124b51b56a"],
      "extensions": {}
    },
    {
      "page_id": "page_068036f7-b0a0-590e-a20a-bbea09ac89e0",
      "page_number": 2,
      "width": 595.276,
      "height": 841.89,
      "rotation_applied": 0,
      "media_box_original": [0.0, 0.0, 595.276, 841.89],
      "crop_box_original": [0.0, 0.0, 595.276, 841.89],
      "blocks": [
        {
          "block_id": "blk_bdf8ae64-ce95-5e2c-9444-28a378ec60da",
          "block_type": "HEADING",
          "page_number": 2,
          "bbox": [48.0, 64.0, 360.0, 90.0],
          "polygon": null,
          "reading_order": 0,
          "reading_order_status": "IN_FLOW",
          "text": "1. 财务摘要 / Financial Summary",
          "text_direction": "LTR",
          "language": "zh-Hans",
          "confidence": 0.97,
          "confidence_source": "CALIBRATED",
          "parent_block_id": null,
          "relationship_ids": [],
          "provenance_ids": ["prov_2d4afea1-5841-5c57-90b4-010c90bd5879"],
          "content_ref": null,
          "style": {"font_size_pt": 16.0, "bold": true, "italic": false, "monospace": false},
          "extensions": {}
        },
        {
          "block_id": "blk_be2e01a3-4f74-5629-a31e-0c576d63539e",
          "block_type": "PARAGRAPH",
          "page_number": 2,
          "bbox": [48.0, 108.0, 547.0, 156.0],
          "polygon": null,
          "reading_order": 1,
          "reading_order_status": "IN_FLOW",
          "text": "本年度收入持续增长。Revenue continued to grow during the year.",
          "text_direction": "LTR",
          "language": "zh-Hans",
          "confidence": 0.95,
          "confidence_source": "CALIBRATED",
          "parent_block_id": null,
          "relationship_ids": ["rel_78e49a74-8246-578f-b6c7-d06751f6766f"],
          "provenance_ids": ["prov_94fc0727-c1c0-566c-8c13-b5d411954c31"],
          "content_ref": null,
          "style": null,
          "extensions": {}
        },
        {
          "block_id": "blk_9d91b824-c9ef-5ea7-922d-58074423c88d",
          "block_type": "TABLE",
          "page_number": 2,
          "bbox": [42.0, 180.0, 553.0, 410.0],
          "polygon": null,
          "reading_order": 2,
          "reading_order_status": "IN_FLOW",
          "text": null,
          "text_direction": "LTR",
          "language": "zh-Hans",
          "confidence": 0.93,
          "confidence_source": "DERIVED",
          "parent_block_id": null,
          "relationship_ids": ["rel_78e49a74-8246-578f-b6c7-d06751f6766f", "rel_ef08211e-937e-5dd1-805e-e47c19304d43"],
          "provenance_ids": ["prov_451c74e2-9f7e-57bc-8180-c9d70c7410a8", "prov_30d78852-df48-55f7-8245-84ab0e4d4951"],
          "content_ref": "tbl_c229ad35-964d-5ed4-ae8a-ab46f5007526",
          "style": null,
          "extensions": {"com.baidu.paddleocr.source_label": "table"}
        },
        {
          "block_id": "blk_cc9a0d60-4cc9-5b8d-89a3-e506dc888259",
          "block_type": "EQUATION",
          "page_number": 2,
          "bbox": [150.0, 450.0, 445.0, 490.0],
          "polygon": null,
          "reading_order": 3,
          "reading_order_status": "IN_FLOW",
          "text": "R = P \\times Q",
          "text_direction": "LTR",
          "language": null,
          "confidence": 0.89,
          "confidence_source": "PARSER",
          "parent_block_id": null,
          "relationship_ids": [],
          "provenance_ids": ["prov_0ad69b58-66b8-5501-8a76-17a0c593464b"],
          "content_ref": "eq_026fc7a4-9841-5c70-9b64-fc5027b85611",
          "style": null,
          "extensions": {}
        },
        {
          "block_id": "blk_04c7653b-cf5e-5735-9fe4-4fcc292f5487",
          "block_type": "FOOTNOTE",
          "page_number": 2,
          "bbox": [48.0, 760.0, 547.0, 790.0],
          "polygon": null,
          "reading_order": 4,
          "reading_order_status": "IN_FLOW",
          "text": "注：金额单位为人民币百万元。",
          "text_direction": "LTR",
          "language": "zh-Hans",
          "confidence": 0.91,
          "confidence_source": "CALIBRATED",
          "parent_block_id": null,
          "relationship_ids": ["rel_ef08211e-937e-5dd1-805e-e47c19304d43"],
          "provenance_ids": ["prov_59fbbf4d-9b26-5a9e-947d-ae5d20b5618c"],
          "content_ref": null,
          "style": null,
          "extensions": {}
        }
      ],
      "page_metadata": {"document_type": "MIXED", "text_density": 0.31, "image_area_ratio": 0.18},
      "provenance_ids": ["prov_70a75cfb-e8fc-5ca6-a290-0ef5d71a2868"],
      "extensions": {}
    }
  ],
  "sections": [
    {
      "section_id": "sec_10c50a39-7662-52eb-81df-5bda1ae1750b",
      "level": 1,
      "heading_block_id": "blk_bdf8ae64-ce95-5e2c-9444-28a378ec60da",
      "parent_section_id": null,
      "child_section_ids": [],
      "content_block_ids": ["blk_be2e01a3-4f74-5629-a31e-0c576d63539e", "blk_9d91b824-c9ef-5ea7-922d-58074423c88d", "blk_cc9a0d60-4cc9-5b8d-89a3-e506dc888259", "blk_04c7653b-cf5e-5735-9fe4-4fcc292f5487"],
      "page_start": 2,
      "page_end": 2,
      "provenance_ids": ["prov_2d4afea1-5841-5c57-90b4-010c90bd5879"],
      "extensions": {}
    }
  ],
  "tables": [
    {
      "table_id": "tbl_c229ad35-964d-5ed4-ae8a-ab46f5007526",
      "logical_row_count": 3,
      "logical_column_count": 2,
      "segments": [{"segment_id": "tseg_e8063d9d-b43b-5ebd-b410-20a9b50afc7d", "page_number": 2, "bbox": [42.0, 180.0, 553.0, 410.0], "block_id": "blk_9d91b824-c9ef-5ea7-922d-58074423c88d", "row_start": 0, "row_end_exclusive": 3, "continued_from_segment_id": null, "continues_to_segment_id": null, "provenance_ids": ["prov_30d78852-df48-55f7-8245-84ab0e4d4951"]}],
      "cells": [
        {"cell_id": "cell_410f68e8-566e-5db4-9218-018f4ff34b4e", "row_index": 0, "column_index": 0, "row_span": 1, "column_span": 1, "text": "指标 / Metric", "is_header": true, "page_number": 2, "bbox": [42.0, 180.0, 290.0, 230.0], "source_block_ids": [], "confidence": 0.96, "provenance_ids": ["prov_30d78852-df48-55f7-8245-84ab0e4d4951"]},
        {"cell_id": "cell_e02c23f6-bc32-5395-909c-9ea1a78e105c", "row_index": 0, "column_index": 1, "row_span": 1, "column_span": 1, "text": "2025", "is_header": true, "page_number": 2, "bbox": [290.0, 180.0, 553.0, 230.0], "source_block_ids": [], "confidence": 0.97, "provenance_ids": ["prov_30d78852-df48-55f7-8245-84ab0e4d4951"]},
        {"cell_id": "cell_24d0aabb-d0d6-53fa-a5d6-f95acb4f64b7", "row_index": 1, "column_index": 0, "row_span": 2, "column_span": 1, "text": "收入 / Revenue", "is_header": false, "page_number": 2, "bbox": [42.0, 230.0, 290.0, 410.0], "source_block_ids": [], "confidence": 0.92, "provenance_ids": ["prov_30d78852-df48-55f7-8245-84ab0e4d4951"]},
        {"cell_id": "cell_b8e95272-cc77-5392-95c9-207060460eea", "row_index": 1, "column_index": 1, "row_span": 1, "column_span": 1, "text": "120", "is_header": false, "page_number": 2, "bbox": [290.0, 230.0, 553.0, 320.0], "source_block_ids": [], "confidence": 0.94, "provenance_ids": ["prov_30d78852-df48-55f7-8245-84ab0e4d4951"]},
        {"cell_id": "cell_8e434970-c605-5308-a892-34df11da07b2", "row_index": 2, "column_index": 1, "row_span": 1, "column_span": 1, "text": "128", "is_header": false, "page_number": 2, "bbox": [290.0, 320.0, 553.0, 410.0], "source_block_ids": [], "confidence": 0.93, "provenance_ids": ["prov_30d78852-df48-55f7-8245-84ab0e4d4951"]}
      ],
      "caption_block_ids": [],
      "header_row_indices": [0],
      "provenance_ids": ["prov_451c74e2-9f7e-57bc-8180-c9d70c7410a8", "prov_30d78852-df48-55f7-8245-84ab0e4d4951"],
      "confidence": 0.93,
      "extensions": {}
    }
  ],
  "figures": [{"figure_id": "fig_765167f0-882a-5754-912e-3758757b078c", "block_ids": ["blk_9d012b44-bd97-54f9-bc43-3a0f7120981f"], "caption_block_ids": ["blk_5d581c5d-ae63-5510-8976-6ab3ea3658fd"], "page_numbers": [1], "asset_artifact_ids": ["art_019d401b-2393-7aa0-aeb7-383824330016"], "provenance_ids": ["prov_426bc604-346e-5748-aa14-d46bbb519fd6"], "confidence": 0.92, "extensions": {}}],
  "equations": [{"equation_id": "eq_026fc7a4-9841-5c70-9b64-fc5027b85611", "block_id": "blk_cc9a0d60-4cc9-5b8d-89a3-e506dc888259", "text": "R = P \\times Q", "format": "LATEX", "label": null, "provenance_ids": ["prov_0ad69b58-66b8-5501-8a76-17a0c593464b"], "confidence": 0.89, "extensions": {}}],
  "references": [{"reference_id": "ref_a4881558-6855-5e24-998f-6c8ada18e2d4", "label": "[1]", "raw_text": "Example Group. 2025 Annual Report.", "field_values": {"year": "2025", "title": "Annual Report"}, "source_block_ids": ["blk_be2e01a3-4f74-5629-a31e-0c576d63539e"], "provenance_ids": ["prov_94fc0727-c1c0-566c-8c13-b5d411954c31"], "confidence": 0.71, "extensions": {}}],
  "chunks": [
    {
      "chunk_id": "chk_7cefc72e-da45-5df1-b97c-ea072793faaa",
      "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022",
      "ir_revision_id": "rev_019d4020-0f42-7cc8-a37d-3f13e915d955",
      "chunk_schema_version": "1.0.0",
      "chunker_version": "1.0.0",
      "chunk_config_hash": "sha256:d5c8ec0fd4010af3d1502fb6088ff86e03be27cf9815a169d030155eb5708845",
      "chunk_type": "PARENT",
      "parent_chunk_id": null,
      "text": "1. 财务摘要 / Financial Summary\n本年度收入持续增长。Revenue continued to grow during the year.",
      "parent_section_id": "sec_10c50a39-7662-52eb-81df-5bda1ae1750b",
      "heading_path": ["1. 财务摘要 / Financial Summary"],
      "page_start": 2,
      "page_end": 2,
      "source_block_ids": ["blk_bdf8ae64-ce95-5e2c-9444-28a378ec60da", "blk_be2e01a3-4f74-5629-a31e-0c576d63539e"],
      "source_entity_ids": ["sec_10c50a39-7662-52eb-81df-5bda1ae1750b"],
      "bboxes": [{"page_number": 2, "bbox": [48.0, 64.0, 547.0, 156.0]}],
      "content_types": ["HEADING", "PARAGRAPH"],
      "token_count": 31,
      "tokenizer_id": "example-tokenizer@sha256:8f2a34d6c5b8e10895a21f9bb45f658b24f3a10d084f7bd6cde31b4a214b006e",
      "content_digest": "sha256:9e2f993f90dc9e2858d7f724c5a539a3b6fcd0f686ccc349d06cb1975cf67c5a",
      "embedding_input_digest": "sha256:9e2f993f90dc9e2858d7f724c5a539a3b6fcd0f686ccc349d06cb1975cf67c5a",
      "embedding_eligible": true,
      "sparse_eligible": true,
      "metadata": {},
      "provenance_ids": ["prov_9f88cff1-d8b8-537d-ad28-bb89319800b1"]
    }
  ],
  "relationships": [
    {"relationship_id": "rel_1c903a18-4871-5c05-ae5e-006db5e36f27", "type": "CONTAINS", "source_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "target_id": "sec_10c50a39-7662-52eb-81df-5bda1ae1750b", "confidence": 1.0, "provenance_ids": ["prov_2d4afea1-5841-5c57-90b4-010c90bd5879"], "metadata": {}, "extensions": {}},
    {"relationship_id": "rel_5dcb0968-0c8e-5126-b6d3-11dd947547f4", "type": "CAPTION_OF", "source_id": "blk_5d581c5d-ae63-5510-8976-6ab3ea3658fd", "target_id": "fig_765167f0-882a-5754-912e-3758757b078c", "confidence": 0.96, "provenance_ids": ["prov_2438f099-a8a5-5362-ac77-d22446422a42"], "metadata": {}, "extensions": {}},
    {"relationship_id": "rel_78e49a74-8246-578f-b6c7-d06751f6766f", "type": "READING_NEXT", "source_id": "blk_be2e01a3-4f74-5629-a31e-0c576d63539e", "target_id": "blk_9d91b824-c9ef-5ea7-922d-58074423c88d", "confidence": 0.97, "provenance_ids": ["prov_94fc0727-c1c0-566c-8c13-b5d411954c31"], "metadata": {}, "extensions": {}},
    {"relationship_id": "rel_ef08211e-937e-5dd1-805e-e47c19304d43", "type": "FOOTNOTE_OF", "source_id": "blk_04c7653b-cf5e-5735-9fe4-4fcc292f5487", "target_id": "tbl_c229ad35-964d-5ed4-ae8a-ab46f5007526", "confidence": 0.82, "provenance_ids": ["prov_59fbbf4d-9b26-5a9e-947d-ae5d20b5618c"], "metadata": {}, "extensions": {}}
  ],
  "provenance": [
    {"provenance_id": "prov_31fb7c0b-38ed-52f8-97aa-18124b51b56a", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d4018-8c5d-7219-9a82-a15209455b53", "page_number": 1, "bbox": [0.0, 0.0, 595.276, 841.89], "source_coordinate_space": "PDF_USER_SPACE", "source_bbox": [0.0, 0.0, 595.276, 841.89], "to_canonical_transform": [1.0, 0.0, 0.0, -1.0, 0.0, 841.89], "parser_run_id": null, "source_parser": null, "parser_version": null, "extraction_method": "IMPORTED", "original_object_id": "page:1", "confidence": null, "char_range": null, "parent_provenance_ids": [], "operation": "PAGE_CANONICALIZATION"},
    {"provenance_id": "prov_70a75cfb-e8fc-5ca6-a290-0ef5d71a2868", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d4018-8c5d-7219-9a82-a15209455b53", "page_number": 2, "bbox": [0.0, 0.0, 595.276, 841.89], "source_coordinate_space": "PDF_USER_SPACE", "source_bbox": [0.0, 0.0, 595.276, 841.89], "to_canonical_transform": [1.0, 0.0, 0.0, -1.0, 0.0, 841.89], "parser_run_id": null, "source_parser": null, "parser_version": null, "extraction_method": "IMPORTED", "original_object_id": "page:2", "confidence": null, "char_range": null, "parent_provenance_ids": [], "operation": "PAGE_CANONICALIZATION"},
    {"provenance_id": "prov_a6fae653-74fd-5411-8f5f-9e9ce3cc1458", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 1, "bbox": [54.0, 64.0, 541.0, 101.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [54.0, 64.0, 541.0, 101.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "LAYOUT_MODEL", "original_object_id": "#/texts/0", "confidence": 0.98, "char_range": [0, 36], "parent_provenance_ids": [], "operation": "NORMALIZE_BLOCK"},
    {"provenance_id": "prov_426bc604-346e-5748-aa14-d46bbb519fd6", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 1, "bbox": [72.0, 160.0, 523.0, 410.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [72.0, 160.0, 523.0, 410.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "LAYOUT_MODEL", "original_object_id": "#/pictures/0", "confidence": 0.92, "char_range": null, "parent_provenance_ids": [], "operation": "NORMALIZE_FIGURE"},
    {"provenance_id": "prov_2438f099-a8a5-5362-ac77-d22446422a42", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 1, "bbox": [90.0, 418.0, 505.0, 440.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [90.0, 418.0, 505.0, 440.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "PDF_TEXT", "original_object_id": "#/texts/1", "confidence": 0.96, "char_range": [0, 34], "parent_provenance_ids": [], "operation": "NORMALIZE_BLOCK"},
    {"provenance_id": "prov_2d4afea1-5841-5c57-90b4-010c90bd5879", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 2, "bbox": [48.0, 64.0, 360.0, 90.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [48.0, 64.0, 360.0, 90.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "LAYOUT_MODEL", "original_object_id": "#/texts/2", "confidence": 0.97, "char_range": [0, 29], "parent_provenance_ids": [], "operation": "NORMALIZE_SECTION"},
    {"provenance_id": "prov_94fc0727-c1c0-566c-8c13-b5d411954c31", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 2, "bbox": [48.0, 108.0, 547.0, 156.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [48.0, 108.0, 547.0, 156.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "PDF_TEXT", "original_object_id": "#/texts/3", "confidence": 0.95, "char_range": [0, 61], "parent_provenance_ids": [], "operation": "NORMALIZE_BLOCK"},
    {"provenance_id": "prov_451c74e2-9f7e-57bc-8180-c9d70c7410a8", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 2, "bbox": [42.0, 180.0, 553.0, 410.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [42.0, 180.0, 553.0, 410.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "TABLE_MODEL", "original_object_id": "#/tables/0", "confidence": 0.54, "char_range": null, "parent_provenance_ids": [], "operation": "SUPERSEDED_TABLE_CANDIDATE"},
    {"provenance_id": "prov_30d78852-df48-55f7-8245-84ab0e4d4951", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401f-dc90-74b8-9f58-8fb0ec5de72e", "page_number": 2, "bbox": [42.0, 180.0, 553.0, 410.0], "source_coordinate_space": "CROP_PIXELS_200DPI", "source_bbox": [0.0, 0.0, 1419.4, 638.9], "to_canonical_transform": [0.36, 0.0, 0.0, 0.36, 42.0, 180.0], "parser_run_id": "prun_019d401f-614f-7bf9-8b44-30ac26b982ba", "source_parser": "PaddleOCR-VL", "parser_version": "PaddleOCR-3.7.0/PaddleOCR-VL-1.6", "extraction_method": "FALLBACK_REPLACEMENT", "original_object_id": "table:0", "confidence": 0.93, "char_range": null, "parent_provenance_ids": ["prov_451c74e2-9f7e-57bc-8180-c9d70c7410a8"], "operation": "REPLACE_ONE_TO_ONE"},
    {"provenance_id": "prov_0ad69b58-66b8-5501-8a76-17a0c593464b", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 2, "bbox": [150.0, 450.0, 445.0, 490.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [150.0, 450.0, 445.0, 490.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "FORMULA_MODEL", "original_object_id": "#/texts/4", "confidence": 0.89, "char_range": [0, 13], "parent_provenance_ids": [], "operation": "NORMALIZE_EQUATION"},
    {"provenance_id": "prov_59fbbf4d-9b26-5a9e-947d-ae5d20b5618c", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 2, "bbox": [48.0, 760.0, 547.0, 790.0], "source_coordinate_space": "DOCLING_TOPLEFT_POINTS", "source_bbox": [48.0, 760.0, 547.0, 790.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": "prun_019d4019-facf-78fd-a3d2-fb081128ac1b", "source_parser": "docling", "parser_version": "2.123.0", "extraction_method": "PDF_TEXT", "original_object_id": "#/texts/5", "confidence": 0.91, "char_range": [0, 15], "parent_provenance_ids": [], "operation": "NORMALIZE_FOOTNOTE"},
    {"provenance_id": "prov_9f88cff1-d8b8-537d-ad28-bb89319800b1", "document_id": "doc_6f5030ec-48ab-5b86-8729-7a4f59ace022", "source_artifact_id": "art_019d401b-2393-7aa0-aeb7-383824330016", "page_number": 2, "bbox": [48.0, 64.0, 547.0, 156.0], "source_coordinate_space": "CANONICAL_PAGE_POINTS", "source_bbox": [48.0, 64.0, 547.0, 156.0], "to_canonical_transform": [1.0, 0.0, 0.0, 1.0, 0.0, 0.0], "parser_run_id": null, "source_parser": null, "parser_version": null, "extraction_method": "DETERMINISTIC_INFERENCE", "original_object_id": null, "confidence": null, "char_range": null, "parent_provenance_ids": ["prov_2d4afea1-5841-5c57-90b4-010c90bd5879", "prov_94fc0727-c1c0-566c-8c13-b5d411954c31"], "operation": "CHUNK_ASSEMBLY"}
  ],
  "quality_summary": {
    "quality_report_id": "qrep_019d4020-78a0-713f-8651-5662021ad3ac",
    "score": 0.91,
    "status": "PASS",
    "issue_counts": {"INFO": 1, "WARNING": 0, "ERROR": 0, "CRITICAL": 0},
    "publishable": true
  },
  "extensions": {}
}
```

## 16. Validation and tests

- JSON Schema positive/negative fixtures for every entity and union.
- Referential integrity, page cardinality, reading order and section/table grid property tests.
- Coordinate transform round trips across PDF/raster rotations/crops.
- Unicode NFC and deterministic canonical-hash fixtures for Chinese/English/mixed content.
- Migration golden tests for every supported source version.
- Provenance graph reachability: every published entity resolves to source artifact and, when parser-derived, parser run.
- ID determinism/collision tests and one-to-one replacement identity tests.
- Extension size/depth/namespace rejection tests.

## 17. Design rationale and known trade-offs

- A normalized top-left point space matches viewers and image models while retaining reversible PDF transforms.
- Central provenance avoids repeating large records but requires referential checks; this is preferable to inconsistent embedded copies.
- Logical cross-page tables preserve semantic continuity while page segments preserve citation geometry.
- Strict closed canonical enums make downstream behavior predictable; `UNKNOWN` and namespaced extensions absorb parser novelty until a deliberate schema change.
- IDs are not promised stable across semantic re-parses because false stability would be worse than explicit revision identity. Source and page identity remain stable; derived identities encode their inputs.
- Full raw output is external to prevent vendor schema churn and unbounded payloads from destabilizing the contract.
