# Evidence-grounded PDF QA implementation

Status: implemented and offline-verified on 2026-09-12. Real BGE-M3 / SiliconFlow validation remains
pending on the user's server, which holds the model and document assets. This is a bounded
engineering change, not a claim of improved retrieval or answer accuracy. The existing
Fixed/Structure A/B and its held-out questions remain unchanged.

## Objective

Make usable source evidence survive known local structure failures, then answer from a budgeted,
traceable context. Fixed 512/64 remains the retrieval default. Structure assists context construction;
it does not become a new ranking signal.

## Work plan

1. Add explicit recovery normalization for missing pages, invalid logical table grids, and text whose
   structural type is unknown. Preserve the original ParseResult before normalization. Keep strict
   normalization available for parser evaluation. Never invent table topology or missing-page text.
2. Build context from Fixed source-token intervals. Merge overlapping intervals, retain disjoint
   portions of a long block, restore bounded complete blocks/tables and explicit context, and record
   omissions. Citations disclose block/table-region precision rather than invented cell precision.
3. Persist an exact dense index so queries do not reparse or re-embed documents. Validate the model,
   tokenizer and stored inputs when loading. Add a reusable QA session and command-line entry points.
4. Connect an explicitly configured chat-completions model. Require claim-level evidence IDs and
   exact source quotations; reject invalid references instead of silently accepting them. Quote
   validation is not semantic entailment verification.
5. Exercise known failure cases, citation rejection, token budgets, cross-page sources, overlap,
   index reload and model transport with offline tests. Run the existing regression checks. Record
   separately which real parser/model evaluations could actually be executed.

## Acceptance and boundaries

- A bad table does not erase unaffected text. Missing pages stay visible as missing.
- Recovery does not manufacture parser confidence, header roles, reading order or table relations.
- Existing valid-input Fixed embedding text and retrieval ranks remain unchanged.
- Context uses a declared tokenizer budget; the generator's complete prompt limit is a separate
  runtime constraint. Large incomplete table excerpts are explicitly identified.
- Source quotes must occur in the actual submitted evidence. Page/bbox references resolve to the
  document artifact. Correct references alone do not certify factual correctness.
- No reranker, hybrid retrieval, vector database, new parser, universal structure inference, or
  tuning against OHR holdout questions.
- Real answer accuracy, citation support, coverage and latency still require a frozen model and
  independently checked answers. Offline mocks are contract tests, not quality benchmarks.

## Delivered implementation

| Plan item | Executable entry point |
|---|---|
| Explicit local recovery and raw evidence retention | `parse-local --recover-structure`; `normalization/recovery.py` |
| Source interval context with bounded expansion | `retrieval/context.py` |
| Reusable persisted exact dense index | `rag-index`; `retrieval/index.py` |
| SiliconFlow answers with source quotations | `rag-ask`; `retrieval/answering.py`; `application/qa.py` |
| Independent answer and citation evaluation | `rag-evaluate`; `evaluation/qa.py` |

See [server commands, experiment boundaries and limitations](EVIDENCE_QA_GUIDE.md).

## Verification recorded

This section records the earlier foundation increment. The subsequent M1 scope/batch increment is
specified in [COMPLEX_DOCUMENT_QA_SPEC.md](COMPLEX_DOCUMENT_QA_SPEC.md), with 463 offline tests passing
and 152 files passing Mypy. At the M1 checkpoint, table-row restoration and numeric semantic verification
were not implemented; the subsequent M2 increment is recorded below.

- Full default offline suite: **452 passed, 1 skipped, 10 deselected**. Cache writing was disabled
  for the local Windows environment (`pytest -p no:cacheprovider`); test selection and coverage
  requirements were unchanged.
- Existing configured IR/quality/fallback/robust coverage: **86.41%**, above the 80% gate. This is
  that configured module subset, not a claim of 86.41% coverage for the new QA modules.
- `ruff check .`: passed. All 12 new Python source/test files pass `ruff format --check`.
- `mypy`: passed, 150 source files. Canonical `schema check`: passed; no wire schema change.
- New CLI index / ask / context-only / evaluation paths tested offline. Targeted checks cover
  source interval overlap, disjoint spans, exact budgets, local table failures, missing pages,
  explicit cross-page headers, source references, invalid citations and provider request shape.
- No real PDF content was sent to a remote model. BGE and SiliconFlow integration has an opt-in
  synthetic server smoke test; it was not run locally. DEV-21 metrics were not rerun and the
  untouched 79 queries were not read or used.

## M2 increment: table evidence recovery

`--table-context` enables logical-row restoration after unchanged Fixed retrieval. The new
`retrieval/table_context.py` aligns the entire rendered source against real tokenizer offsets;
`context.py` restores closed rowspan bands, explicit column headers and explicit caption/footnote
conditions. Connected continuation sources retain separate page regions. Unknown axes and incomplete
restorations remain visible. No Canonical schema change or inferred caption/header relation is introduced.

The 1.1 index persists row maps and alignment counts. Old indexes can still load, with an explicit
missing-map fallback. M1 remains the default; both strategies can run on the same new index. New tests
cover Unicode and multiline cells, repeated values, non-compositional token boundaries, row/column spans,
cross-page anchors/citations, missing maps/conditions, budget exhaustion, and unchanged embeddings/ranks.

The optional `test_table_context_offsets.py` checks the server's actual local BGE tokenizer without
network access or loading model weights. It was not run on this machine. Real M1 baseline and M2
answer/citation quality remain pending. See the active spec's milestone table for final offline counts,
and [guide section 8](EVIDENCE_QA_GUIDE.md#8-m2同一检索结果上的表格证据恢复) for server validation.

Final M2 offline verification: **480 passed, 2 skipped, 10 deselected**; Mypy **155 files**;
Ruff, format checks for the 9 touched M2 Python files, and Canonical schema check passed.
