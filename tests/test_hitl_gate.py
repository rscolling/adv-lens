"""HumanReviewGate tests — node, pipeline shape, FastAPI decision endpoints."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from adv_lens.app.graph.nodes.hitl_gate import compute_report_hash, hitl_gate_node
from adv_lens.app.graph.state import ADVState
from adv_lens.app.main import app
from adv_lens.app.storage.audit import HumanReview
from adv_lens.app.storage.db import get_session
from adv_lens.extractors.schemas import (
    RedlineReport,
    Scorecard,
    ScoreCategory,
)


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture()
def in_memory_session() -> Iterator[Session]:
    # StaticPool keeps a single connection alive so the in-memory DB is
    # visible to every Session created from this engine — without it,
    # FastAPI's per-request session would get a fresh empty DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(in_memory_session: Session) -> Iterator[TestClient]:
    """FastAPI TestClient with the DB session dependency overridden to the in-memory engine."""

    def _override() -> Iterator[Session]:
        yield in_memory_session

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def _redline(crd: str = "108000") -> RedlineReport:
    return RedlineReport(
        brochure_crd=crd,
        brochure_version_id="v1",
        scorecard=Scorecard(
            overall_score=80,
            categories=[ScoreCategory(name="compliance", score=80, rationale="x")],
            headline="Test report.",
        ),
    )


# ── compute_report_hash ──────────────────────────────────────────────
def test_compute_report_hash_is_deterministic_64_hex() -> None:
    h1 = compute_report_hash(_redline().model_dump_json())
    h2 = compute_report_hash(_redline().model_dump_json())
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_compute_report_hash_changes_with_payload() -> None:
    a = compute_report_hash(_redline(crd="108000").model_dump_json())
    b = compute_report_hash(_redline(crd="999999").model_dump_json())
    assert a != b


# ── hitl_gate_node ───────────────────────────────────────────────────
def test_gate_marks_pending_review_when_redline_present() -> None:
    state = ADVState(trace_id="t-1", brochure_crd="108000", redline=_redline())
    update = hitl_gate_node(state)
    assert update["review_status"] == "pending_review"
    assert update["report_hash"] is not None
    assert len(update["report_hash"]) == 64


def test_gate_rejects_when_no_redline_on_state() -> None:
    state = ADVState(trace_id="t-1", brochure_crd="108000")
    update = hitl_gate_node(state)
    assert update["review_status"] == "rejected"
    assert "report_hash" not in update


def test_gate_auto_approves_when_hitl_disabled(monkeypatch) -> None:
    from adv_lens.app.graph.nodes import hitl_gate as mod

    monkeypatch.setattr(mod.default_settings, "enable_hitl", False, raising=False)
    state = ADVState(trace_id="t-1", brochure_crd="108000", redline=_redline())
    update = hitl_gate_node(state)
    assert update["review_status"] == "approved"


def test_gate_report_hash_round_trips_through_state() -> None:
    state = ADVState(trace_id="t-1", brochure_crd="108000", redline=_redline())
    update = hitl_gate_node(state)
    # Apply update to state and verify the computed hash matches independent recompute.
    new_state = state.model_copy(update=update)
    expected = compute_report_hash(state.redline.model_dump_json())
    assert new_state.report_hash == expected
    assert new_state.review_status == "pending_review"


# ── Pipeline topology ────────────────────────────────────────────────
def test_pipeline_appends_hitl_gate_as_terminal_node() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=True)
    g = compiled.get_graph()
    nodes = set(g.nodes)
    assert "hitl_gate" in nodes

    edges = {(e.source, e.target) for e in g.edges}
    # write_redline now feeds hitl_gate, hitl_gate edges to END.
    assert ("write_redline", "hitl_gate") in edges
    end_edges = {src for (src, tgt) in edges if tgt == "__end__"}
    assert "hitl_gate" in end_edges
    assert "write_redline" not in end_edges  # write_redline no longer terminal


def test_pipeline_omits_hitl_gate_when_extractors_excluded() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=False)
    nodes = set(compiled.get_graph().nodes)
    assert "hitl_gate" not in nodes


# ── ADVState ──────────────────────────────────────────────────────────
def test_advstate_review_status_defaults_to_not_started() -> None:
    state = ADVState(trace_id="t", brochure_crd="108000")
    assert state.review_status == "not_started"
    assert state.report_hash is None


def test_advstate_review_status_validates_literal() -> None:
    with pytest.raises(ValueError):
        ADVState(trace_id="t", brochure_crd="108000", review_status="frobnicated")


# ── FastAPI decision endpoints ───────────────────────────────────────
_VALID_HASH = "a" * 64


def _decision_payload(**overrides) -> dict:
    base = {
        "trace_id": "trace-001",
        "brochure_crd": "108000",
        "report_hash": _VALID_HASH,
        "reviewer": "ccoanon@firm.example",
        "decision": "approved",
        "rationale": "Clean report, no material findings; aligns with peer norms.",
    }
    base.update(overrides)
    return base


def test_post_decision_records_audit_row(client: TestClient, in_memory_session: Session) -> None:
    r = client.post("/report/decision", json=_decision_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["decision"] == "approved"
    assert body["report_hash"] == _VALID_HASH
    assert body["id"] is not None

    # Round-trip via the DB session.
    rows = list(in_memory_session.exec(select(HumanReview)).all())
    assert len(rows) == 1
    assert rows[0].trace_id == "trace-001"
    assert rows[0].rationale.startswith("Clean report")


def test_post_decision_rejects_non_hex_report_hash(client: TestClient) -> None:
    r = client.post("/report/decision", json=_decision_payload(report_hash="ZZ" * 32))
    assert r.status_code == 422


def test_post_decision_rejects_short_report_hash(client: TestClient) -> None:
    r = client.post("/report/decision", json=_decision_payload(report_hash="abcd"))
    assert r.status_code == 422


def test_post_decision_rejects_unknown_decision(client: TestClient) -> None:
    r = client.post("/report/decision", json=_decision_payload(decision="frobnicated"))
    assert r.status_code == 422


def test_post_decision_rejects_non_numeric_crd(client: TestClient) -> None:
    r = client.post("/report/decision", json=_decision_payload(brochure_crd="abc"))
    assert r.status_code == 422


def test_post_decision_rejects_empty_rationale(client: TestClient) -> None:
    r = client.post("/report/decision", json=_decision_payload(rationale=""))
    assert r.status_code == 422


def test_get_decisions_returns_oldest_first(client: TestClient) -> None:
    # Three decisions on the same trace — revise → approve.
    client.post(
        "/report/decision",
        json=_decision_payload(decision="revise", rationale="Tighten Item 5 finding."),
    )
    client.post(
        "/report/decision",
        json=_decision_payload(decision="approved", rationale="Revisions look good."),
    )

    r = client.get("/report/decision/trace-001")
    assert r.status_code == 200
    decisions = r.json()
    assert len(decisions) == 2
    # Oldest first.
    assert decisions[0]["decision"] == "revise"
    assert decisions[1]["decision"] == "approved"


def test_get_decisions_unknown_trace_returns_empty_list(client: TestClient) -> None:
    r = client.get("/report/decision/trace-does-not-exist")
    assert r.status_code == 200
    assert r.json() == []


def test_post_decision_allows_multiple_rows_per_trace(
    client: TestClient, in_memory_session: Session
) -> None:
    """Re-posting the same decision creates a new row — that's the audit semantic."""
    client.post("/report/decision", json=_decision_payload())
    client.post("/report/decision", json=_decision_payload())
    rows = list(in_memory_session.exec(select(HumanReview)).all())
    assert len(rows) == 2
