# ADR 0004 — Peer-corpus indexing strategy

- **Status:** Accepted
- **Date:** 2026-04-24
- **Decider:** Robert Colling

## Context

The peer-comparison retriever has to answer questions like "show me how
five firms in the $1B-$10B AUM band describe Item 5 (Fees and
Compensation)." Several decisions stack here: chunk granularity, vector
schema, point identity, payload shape, dense-vs-hybrid scope, and how
operators curate the corpus.

The brief calls for hybrid dense + BM25 + RRF + cross-encoder rerank.
Landing all four on Day 5 would either be shallow (each one half-built)
or push the segmenter / extractor work into next week. We pick the
narrower scope that still makes the rest of the pipeline buildable.

## Decision

### 1. Granularity: one vector per Item per brochure.

Each Part 2A brochure produces up to 18 stored vectors — one per Item
section detected by `HeuristicSegmenter`. Item-level chunks match the
operator question ("how do peers describe Item 5"), avoid the
chunking-strategy rabbit hole, and keep the corpus small (~10K vectors
per 600 brochures × 18 items × ~92% completeness).

Sub-Item chunking (paragraph or sliding-window) becomes interesting only
when an extractor needs sub-section retrieval. None do as of week 1.

### 2. Embedding model: `BAAI/bge-small-en-v1.5` (384-dim).

Cheap to host on-prem (~130 MB), strong on regulatory-text retrieval per
the brief, and 384-dim keeps Qdrant footprint small. The query-side
instruction prefix (`"Represent this sentence for searching relevant
passages: "`) is applied automatically by `BgeSmallEnEmbedder.embed_query`.

### 3. Named vectors from day one.

Qdrant collection is created with named vectors (`{"dense": ...}`) rather
than a default unnamed vector. Adding sparse vectors (`{"sparse": ...}`)
and a hybrid query in week 2 is a no-migration extension. Single-vector
collections force a recreate-and-resync; we pay the small ergonomic cost
once.

### 4. Point IDs are deterministic.

`uuid5(NAMESPACE, "<crd>/<brochure_version_id>/<item_number>")`. Reseeding
the same fixtures is upsert-in-place; no duplicates, no need for a
clean-collection step. The namespace is a fixed UUID checked into source
— do not change it once production data is seeded.

### 5. Payload is queryable, not opaque.

Stored payload: `crd`, `brochure_version_id`, `item_number`, `item_title`,
`section_sha256`, `brochure_sha256`, `char_start`, `char_end`, `aum_band`,
`main_office_state`, `indexed_at`. Each filter field gets a Qdrant
payload index. Body text is *not* in the payload — too large to store
twice; the canonical body lives in the cached PDF + segmented JSON.

### 6. Operator-curated peer list, JSON-backed.

`data/peers/<file>.json` holds `[{"crd": ..., "aum_band": ..., ...}]`.
The `seed-peers` CLI runs the LangGraph pipeline per CRD and writes the
sections. Auto-discovery from the IARD bulk CSV (filter by AUM band +
strategy tag) lands in week 3 once the IARD loader is wired into the
peer pipeline; for week 1, the explicit list is auditable and fast.

### 7. Skip placeholder sections.

Sections marked `is_placeholder` (Item body reads "Not applicable" or
similar) aren't embedded — they'd cluster in vector space without
adding signal. Their absence is observable via `count(crd) < 18`.

### 8. Hybrid retrieval is week 2, not now.

Dense-only this week. Sparse vectors (BM25-style via Qdrant's
`SparseVector` API), RRF fusion, and a cross-encoder rerank land in
week 2 alongside the extractor nodes that need them. The schema and
named-vectors choice mean none of this requires re-seeding.

## Consequences

- **Idempotent reseeds.** Operators can re-run `seed-peers` after a
  segmenter change without cleanup. Hash-based change detection is
  already in payload (`section_sha256`) for week-2 incremental indexing.
- **Tests don't need torch.** The `Embedder` protocol + `RandomEmbedder`
  test double + lazy sentence-transformers import keep CI fast. Live
  embeddings only run when the production code path is exercised.
- **The corpus is portable.** Qdrant snapshot + the JSON peer list +
  the cached PDFs are everything needed to reconstruct the corpus on a
  fresh machine. No hidden state.
- **First seed run is slow.** sentence-transformers downloads ~130MB on
  first invocation. Acceptable for a one-off; CI never loads the real
  model.
- **No HTTP retrieval endpoint yet.** `PeerStore.query_peers` is callable
  from Python and the CLI. Wrapping it in `GET /peers` is one route
  function and lands when the redline writer needs a request shape.
