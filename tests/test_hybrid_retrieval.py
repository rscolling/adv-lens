"""Hybrid retrieval tests — BM25 sparse + RRF + reranker, all offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from adv_lens.app.settings import Settings
from adv_lens.retrieval.embeddings import RandomEmbedder
from adv_lens.retrieval.qdrant_store import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    PeerSpec,
    PeerStore,
)
from adv_lens.retrieval.rerank import IdentityReranker
from adv_lens.retrieval.sparse import (
    BM25SparseEncoder,
    IdentitySparseEncoder,
    _hash_token,
    _tokenize,
)
from adv_lens.segmenter.models import ITEM_TITLES, ItemNumber, Section


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        qdrant_collection_peers="advlens_hybrid_test",
        embedding_dim=64,
        sparse_vocab_size=4096,
        hybrid_prefetch_limit=20,
        rerank_top_k=10,
    )


def _section(item: int, body: str) -> Section:
    return Section(
        item_number=ItemNumber(item),
        title=ITEM_TITLES[item],
        body=body,
        char_start=0,
        char_end=len(body),
    )


def _hybrid_store(tmp_path: Path, *, with_reranker: bool = False) -> PeerStore:
    s = _settings(tmp_path)
    return PeerStore(
        QdrantClient(":memory:"),
        RandomEmbedder(dim=s.embedding_dim),
        s,
        sparse_encoder=IdentitySparseEncoder(vocab_size=s.sparse_vocab_size),
        reranker=IdentityReranker() if with_reranker else None,
    )


# ── Tokenizer + hash ──────────────────────────────────────────────────
def test_tokenizer_lowercases_and_keeps_currency_symbols() -> None:
    assert _tokenize("Fees: $1,000 — 1.00% AUM") == ["fees", "$1", "000", "1", "00%", "aum"]


def test_tokenizer_returns_empty_list_for_empty_text() -> None:
    assert _tokenize("") == []
    assert _tokenize("   \n  ") == []


def test_hash_token_is_deterministic_and_in_range() -> None:
    a = _hash_token("fee", 4096)
    b = _hash_token("fee", 4096)
    c = _hash_token("disciplinary", 4096)
    assert a == b
    assert a != c
    assert 0 <= a < 4096


# ── BM25SparseEncoder ────────────────────────────────────────────────
def test_bm25_encoder_tracks_corpus_stats() -> None:
    enc = BM25SparseEncoder(_settings(Path("/tmp")))
    enc.encode_documents(["Fees and compensation", "Disciplinary information"])
    assert enc.n_docs == 2
    # Both tokens present in only one doc each → df=1 per term.


def test_bm25_encoder_query_uses_idf_when_corpus_observed() -> None:
    enc = BM25SparseEncoder(_settings(Path("/tmp")))
    docs = [
        "the firm offers tiered fees",
        "the firm has no disciplinary history",
        "the firm participates in soft dollar arrangements",
    ]
    enc.encode_documents(docs)
    q = enc.encode_query("fees")
    assert len(q.indices) == 1
    # IDF positive for a term appearing in 1 of 3 docs.
    assert q.values[0] > 0.0


def test_bm25_encoder_zero_corpus_query_falls_back_to_floor() -> None:
    enc = BM25SparseEncoder(_settings(Path("/tmp")))
    q = enc.encode_query("fees")
    # No corpus observed; floor IDF prevents zero-weight queries.
    assert q.values and q.values[0] > 0.0


def test_bm25_encoder_empty_text_returns_empty_sparse_vector() -> None:
    enc = BM25SparseEncoder(_settings(Path("/tmp")))
    [v] = enc.encode_documents(["   "])
    assert v.indices == [] and v.values == []


# ── IdentitySparseEncoder ────────────────────────────────────────────
def test_identity_encoder_doc_weight_is_sqrt_tf() -> None:
    enc = IdentitySparseEncoder(vocab_size=4096)
    [v] = enc.encode_documents(["fee fee fee bar"])
    # Two unique tokens — fee (TF=3, sqrt=√3) and bar (TF=1, sqrt=1).
    assert len(v.indices) == 2
    sorted_values = sorted(v.values, reverse=True)
    assert abs(sorted_values[0] - 3**0.5) < 1e-6
    assert abs(sorted_values[1] - 1.0) < 1e-6


def test_identity_encoder_query_weight_is_one_per_token() -> None:
    enc = IdentitySparseEncoder(vocab_size=4096)
    q = enc.encode_query("fee fee bar")
    assert q.values == [1.0, 1.0]


# ── PeerStore upsert with sparse ─────────────────────────────────────
def test_hybrid_collection_includes_sparse_vector_config(tmp_path: Path) -> None:
    store = _hybrid_store(tmp_path)
    store.ensure_collection()
    info = store._client.get_collection(store.collection_name)
    # Named sparse vectors live under config.params.sparse_vectors as a dict.
    sparse_cfg = info.config.params.sparse_vectors
    assert sparse_cfg is not None
    assert SPARSE_VECTOR_NAME in sparse_cfg


def test_dense_only_collection_omits_sparse_config(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = PeerStore(QdrantClient(":memory:"), RandomEmbedder(dim=s.embedding_dim), s)
    store.ensure_collection()
    info = store._client.get_collection(store.collection_name)
    assert info.config.params.sparse_vectors in (None, {})
    # Dense is still there.
    assert DENSE_VECTOR_NAME in info.config.params.vectors


def test_hybrid_upsert_writes_body_into_payload(tmp_path: Path) -> None:
    store = _hybrid_store(tmp_path)
    store.ensure_collection()
    sec = _section(5, "Tiered fee schedule starting at 1.00% AUM.")
    store.upsert_sections(PeerSpec(crd="108000", brochure_version_id="v1"), [sec])

    pts, _ = store._client.scroll(store.collection_name, limit=10, with_payload=True)
    assert pts[0].payload["body"] == sec.body
    assert pts[0].payload["item_number"] == 5


def test_query_peers_rejects_hybrid_when_no_sparse_encoder(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = PeerStore(QdrantClient(":memory:"), RandomEmbedder(dim=s.embedding_dim), s)
    store.ensure_collection()
    with pytest.raises(ValueError, match="hybrid=True requires a SparseEncoder"):
        store.query_peers("anything", hybrid=True)


# ── Hybrid query (RRF) end-to-end ────────────────────────────────────
def _seed_three_brochures(store: PeerStore) -> None:
    store.ensure_collection()
    store.upsert_sections(
        PeerSpec(crd="108000", brochure_version_id="v1", aum_band="$1B-$10B"),
        [
            _section(5, "Fees: 1.00% AUM tiered schedule billed quarterly in advance."),
            _section(9, "No disciplinary history to disclose."),
        ],
    )
    store.upsert_sections(
        PeerSpec(crd="108001", brochure_version_id="v2", aum_band="$1B-$10B"),
        [
            _section(5, "Hourly planning fees of $300 per hour, no AUM-based pricing."),
            _section(9, "FINRA settlement in 2018 against a former principal."),
        ],
    )
    store.upsert_sections(
        PeerSpec(crd="108002", brochure_version_id="v3", aum_band="$100M-$1B"),
        [
            _section(5, "Wrap fee program with quarterly billing in advance."),
        ],
    )


def test_hybrid_query_returns_results_with_filters(tmp_path: Path) -> None:
    store = _hybrid_store(tmp_path)
    _seed_three_brochures(store)

    hits = store.query_peers(
        "tiered AUM fees",
        item_number=5,
        aum_band="$1B-$10B",
        k=5,
        hybrid=True,
        rerank=False,
    )
    assert hits, "hybrid query returned no hits"
    assert all(h.item_number == 5 for h in hits)
    assert all(h.payload.get("aum_band") == "$1B-$10B" for h in hits)


def test_hybrid_query_includes_body_excerpt(tmp_path: Path) -> None:
    store = _hybrid_store(tmp_path)
    _seed_three_brochures(store)
    hits = store.query_peers("fees", item_number=5, k=3, hybrid=True, rerank=False)
    for h in hits:
        # Excerpt is now real prose from the indexed body, not a placeholder.
        assert "fee" in h.body_excerpt.lower() or "wrap" in h.body_excerpt.lower()


def test_dense_only_query_still_works_after_hybrid_collection(tmp_path: Path) -> None:
    """Backward-compat: a hybrid-capable store can still answer dense-only queries."""
    store = _hybrid_store(tmp_path)
    _seed_three_brochures(store)
    hits = store.query_peers("fees", item_number=5, k=3, hybrid=False, rerank=False)
    assert hits


# ── Cross-encoder reranker (Identity test double) ─────────────────────
def test_identity_reranker_scores_by_token_overlap() -> None:
    r = IdentityReranker()
    scores = r.rerank("fee schedule", ["fee schedule details", "disciplinary history", "fee"])
    # First doc shares both query terms (score 1.0).
    assert scores[0] == pytest.approx(1.0)
    # Second doc shares zero terms.
    assert scores[1] == pytest.approx(0.0)
    # Third doc shares one of two query terms.
    assert scores[2] == pytest.approx(0.5)


def test_identity_reranker_handles_empty_inputs() -> None:
    r = IdentityReranker()
    assert r.rerank("query", []) == []
    assert r.rerank("", ["doc"]) == [0.0]


def test_query_peers_with_reranker_reorders_by_overlap(tmp_path: Path) -> None:
    store = _hybrid_store(tmp_path, with_reranker=True)
    store.ensure_collection()
    # Three sections in the same Item 5; query should be re-ranked to favour
    # the section with the most query-term overlap.
    store.upsert_sections(
        PeerSpec(crd="A", brochure_version_id="va"),
        [_section(5, "wrap fee program quarterly billing")],
    )
    store.upsert_sections(
        PeerSpec(crd="B", brochure_version_id="vb"),
        [_section(5, "tiered AUM fees billed monthly in arrears")],
    )
    store.upsert_sections(
        PeerSpec(crd="C", brochure_version_id="vc"),
        [_section(5, "fixed annual retainer with hourly add-ons")],
    )

    hits = store.query_peers("tiered AUM fees", item_number=5, k=3, hybrid=True, rerank=True)
    # The reranker should put the brochure with the most overlapping terms first.
    assert hits[0].crd == "B"


def test_query_peers_skip_reranker_when_disabled(tmp_path: Path) -> None:
    store = _hybrid_store(tmp_path, with_reranker=True)
    _seed_three_brochures(store)
    hits = store.query_peers("fees", item_number=5, k=3, hybrid=True, rerank=False)
    # With rerank=False, the IdentityReranker is bypassed; we just want hits back.
    assert hits


# ── make_peer_store factory shape ────────────────────────────────────
def test_make_peer_store_defaults_to_hybrid_and_rerank(monkeypatch) -> None:
    """Smoke: factory returns a store with sparse encoder + reranker wired.

    We don't actually connect to Qdrant — monkeypatch the QdrantClient ctor
    to a no-op double — but verify the field assignments.
    """
    from adv_lens.retrieval import qdrant_store as mod

    class _DummyClient:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "QdrantClient", _DummyClient)
    store = mod.make_peer_store()
    assert store.hybrid_enabled is True
    assert store._reranker is not None


def test_make_peer_store_dense_only_disables_sparse_and_rerank(monkeypatch) -> None:
    from adv_lens.retrieval import qdrant_store as mod

    class _DummyClient:
        def __init__(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "QdrantClient", _DummyClient)
    store = mod.make_peer_store(hybrid=False, rerank=False)
    assert store.hybrid_enabled is False
    assert store._reranker is None
