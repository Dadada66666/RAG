# GPU validation: table localization versus recognition — 2026-09-13

## Scope and reproducibility

Remote main was fast-forwarded from `6957a7e` to `2321c43` with `git pull --ff-only`.
Existing tracked preparation changes were preserved in stash `pre-gpu-validation-20260913`;
conflicting untracked files were moved to `/root/autodl-tmp/pre-pull-untracked.q8tES9`.
No tracked remote changes remain. Existing `hfd.sh` and `time.zip` were left untouched.

Remote artifacts: `/root/autodl-tmp/table-region-validation.5EsDrS`.
Local copy: `.tmp/server-gpu-20260913/table-region-validation.5EsDrS`.
All 71 files listed in the remote SHA-256 manifest were verified after download.
The additional local `verified/manual-grid-check.json` records visual-grid checks below.

Hardware: RTX 3090 24 GB. Runtime: existing `.venv-paddle`, PaddleOCR 3.7.0,
PaddleX 3.7.1, PaddlePaddle GPU 3.3.0. Exact installed versions, PDF digest, commit,
input coordinates and rendering scale are in `verified/manifest.json`.

Only previously used DEV material was read. HOLDOUT-79 was not read. No remote generation
API calls were made. No parser configuration, Canonical IR, retrieval truth or production
algorithm was changed. This is a diagnostic experiment, not an end-to-end accuracy claim.

## Updated-code regression

- Remote tests: **501 passed, 2 skipped, 10 deselected**, 30.46 seconds.
- Ruff passed; Mypy passed for 157 files.
- BGE-M3 CUDA rebuilt and reloaded the index: **88 chunks**, maximum 512 tokens.
- All DEV-21 Top-10 chunk IDs and ordering equal the previous GPU baseline;
  maximum absolute cosine score difference **0.0**.
- LOGICAL_ROWS + caption context stayed within 4096 tokens for every query;
  maximum **4073**. Build/retrieval/context/replay script elapsed **18.98 s**, one run only.
- Saved completion replay: missing `reason` now passes; fabricated ellipsis quote still fails.
  Original completions were reused, so this is not a fresh generation success rate.

The tokenizer warned about an 18121-token source stream while preparing windows. The persisted
embedding chunks are at most 512 tokens; the warning does not establish that an overlength
sequence was sent to BGE. Retrieval completed and matched the previous scores exactly.

## Paired parser experiment

Use the existing Paddle pipeline once per run. Render the original PDF at scale 2; compare whole
page recognition with separate visually selected table crops, using `use_layout_detection=False`
and `prompt_label=table` for the latter. Coordinates were selected from the PDF appearance,
not the query or reference answer. They are manual oracle regions, not an automatic splitter.
Crop coordinates map to original PDF top-left points by `(crop coordinate + origin) / scale`.
That transform was recorded; these image outputs were not installed as Canonical replacements.

| Input | Result | Interpretation |
|---|---|---|
| Page 8, whole page | 3 table regions; Tables 3 and 4 again share bbox `[204,433,627,543]` | Reproduces localization failure; PDF visually contains four tables |
| Table 4, separate crop | 5 rows × 6 columns; all 24 numeric body cells match the PDF | Correct localization helps this table |
| Table 3, separate crop | 4 rows × **3** columns; `o2m o2o` combined, every body row has a single check mark | Correct crop does not recover the two distinct condition columns |
| Table 2, separate crop | 9 × 9 grid, zero rowspans | Group labels remain attached to one row, not materialized across their visual row groups |
| Table 5, separate crop | 5 × 6 grid | Shape preserved; no claim of exhaustive cell adjudication |
| Page 7, whole page | 7 blocks, 1 table | Normal table control executes; count is not cell accuracy |
| Page 1, whole page | 10 blocks, 0 tables | No spurious table in this prose-page control |

Whole pages 8/7 took 41.22/43.52 s after runtime initialization. Four crops took
6.26/1.23/3.89/4.85 s respectively. Crop runs skip localization and cover less content:
these timings do not justify claiming an equivalent full-page speedup.

A single follow-up resolution check re-rendered the same Table 3/4 regions from the PDF at
scale 4, not by enlarging the low-resolution bitmap. Table 3 still produced the same incorrect
three-column HTML. Table 4 still matched all 24 numeric cells. No resolution sweep was performed.

The installed PaddleX `pipeline.py` explicitly excludes `table` from adjacent block merging
(`non_merge_labels=... + ["table"]`). Turning off `merge_layout_blocks` is therefore not an
evidence-backed fix for this observed merged table. The saved layout output already has the
combined box; this experiment does not isolate detector inference from all detector postprocessing.

## Test-harness failure retained

The first run incorrectly passed image results through the PDF adapter's page-index sanitizer.
Image results have `page_index=null`, so all seven cases were marked ERROR after inference.
These are harness errors, not seven model failures. Original `run.py`, log and summary remain.
`run-v2.py` directly saves native image result JSON; all seven runs completed under `verified/`.
Production PDF validation was not relaxed to accommodate this experiment.

## Decision and next changes

1. **Keep the present Fixed/BGE retrieval and caption-context mechanism.** The updated index
   preserves dense ranking. Do not alter chunk size or add ranking machinery to repair missing
   condition columns: the missing information precedes retrieval.
2. **Do not deploy automatic crop-and-replace yet.** Table 4 proves a localization opportunity;
   Table 3 disproves that correct crops alone suffice. A prospective splitter must be evaluated
   on independently selected adjacent-table and single-table negatives, including false splits,
   before it can replace anything. Keep candidate outputs separate from Canonical facts.
3. **Make condition-column and row-group preservation the next parser acceptance test.** The
   existing smoke fixtures do not establish this ability. Build real four-column check-mark
   tables and actual merged row groups with independent numeric values. Require conditions and
   values together, not just a nonempty grid. Do not repair cells from expected answers.
4. **Evaluate an existing alternative only if it can recover these structures.** Docling has an
   adapter in the repository but is not installed in the current server environment; it was not
   tested here. A bounded comparison on these structural classes could justify selective routing.
   Do not implement full-corpus double parsing or promise the alternative will work before measurement.

The present system can execute the tested dense and context pipeline reproducibly. It cannot
yet reliably answer all condition-sensitive table questions: the recognizer can emit a valid,
plausible HTML grid that has already erased the distinction the question requires. IR schema
validation and exact quotation checks cannot prove that this grid matches the original page.
The next useful work is to improve or establish that upstream capability, not to guess the lost
conditions in the normalizer or generation prompt.
