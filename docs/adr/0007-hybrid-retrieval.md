# ADR 0007 — Hybrid retrieval: BM25 + RRF + cross-encoder rerank

- **Status:** Accepted
- **Date:** 2026-04-25
- **Decider:** Robert Colling
- **Activates:** the plan in ADR 0004 § 8.
- **Amends:** ADR 0004 § 5 (body text now lives in payload).

## Context

ADR 0004 deferred hybrid retrieval to "week 2 alongside the extractors that
need them." Day 9 lands it before the redline writer (week 3 territory)
forces a retrofit. The brief explicitly promises "dense (bge-small-en-v1.5)
+ sparse (BM25), RRF, cross-encoder rerank" — this ADR locks the choices.

Three sub-decisions stack: how sparse vectors are computed, how dense and
sparse are fused, and where reranking lives. Plus one revision: the
reranker requires document text, which forces a change to the payload
shape from ADR 0004.

## Decision

### 1. Sparse vectors are hashed-vocabulary BM25, computed in Python.

`adv_lens.retrieval.sparse.BM25SparseEncoder` tokenises (lowercase,
word + currency-symbol regex), hashes each unique token to an index in a
65,536-dim space (djb2 hash, no global vocab needed), and stores the
canonical Okapi BM25 doc-side weight (k1=1.5, b=0.75) at that index.

Query-side: each unique token gets the corpus-derived IDF (with a 0.05
floor for OOV terms so the query still ranks something). The encoder
maintains running corpus stats across calls to `encode_documents`, which
is the natural shape for the `seed_peers` flow.

We chose hashed BM25 over a learned sparse model (SPLADE,
`Qdrant/bm25` from FastEmbed) because:
- The brief is checking the **canonical** sparse retrieval baseline,
  not state-of-the-art.
- Hashed vocab means no global pre-training pass; new brochures index
  without re-running anything.
- Deterministic for given input → trivially testable offline (the
  `IdentitySparseEncoder` test double uses the same hash + a TF
  weighting and exercises the same code paths).

Swapping in a learned sparse model later is a one-class change — the
`SparseEncoder` protocol returns `qm.SparseVector(indices, values)`
regardless of how those values were produced.

### 2. Fusion is server-side RRF via Qdrant `Prefetch` + `FusionQuery`.

`PeerStore.query_peers(..., hybrid=True)` issues a single
`client.query_points` with two prefetches (one per named vector,
`limit=settings.hybrid_prefetch_limit=20`), and Qdrant's RRF fusion
returns one ranked list. No client-side rank merging.

RRF over weighted-sum because:
- RRF is parameter-free (no `α` to tune per query family).
- Qdrant supports it natively, no Python-side glue.
- Empirically robust on heterogeneous retrievers — exactly the
  dense/sparse case.

Filters (CRD exclusion, AUM band, item number, state) are applied in
**both** prefetches, not post-fusion, so prefetch quotas aren't burned
on out-of-scope hits.

### 3. Cross-encoder reranking is post-fusion, top-50, in Python.

Default model: `cross-encoder/ms-marco-MiniLM-L-6-v2` (~80 MB, fast on
CPU, trained on web-passage retrieval).
`PeerStore.query_peers(..., rerank=True)` (default) sends the top
`settings.rerank_top_k=50` fused hits through the cross-encoder, replaces
each hit's score with the rerank score, sorts desc, and truncates to `k`.

Reasoning:
- Cross-encoders are ~100× slower than bi-encoders per pair, so
  reranking only the top 50 (not the full corpus) keeps latency bounded.
- Rerank lifts dense+sparse retrieval most when both retrievers agree
  on a candidate set but disagree on order — exactly RRF's output shape.

The reranker is opt-out (`--no-rerank` on the CLI) for diagnostic runs
where you want to see raw fusion order.

### 4. Body text now lives in the payload (revising ADR 0004 § 5).

The reranker scores `(query, document_body)` pairs. Without the body in
payload we'd have to refetch + re-segment the cached PDF on every query
— unworkable.

Footprint cost: ~2 KB per Item × 18 Items × 60 brochures ≈ 2 MB. The
ADR 0004 concern about "too large to store twice" was overstated for
this scale. Production scaling beyond ~10K brochures revisits this
(stream bodies from object storage; keep payload compact).

### 5. Hybrid is opt-in at the store level, on by default in the factory.

`PeerStore(..., sparse_encoder=None, reranker=None)` is dense-only.
`make_peer_store(hybrid=True, rerank=True)` (the default) wires both.
The seed CLI uses `hybrid=True, rerank=False` — sparse vectors must be
written at upsert time, but the cross-encoder isn't needed for indexing.

### 6. Backfilling sparse vectors into an existing collection is not supported in-place.

Qdrant requires sparse vector configuration at collection-creation time.
A collection seeded dense-only must be:
1. Snapshotted (Qdrant snapshot API; payload preserves all fields).
2. Recreated with `sparse_vectors_config={"sparse": SparseVectorParams()}`.
3. Re-upserted from the snapshot (the body field in payload makes this
   a pure recompute — no PDF re-fetch).

Document this in the runbook when production seeding lands. For week-2
synthetic data, blowing away the local collection and reseeding is fine.

## Consequences

- **The redline writer in week 3 has a real peer-context source.** No
  retrofit when it lands.
- **CI tests run hybrid offline.** `IdentitySparseEncoder` +
  `IdentityReranker` doubles cover the full code path without
  loading torch or downloading the rerank model.
- **First production query is slow.** Cross-encoder model downloads
  ~80 MB on first invocation. Acceptable for an interactive use case;
  a warm-up call at startup would mask it if needed.
- **Backfill-in-place isn't supported.** Operators upgrading from a
  pre-hybrid collection follow the snapshot-and-reseed runbook.
- **Body in payload is a real change.** Existing dense-only points
  written before this ADR don't have a `body` field; the
  `_materialize_hits` fallback handles them by emitting the old
  title-and-char-bounds excerpt. Reseeding is the clean path.
- **`Embedder` / `SparseEncoder` / `Reranker` protocols are now the
  three swap points.** Switching to a SPLADE sparse encoder, a domain
  fine-tuned dense encoder, or a regulatory-trained reranker is one
  class change each.
