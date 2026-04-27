"""Review-UI route tests — list, detail, redline iframe, decision POST, seed."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.jobs.scheduler import get_scheduler
from adv_lens.app.main import app
from adv_lens.app.storage.audit import HumanReview
from adv_lens.app.storage.db import get_session
from adv_lens.app.web import seed as seed_module

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SAMPLE_STATE = _REPO_ROOT / "docs" / "examples" / "sample-state.json"


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture()
def in_memory_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def captured_schedules() -> list[tuple[str, object]]:
    """Records (trace_id, session_factory) for every scheduler call."""
    return []


@pytest.fixture()
def client(
    in_memory_session: Session, captured_schedules: list[tuple[str, object]]
) -> Iterator[TestClient]:
    def _override_session() -> Iterator[Session]:
        yield in_memory_session

    def _fake_scheduler() -> object:
        def _record(trace_id: str, session_factory: object) -> None:
            captured_schedules.append((trace_id, session_factory))

        return _record

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_scheduler] = _fake_scheduler
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_scheduler, None)


def _seed_run(
    session: Session,
    *,
    trace_id: str = "test-run-1",
    crd: str = "110181",
    redline_score: int = 68,
    report_hash: str = "a" * 64,
    status: str = "complete",
) -> PipelineRun:
    """Insert a minimal PipelineRun row with a valid RedlineReport in result."""
    redline = {
        "brochure_crd": crd,
        "brochure_version_id": "1037550",
        "scorecard": {
            "overall_score": redline_score,
            "categories": [
                {"name": "compliance", "score": redline_score, "rationale": "ok"},
            ],
            "headline": f"Score {redline_score}: stub headline for tests.",
        },
        "findings": [
            {
                "id": "F-001",
                "category": "fee_structure",
                "severity": "info",
                "summary": "Stub finding for tests.",
                "detail": "More text.",
                "recommendation": "Do nothing.",
                "sec_reference": "Item 5",
            }
        ],
        "peer_comparisons": [],
        "extraction_warnings_seen": [],
        "notes": None,
    }
    row = PipelineRun(
        trace_id=trace_id,
        brochure_crd=crd,
        brochure_version_id="1037550",
        status=status,
        result={
            "trace_id": trace_id,
            "brochure_crd": crd,
            "brochure_version_id": "1037550",
            "redline": redline,
            "report_hash": report_hash,
            "review_status": "pending_review",
        },
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── List view ────────────────────────────────────────────────────────
def test_review_list_empty_state(client: TestClient) -> None:
    r = client.get("/review")
    assert r.status_code == 200
    assert "No pipeline runs yet" in r.text


def test_review_list_shows_seeded_run(client: TestClient, in_memory_session: Session) -> None:
    _seed_run(in_memory_session, trace_id="seen-1", redline_score=68)
    r = client.get("/review")
    assert r.status_code == 200
    assert "seen-1" in r.text
    # Score pill rendered
    assert ">68<" in r.text
    # Status pill rendered
    assert "pill-complete" in r.text


def test_review_list_score_band_classes(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="hi", redline_score=92)
    _seed_run(in_memory_session, trace_id="mid", redline_score=68)
    _seed_run(in_memory_session, trace_id="lo", redline_score=42)
    r = client.get("/review")
    assert "score-good" in r.text
    assert "score-warn" in r.text
    assert "score-bad" in r.text


# ── Detail view ──────────────────────────────────────────────────────
def test_review_detail_404_on_unknown_trace(client: TestClient) -> None:
    r = client.get("/review/no-such-trace")
    assert r.status_code == 404


def test_review_detail_renders_with_redline_and_form(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="detail-1", report_hash="b" * 64)
    r = client.get("/review/detail-1")
    assert r.status_code == 200
    # iframe pointing at the redline route
    assert 'src="/review/detail-1/redline.html"' in r.text
    # Decision form posts via HTMX
    assert 'hx-post="/review/detail-1/decide"' in r.text
    # Hidden report_hash carried into the form
    assert f'value="{"b" * 64}"' in r.text
    # Empty decision history initially
    assert "No decisions recorded yet" in r.text


def test_review_detail_disables_form_when_no_report_hash(
    client: TestClient, in_memory_session: Session
) -> None:
    # Seed without a report_hash to exercise the disabled-form branch
    row = PipelineRun(
        trace_id="no-hash",
        brochure_crd="110181",
        status="complete",
        result={"trace_id": "no-hash", "brochure_crd": "110181", "redline": None},
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    in_memory_session.add(row)
    in_memory_session.commit()

    r = client.get("/review/no-hash")
    assert r.status_code == 200
    assert "decision capture disabled" in r.text
    # Form should NOT be rendered
    assert "hx-post=" not in r.text


# ── Redline iframe route ─────────────────────────────────────────────
def test_review_redline_iframe_renders_full_html(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="frame-1", redline_score=82)
    r = client.get("/review/frame-1/redline.html")
    assert r.status_code == 200
    # Standalone HTML (the existing renderer's contract)
    assert "<!DOCTYPE html>" in r.text or "<!doctype html>" in r.text.lower()
    assert "ADV-Lens Compliance Redline" in r.text
    assert ">82<" in r.text


def test_review_redline_iframe_handles_missing_redline(
    client: TestClient, in_memory_session: Session
) -> None:
    row = PipelineRun(
        trace_id="empty-redline",
        brochure_crd="110181",
        status="failed",
        result={"trace_id": "empty-redline", "brochure_crd": "110181"},
    )
    in_memory_session.add(row)
    in_memory_session.commit()

    r = client.get("/review/empty-redline/redline.html")
    assert r.status_code == 200
    assert "No redline produced" in r.text


def test_review_redline_iframe_404_on_unknown_trace(client: TestClient) -> None:
    r = client.get("/review/no-such-trace/redline.html")
    assert r.status_code == 404


# ── Decision POST ────────────────────────────────────────────────────
def test_decide_writes_audit_row_and_returns_panel(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="decide-1", report_hash="c" * 64)
    r = client.post(
        "/review/decide-1/decide",
        data={
            "decision": "approved",
            "reviewer": "jane.cco@firm.example",
            "rationale": "All items reviewed; signed off.",
            "report_hash": "c" * 64,
        },
    )
    assert r.status_code == 200
    # Returned partial contains the new decision
    assert 'id="decisions-panel"' in r.text
    assert "jane.cco@firm.example" in r.text
    assert "All items reviewed" in r.text
    # And a row was written
    rows = in_memory_session.exec(
        select(HumanReview).where(HumanReview.trace_id == "decide-1")
    ).all()
    assert len(rows) == 1
    assert rows[0].decision == "approved"
    assert rows[0].report_hash == "c" * 64


def test_decide_rejects_invalid_decision(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="bad-d", report_hash="d" * 64)
    r = client.post(
        "/review/bad-d/decide",
        data={
            "decision": "maybe",
            "reviewer": "jane",
            "rationale": "...",
            "report_hash": "d" * 64,
        },
    )
    assert r.status_code == 400


def test_decide_rejects_short_report_hash(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="short-hash", report_hash="e" * 64)
    r = client.post(
        "/review/short-hash/decide",
        data={
            "decision": "approved",
            "reviewer": "jane",
            "rationale": "ok",
            "report_hash": "tooshort",
        },
    )
    assert r.status_code == 400


def test_decide_404_on_unknown_trace(client: TestClient) -> None:
    r = client.post(
        "/review/no-such/decide",
        data={
            "decision": "approved",
            "reviewer": "jane",
            "rationale": "ok",
            "report_hash": "f" * 64,
        },
    )
    assert r.status_code == 404


def test_decide_appends_to_history_for_repeat_decisions(
    client: TestClient, in_memory_session: Session
) -> None:
    _seed_run(in_memory_session, trace_id="multi", report_hash="1" * 64)
    for dec, why in [
        ("revise", "Items 11/12 spans look short."),
        ("approved", "Re-ran; spans now correct."),
    ]:
        r = client.post(
            "/review/multi/decide",
            data={
                "decision": dec,
                "reviewer": "jane",
                "rationale": why,
                "report_hash": "1" * 64,
            },
        )
        assert r.status_code == 200

    rows = in_memory_session.exec(
        select(HumanReview).where(HumanReview.trace_id == "multi").order_by(HumanReview.ts)
    ).all()
    assert [r.decision for r in rows] == ["revise", "approved"]


# ── Run-from-UI POST ─────────────────────────────────────────────────
def test_run_from_ui_creates_row_and_schedules(
    client: TestClient,
    in_memory_session: Session,
    captured_schedules: list[tuple[str, object]],
) -> None:
    r = client.post(
        "/review/runs",
        data={"crd": "110181", "brochure_version_id": "1037550"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/review?queued=")

    rows = in_memory_session.exec(
        select(PipelineRun).where(PipelineRun.brochure_crd == "110181")
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "queued"
    assert rows[0].brochure_version_id == "1037550"
    assert len(captured_schedules) == 1
    assert captured_schedules[0][0] == rows[0].trace_id


def test_run_from_ui_accepts_blank_version_id(
    client: TestClient, in_memory_session: Session
) -> None:
    r = client.post(
        "/review/runs",
        data={"crd": "108000", "brochure_version_id": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rows = in_memory_session.exec(
        select(PipelineRun).where(PipelineRun.brochure_crd == "108000")
    ).all()
    assert len(rows) == 1
    assert rows[0].brochure_version_id is None


def test_run_from_ui_rejects_non_numeric_crd(
    client: TestClient, in_memory_session: Session
) -> None:
    r = client.post(
        "/review/runs",
        data={"crd": "not-a-crd", "brochure_version_id": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    rows = in_memory_session.exec(select(PipelineRun)).all()
    assert rows == []


def test_run_from_ui_rejects_non_numeric_version_id(
    client: TestClient, in_memory_session: Session
) -> None:
    r = client.post(
        "/review/runs",
        data={"crd": "108000", "brochure_version_id": "not-numeric"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    rows = in_memory_session.exec(select(PipelineRun)).all()
    assert rows == []


def test_run_from_ui_strips_whitespace(
    client: TestClient, in_memory_session: Session
) -> None:
    r = client.post(
        "/review/runs",
        data={"crd": "  110181  ", "brochure_version_id": "  1037550 "},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rows = in_memory_session.exec(
        select(PipelineRun).where(PipelineRun.brochure_crd == "110181")
    ).all()
    assert len(rows) == 1
    assert rows[0].brochure_version_id == "1037550"


def test_review_list_shows_queued_flash(
    client: TestClient, in_memory_session: Session
) -> None:
    r = client.get("/review?queued=advlens-test-flash")
    assert r.status_code == 200
    assert "advlens-test-flash" in r.text
    assert "Queued pipeline run" in r.text


def test_review_list_shows_error_flash(client: TestClient) -> None:
    r = client.get("/review?error=Something%20broke")
    assert r.status_code == 200
    assert "Something broke" in r.text
    assert "flash-bad" in r.text


def test_review_list_warns_when_anthropic_key_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adv_lens.app import settings as settings_module
    from adv_lens.app.web import routes as routes_module

    monkeypatch.setattr(settings_module.settings, "anthropic_api_key", "")
    monkeypatch.setattr(routes_module.settings, "anthropic_api_key", "")
    r = client.get("/review")
    assert r.status_code == 200
    assert "ANTHROPIC_API_KEY not set" in r.text


# ── Root redirect ────────────────────────────────────────────────────
def test_root_redirects_to_review(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/review"


# ── Seed CLI ─────────────────────────────────────────────────────────
def test_seed_loads_sample_state_into_pipeline_runs(
    in_memory_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed CLI should round-trip the on-disk sample into a PipelineRun row."""
    if not _SAMPLE_STATE.exists():
        pytest.skip(f"sample state not found at {_SAMPLE_STATE}")

    # Point the seed module at our in-memory engine
    engine = in_memory_session.get_bind()
    monkeypatch.setattr(seed_module, "engine", engine)

    trace_id = seed_module.seed_sample(_SAMPLE_STATE)

    with _SAMPLE_STATE.open(encoding="utf-8") as f:
        expected = json.load(f)
    assert trace_id == expected["trace_id"]

    rows = in_memory_session.exec(
        select(PipelineRun).where(PipelineRun.trace_id == trace_id)
    ).all()
    assert len(rows) == 1
    assert rows[0].status == "complete"
    assert rows[0].brochure_crd == str(expected["brochure_crd"])


def test_seed_is_idempotent(
    in_memory_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not _SAMPLE_STATE.exists():
        pytest.skip(f"sample state not found at {_SAMPLE_STATE}")

    engine = in_memory_session.get_bind()
    monkeypatch.setattr(seed_module, "engine", engine)

    seed_module.seed_sample(_SAMPLE_STATE)
    seed_module.seed_sample(_SAMPLE_STATE)  # second call should not raise

    with _SAMPLE_STATE.open(encoding="utf-8") as f:
        trace_id = json.load(f)["trace_id"]
    rows = in_memory_session.exec(
        select(PipelineRun).where(PipelineRun.trace_id == trace_id)
    ).all()
    assert len(rows) == 1  # not duplicated
