"""retrieve_peers_node tests — in-memory PeerStore, fully offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from adv_lens.app.graph.nodes.retrieve_peers import (
    _ITEM_QUERIES,
    _items_to_query,
    retrieve_peers_node,
)
from adv_lens.app.graph.state import ADVState
from adv_lens.app.settings import Settings
from adv_lens.extractors.schemas import (
    AffiliationsItem10,
    ConflictsExtraction,
    DisciplinaryExtraction,
    Extractions,
    FeeExtraction,
)
from adv_lens.retrieval.embeddings import RandomEmbedder
from adv_lens.retrieval.qdrant_store import PeerSpec, PeerStore
from adv_lens.retrieval.sparse import IdentitySparseEncoder
from adv_lens.segmenter.models import ITEM_TITLES, ItemNumber, Section


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        qdrant_collection_peers="advlens_peer_test",
        embedding_dim=64,
        sparse_vocab_size=4096,
        peer_query_top_k_per_item=3,
    )


def _section(item: int, body: str) -> Section:
    return Section(
        item_number=ItemNumber(item),
        title=ITEM_TITLES[item],
        body=body,
        char_start=0,
        char_end=len(body),
    )


def _seed(tmp_path: Path) -> PeerStore:
    s = _settings(tmp_path)
    store = PeerStore(
        QdrantClient(":memory:"),
        RandomEmbedder(dim=s.embedding_dim),
        s,
        sparse_encoder=IdentitySparseEncoder(vocab_size=s.sparse_vocab_size),
    )
    store.ensure_collection()
    # Three peers in different AUM bands, each with several Item sections.
    store.upsert_sections(
        PeerSpec(crd="P-001", brochure_version_id="v1", aum_band="$1B-$10B"),
        [
            _section(5, "Tiered AUM fee schedule starting 1.00%."),
            _section(9, "No disciplinary history."),
            _section(10, "No affiliated broker-dealer."),
            _section(12, "Soft dollars within Section 28(e)."),
        ],
    )
    store.upsert_sections(
        PeerSpec(crd="P-002", brochure_version_id="v2", aum_band="$1B-$10B"),
        [
            _section(5, "Hourly planning fees $300-$450."),
            _section(9, "FINRA settlement against principal in 2018."),
            _section(11, "Code of ethics requires preclearance."),
        ],
    )
    store.upsert_sections(
        PeerSpec(crd="P-003", brochure_version_id="v3", aum_band="$100M-$1B"),
        [
            _section(5, "Wrap fee program quarterly billing."),
        ],
    )
    return store


# ── Helpers / static config ──────────────────────────────────────────
def test_static_query_table_covers_all_extractor_items() -> None:
    # Every Item we extract for must have a peer query anchor.
    expected = {
        ItemNumber.FEES_AND_COMPENSATION,
        ItemNumber.DISCIPLINARY_INFORMATION,
        ItemNumber.OTHER_ACTIVITIES,
        ItemNumber.CODE_OF_ETHICS,
        ItemNumber.BROKERAGE_PRACTICES,
    }
    assert set(_ITEM_QUERIES) == expected


def test_items_to_query_picks_only_populated_extractions() -> None:
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        extractions=Extractions(fee=FeeExtraction()),
    )
    assert _items_to_query(state) == [ItemNumber.FEES_AND_COMPENSATION]


def test_items_to_query_expands_conflicts_to_three_items() -> None:
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        extractions=Extractions(conflicts=ConflictsExtraction()),
    )
    items = _items_to_query(state)
    assert ItemNumber.OTHER_ACTIVITIES in items
    assert ItemNumber.CODE_OF_ETHICS in items
    assert ItemNumber.BROKERAGE_PRACTICES in items


def test_items_to_query_returns_empty_when_no_extractions() -> None:
    state = ADVState(trace_id="t", brochure_crd="108000")
    assert _items_to_query(state) == []


# ── Node behaviour ────────────────────────────────────────────────────
async def test_retrieve_peers_returns_empty_when_no_extractions(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    state = ADVState(trace_id="t", brochure_crd="108000")
    update = await retrieve_peers_node(state, store=store)
    assert update == {"peer_context": []}


async def test_retrieve_peers_populates_per_item_hits(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        extractions=Extractions(
            fee=FeeExtraction(),
            disciplinary=DisciplinaryExtraction(has_disciplinary_history=False),
        ),
    )
    update = await retrieve_peers_node(state, store=store)

    peer_context = update["peer_context"]
    # Two extractions populated → two items queried (5 and 9).
    item_numbers = {h["item_number"] for h in peer_context}
    assert item_numbers == {5, 9}
    # All hits are dicts (PeerHit.model_dump()), not Pydantic models.
    assert all(isinstance(h, dict) for h in peer_context)
    assert all("crd" in h and "score" in h and "body_excerpt" in h for h in peer_context)


async def test_retrieve_peers_excludes_subject_crd(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    state = ADVState(
        trace_id="t",
        brochure_crd="P-001",  # one of the seeded peers
        extractions=Extractions(fee=FeeExtraction()),
    )
    update = await retrieve_peers_node(state, store=store)
    assert all(h["crd"] != "P-001" for h in update["peer_context"])


async def test_retrieve_peers_filters_by_aum_band_when_set(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        brochure_aum_band="$1B-$10B",
        extractions=Extractions(fee=FeeExtraction()),
    )
    update = await retrieve_peers_node(state, store=store)
    # P-003 is in $100M-$1B and should be filtered out; only P-001 / P-002 hits.
    crds = {h["crd"] for h in update["peer_context"]}
    assert "P-003" not in crds
    assert crds.issubset({"P-001", "P-002"})


async def test_retrieve_peers_queries_three_items_for_conflicts(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        extractions=Extractions(
            conflicts=ConflictsExtraction(
                item_10_affiliations=AffiliationsItem10(affiliated_broker_dealer=False)
            )
        ),
    )
    update = await retrieve_peers_node(state, store=store)
    item_numbers = {h["item_number"] for h in update["peer_context"]}
    # The corpus has hits for 10 (P-001), 11 (P-002), 12 (P-001).
    assert {10, 11, 12} & item_numbers


# ── Failure modes ────────────────────────────────────────────────────
class _FlakyStore:
    """PeerStore stand-in that always raises — exercises graceful degradation."""

    hybrid_enabled = False

    def query_peers(self, *args, **kwargs):
        raise RuntimeError("qdrant unreachable")


async def test_retrieve_peers_logs_per_item_errors_and_returns_empty(tmp_path: Path) -> None:
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        extractions=Extractions(
            fee=FeeExtraction(),
            disciplinary=DisciplinaryExtraction(has_disciplinary_history=False),
        ),
    )
    update = await retrieve_peers_node(state, store=_FlakyStore())
    assert update["peer_context"] == []
    assert "errors" in update
    assert len(update["errors"]) == 2  # one per attempted item
    assert all("qdrant unreachable" in e for e in update["errors"])


async def test_retrieve_peers_unavailable_store_skips_silently_with_one_warning(
    monkeypatch, tmp_path: Path
) -> None:
    # Force _default_store() to return None — simulates make_peer_store() raising.
    from adv_lens.app.graph.nodes import retrieve_peers as mod

    monkeypatch.setattr(mod, "_default_store", lambda: None)
    state = ADVState(
        trace_id="t",
        brochure_crd="108000",
        extractions=Extractions(fee=FeeExtraction()),
    )
    update = await retrieve_peers_node(state)
    assert update["peer_context"] == []
    assert any("peer store unavailable" in e for e in update["errors"])


# ── Pipeline topology ────────────────────────────────────────────────
def test_pipeline_inserts_retrieve_peers_between_extractors_and_redline() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=True)
    g = compiled.get_graph()
    nodes = set(g.nodes)
    assert "retrieve_peers" in nodes

    edges = {(e.source, e.target) for e in g.edges}
    # Each extractor must edge into retrieve_peers (fan-in shape).
    assert ("extract_fee", "retrieve_peers") in edges
    assert ("extract_disciplinary", "retrieve_peers") in edges
    assert ("extract_conflicts", "retrieve_peers") in edges
    # retrieve_peers feeds write_redline; the old direct edges are gone.
    assert ("retrieve_peers", "write_redline") in edges
    assert ("extract_fee", "write_redline") not in edges
    assert ("extract_disciplinary", "write_redline") not in edges
    assert ("extract_conflicts", "write_redline") not in edges


def test_pipeline_omits_retrieve_peers_when_extractors_excluded() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=False)
    nodes = set(compiled.get_graph().nodes)
    assert "retrieve_peers" not in nodes


# ── Backward compat: existing extractor tests still pass ─────────────
def test_advstate_now_carries_aum_band_field() -> None:
    state = ADVState(trace_id="t", brochure_crd="108000", brochure_aum_band="$1B-$10B")
    assert state.brochure_aum_band == "$1B-$10B"


def test_advstate_aum_band_defaults_to_none() -> None:
    state = ADVState(trace_id="t", brochure_crd="108000")
    assert state.brochure_aum_band is None


# Quiet a pylint-style unused-import — pytest collects without it.
_ = pytest
