# Open Questions and Decision Deadlines

Resolved architecture policy is not repeated here. Every open answer must be recorded in a versioned
manifest, configuration or ADR when it changes a public, wire or trust contract.

| ID | Question | Current safe assumption | Decision deadline / owner |
|---|---|---|---|
| OQ-01 | Who owns corpus licensing, annotation, adjudication and protected-holdout access? | No parser/Quality Gate promotion without named owner and immutable release. | Before Next 1 dataset freeze; product/data/legal |
| OQ-02 | Which exact ParseBench dataset/evaluator commit and redistribution terms are approved? | Use upstream evaluator through a pinned adapter; do not commit unapproved PDFs. | Before `parsebench-complex-v1`; legal/evaluation owner |
| OQ-03 | Which development and protected holdout IDs satisfy strata without family leakage? | Deterministic selection before parser results; fixed seed/IDs/digest. | Before parser baseline; evaluation owner |
| OQ-04 | What predicate defines supported-slice correctness for gate calibration? | Hard integrity plus slice-specific text/table/numeric/order requirements; no global perfect-document claim. | Before Next 2 labels; product/data owner |
| OQ-05 | Is the IR V1 minor relaxation for `score=null` with `score_model=NONE` approved? | Do not fabricate a score; remain `NOT_EVALUATED` until schema/migration approval. | Before Quality Gate implementation; architecture owner |
| OQ-06 | What holdout sample size/confidence requirement applies to accepted precision and coverage? | Report uncertainty; insufficient samples cannot establish 95%. | Before threshold freeze; evaluation/product owner |
| OQ-07 | Can adapters execute true page/table scope? | Capabilities describe actual executable behavior; no fake selectivity. | Before Next 3; parser owner |
| OQ-08 | What is the reference GPU/VRAM and latency budget? | No co-residency/scheduler assumption; record actual device. | Before parser/embedding/reranker runs; platform owner |
| OQ-09 | Which tokenizer and embedding immutable revision/profile is approved? | Evaluate multilingual candidate; no default before benchmark/license review. | Before Next 4/5; RAG/legal owner |
| OQ-10 | Which Chinese/English sparse analyzer and query normalization profile is baseline? | Pin and compare a mature Unicode/CJK analyzer; preserve numbers/units. | Before Next 6; RAG owner |
| OQ-11 | What candidate/final top-k and generation context budget are supported? | Retrieval values are provisional experiment settings. | Before retrieval freeze; RAG/product owner |
| OQ-12 | Who owns curated QA/relevance/citation truth and unanswerable policy? | No RAG claim without adjudicated queries and source IDs. | Before Next 5/8; product/data owner |
| OQ-13 | May `PARTIAL`/`DEGRADED` output enter the index? | Deny by default; only `ACCEPT/PASS` is publishable. | Before chunk indexing; RAG/product owner |
| OQ-14 | Are chart/figure questions required in first retrieval evaluation? | Preserve assets/captions/provenance; multimodal embeddings deferred. | Before QA corpus freeze; product owner |
| OQ-15 | What tenant, retention and hostile-file boundary applies to service mode? | Logical isolation; no hostile multi-tenant certification or external API yet. | Before storage/API work; security/compliance |

No current open question blocks this specification package. It blocks only the listed implementation
or promotion deadline. Capacity choices are configuration; trust boundary, Canonical semantics,
public metric meaning and wire compatibility require an ADR amendment or explicit existing-ADR update.

