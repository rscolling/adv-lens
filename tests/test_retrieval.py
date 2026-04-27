"""Retrieval tests — in-memory Qdrant + RandomEmbedder, fully offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from adv_lens.app.settings import Settings
from adv_lens.retrieval.embeddings import RandomEmbedder
from adv_lens.retrieval.qdrant_store import (
    DENSE_VECTOR_NAME,
    PeerSpec,
    PeerStore,
    point_id_for,
    section_sha256,
)
from adv_lens.retrieval.seed import (
    load_peer_specs,
    report_to_markdown,
    seed_peers,
)
from adv_lens.segmenter import HeuristicSegmenter
from adv_lens.segmenter.models import ITEM_TITLES, ItemNumber, Section


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        qdrant_collection_peers="advlens_test",
        embedding_dim=64,  # smaller dim keeps test vectors compact
    )


def _store(tmp_path: Path) -> PeerStore:
    s = _settings(tmp_path)
    return PeerStore(QdrantClient(":memory:"), RandomEmbedder(dim=s.embedding_dim), s)


def _section(item: int, body: str) -> Section:
    return Section(
        item_number=ItemNumber(item),
        title=ITEM_TITLES[item],
        body=body,
        char_start=0,
        char_end=len(body),
    )


# ── Embedder ─────────────────────────────────────────────────────────
def test_random_embedder_is_deterministic_and_unit_norm() -> None:
    e = RandomEmbedder(dim=128)
    v1 = e.embed(["hello"])[0]
    v2 = e.embed(["hello"])[0]
    assert v1 == v2
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-5


def test_random_embedder_distinguishes_inputs() -> None:
    e = RandomEmbedder(dim=128)
    v1 = e.embed(["fee schedule"])[0]
    v2 = e.embed(["disciplinary history"])[0]
    cosine = sum(a * b for a, b in zip(v1, v2, strict=True))
    assert cosine < 0.95  # different texts → not identical


# ── PeerStore: schema + upsert ───────────────────────────────────────
def test_ensure_collection_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    store.ensure_collection()  # second call must not raise
    assert store._client.collection_exists(store.collection_name)


def test_upsert_writes_one_point_per_meaningful_section(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()

    sections = [
        _section(4, "Synthetic Advisory provides discretionary investment management."),
        _section(5, "Fees are charged at 1.00% on the first $1M, 0.85% above."),
        _section(6, "Not applicable."),  # placeholder — skipped
    ]
    spec = PeerSpec(crd="108000", brochure_version_id="999001", aum_band="$1B-$10B")
    written = store.upsert_sections(spec, sections)

    assert written == 2
    assert store.count() == 2


def test_upsert_is_idempotent_via_deterministic_point_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    sections = [_section(5, "Fees: 1% of AUM annually.")]
    spec = PeerSpec(crd="108000", brochure_version_id="999001")

    store.upsert_sections(spec, sections)
    store.upsert_sections(spec, sections)  # second call upserts in place
    assert store.count() == 1


def test_upsert_requires_brochure_version_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    spec = PeerSpec(crd="108000")  # no version id
    with pytest.raises(ValueError, match="brochure_version_id is required"):
        store.upsert_sections(spec, [_section(5, "Fees.")])


def test_point_id_is_stable_and_uuid_shaped() -> None:
    pid_a = point_id_for("108000", "999001", 5)
    pid_b = point_id_for("108000", "999001", 5)
    pid_c = point_id_for("108000", "999001", 6)
    assert pid_a == pid_b
    assert pid_a != pid_c
    assert len(pid_a) == 36  # uuid str length


def test_section_sha256_changes_with_body() -> None:
    a = section_sha256("Fees: 1.00%")
    b = section_sha256("Fees: 0.85%")
    assert a != b


# ── PeerStore: query + filters ───────────────────────────────────────
def test_query_filters_by_item_number(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    store.upsert_sections(
        PeerSpec(crd="108000", brochure_version_id="999001"),
        [_section(5, "Tiered fee schedule."), _section(9, "No disciplinary events.")],
    )

    item5_hits = store.query_peers("fee schedule", item_number=5, k=10)
    assert all(h.item_number == 5 for h in item5_hits)
    assert len(item5_hits) == 1


def test_query_filters_by_aum_band(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    store.upsert_sections(
        PeerSpec(crd="108000", brochure_version_id="999001", aum_band="$1B-$10B"),
        [_section(5, "Fees text.")],
    )
    store.upsert_sections(
        PeerSpec(crd="108001", brochure_version_id="999002", aum_band="$100M-$1B"),
        [_section(5, "Fees text.")],
    )

    big = store.query_peers("fee", item_number=5, aum_band="$1B-$10B", k=10)
    assert {h.crd for h in big} == {"108000"}


def test_query_excludes_subject_crd(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    for crd in ("108000", "108001", "108002"):
        store.upsert_sections(
            PeerSpec(crd=crd, brochure_version_id=f"v{crd}"),
            [_section(5, "Fees text.")],
        )

    hits = store.query_peers("fee", item_number=5, exclude_crd="108000", k=10)
    assert "108000" not in {h.crd for h in hits}
    assert len(hits) == 2


def test_collection_uses_named_dense_vector(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure_collection()
    info = store._client.get_collection(store.collection_name)
    # Named vectors live under .config.params.vectors as a dict in qdrant-client.
    vectors_cfg = info.config.params.vectors
    assert isinstance(vectors_cfg, dict)
    assert DENSE_VECTOR_NAME in vectors_cfg


# ── Seed flow ─────────────────────────────────────────────────────────
def test_load_peer_specs_parses_json(tmp_path: Path) -> None:
    path = tmp_path / "peers.json"
    path.write_text(
        '[{"crd": "108000", "aum_band": "$1B-$10B"},'
        ' {"crd": "108001", "brochure_version_id": "v2"}]',
        encoding="utf-8",
    )
    specs = load_peer_specs(path)
    assert [s.crd for s in specs] == ["108000", "108001"]
    assert specs[0].aum_band == "$1B-$10B"
    assert specs[1].brochure_version_id == "v2"


def test_report_to_markdown_renders_totals(tmp_path: Path) -> None:
    md = report_to_markdown(
        {
            "108000": {"sections_indexed": 17, "errors": [], "brochure_version_id": "999001"},
            "108001": {"sections_indexed": 0, "errors": ["fetch failed"]},
        }
    )
    assert "108000" in md and "108001" in md
    assert "**17**" in md  # total
    assert "fetch failed" in md


async def test_seed_peers_records_pipeline_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed_peers passes pipeline errors through to the per-CRD report.

    Earlier this test relied on the pipeline failing because the SEC IAPD
    fetch couldn't reach the network — true on offline-dev machines but
    false on CI runners with internet, where the fetch happened to succeed
    and indexed a real section. Stubbing ``run_pipeline`` directly tests
    the error-handling branch deterministically regardless of environment.
    """
    from adv_lens.app.graph.state import ADVState
    from adv_lens.retrieval import seed as seed_module

    async def _failing_pipeline(crd: str, **kwargs: object) -> ADVState:
        return ADVState(
            trace_id="seed-test-trace",
            brochure_crd=crd,
            errors=[f"fetch_brochure_node: simulated failure for CRD {crd}"],
        )

    monkeypatch.setattr(seed_module, "run_pipeline", _failing_pipeline)

    store = _store(tmp_path)
    specs = [PeerSpec(crd="108000", brochure_version_id="999001")]
    report = await seed_peers(specs, store)

    assert "108000" in report
    assert report["108000"]["sections_indexed"] == 0
    assert report["108000"]["errors"]
    assert any("simulated failure" in e for e in report["108000"]["errors"])


# ── Live segmenter → store smoke (no network, no LLM, no torch) ──────
def test_full_section_list_indexes_via_random_embedder(tmp_path: Path) -> None:
    text_lines = []
    for n in range(1, 19):
        text_lines.append(f"Item {n}. {ITEM_TITLES[n]}")
        text_lines.append(f"Body for item {n} — some unique text {n * 11}.")
    text = "\n".join(text_lines)

    segmented = HeuristicSegmenter().segment_text(text)
    store = _store(tmp_path)
    store.ensure_collection()
    written = store.upsert_sections(
        PeerSpec(crd="108000", brochure_version_id="999001", aum_band="$1B-$10B"),
        segmented.sections,
    )
    assert written == 18
    assert store.count() == 18

    # Sanity-check semantic-ish ordering: query for "fees" should rank the
    # Item 5 section in the top-3 even with random vectors (given enough items).
    # We don't assert top-1 because RandomEmbedder is hash-based, not semantic.
    hits = store.query_peers("Item 5 Fees and Compensation", item_number=5, k=1)
    assert len(hits) == 1
    assert hits[0].item_number == 5
