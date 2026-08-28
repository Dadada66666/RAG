# Open Questions and Decision Deadlines

These questions do not invalidate Phase 1. Defaults allow early implementation, but the listed deadline prevents late architectural surprise.

| ID | Question | Current safe assumption | Decision deadline / owner |
|---|---|---|---|
| OQ-01 | What is the reference NVIDIA GPU and available VRAM? | No co-residency assumption; Docling primary may use CPU, Paddle fallback serialized on GPU. | Before Phase 7 real adapter capacity acceptance; platform owner |
| OQ-02 | What percentage is born-digital vs scanned, and which table/document families dominate? | Equal protected attention to zh/en/mixed, born/scanned and financial/scientific slices. | Before Golden Dataset sampling and default promotion; product/data owner |
| OQ-03 | Is the organization/service within MinerU/Marker/Surya weight-license thresholds and attribution policy? | These candidates are not production defaults without written approval. | Before installing/evaluating conditional-license weights in shared infrastructure; legal/security |
| OQ-04 | Is MVP single-tenant/trusted internal, or mutually hostile multi-tenant? | Logical tenant isolation; not certified for hostile multi-tenancy. | Before service exposure beyond trusted network; security owner |
| OQ-05 | Retention/deletion/legal-hold requirements for original/raw/canonical/chunks/backups? | Conservative retention classes, two-phase deletion and no cross-tenant deduplication. | Before Phase 3 storage production configuration; compliance/data owner |
| OQ-06 | May `PARTIAL` output be indexed, and for which issue types? | Deny-by-default; only explicit downstream policy can activate disclosed PARTIAL manifests. | Before Phase 12 downstream integration; RAG/product owner |
| OQ-07 | Which tokenizer/embedding models and hard context limits will downstream use? | Chunker supports pinned tokenizer profiles; example 600/800/1000 tokens is provisional. | Before Phase 12 acceptance; RAG owner |
| OQ-08 | Who owns Golden Dataset annotation/adjudication and protected-test access? | No parser promotion without an approved owner and immutable dataset release. | Before Phase 9 threshold freeze / Phase 15; data/QA owner |
| OQ-09 | Which authentication/identity provider and tenant claims will service mode use? | API defines authorization scopes but authentication adapter is deployment-specific. | Before Phase 13 external API exposure; platform/security owner |
| OQ-10 | What are target volume, latency SLO and storage budget at 10/100/1000/10000 documents? | MVP targets are engineering baselines; backpressure uses page-equivalents. | Before Phase 13 load acceptance and Phase 16 trigger; product/platform owner |
| OQ-11 | Are figures/charts required as searchable multimodal embeddings in V1? | Preserve assets/captions/OCR/provenance; no generated description or multimodal retrieval engine. | Before Phase 12 policy freeze; product/RAG owner |
| OQ-12 | Which PDF renderer is approved and how are its native vulnerabilities patched? | Versioned PDFium-compatible adapter in sandbox, replaceable by port. | Before Phase 6 production admission; security/platform owner |

Any answer that changes trust boundary, supported source formats, production parser license, Canonical coordinate semantics or job durability requires an ADR amendment/new ADR. Configuration-only capacity choices do not.

