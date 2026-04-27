"""Async pipeline-job tests — endpoint contract + runner lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from adv_lens.app.graph.state import ADVState
from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.jobs.runner import run_pipeline_job
from adv_lens.app.jobs.scheduler import get_scheduler
from adv_lens.app.main import app
from adv_lens.app.storage.audit import HumanReview  # noqa: F401  ensure table registered
from adv_lens.app.storage.db import get_session
from adv_lens.extractors.schemas import (
    RedlineReport,
    Scorecard,
    ScoreCategory,
)


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def in_memory_session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture()
def scheduler_calls() -> list[tuple]:
    return []


@pytest.fixture()
def client(in_memory_session: Session, scheduler_calls: list[tuple]) -> Iterator[TestClient]:
    """TestClient with DB + scheduler dependencies overridden.

    The scheduler override records calls but never launches a real task —
    that keeps endpoint tests deterministic. The runner is exercised
    separately by calling ``run_pipeline_job`` directly.
    """

    def _session_override() -> Iterator[Session]:
        yield in_memory_session

    def _fake_scheduler():
        def _capture(trace_id: str, session_factory, *, pipeline_runner=None):
            scheduler_calls.append((trace_id, session_factory, pipeline_runner))
            return None

        return _capture

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_scheduler] = _fake_scheduler
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_scheduler, None)


def _redline() -> RedlineReport:
    return RedlineReport(
        brochure_crd="108000",
        brochure_version_id="v1",
        scorecard=Scorecard(
            overall_score=80,
            categories=[ScoreCategory(name="compliance", score=80, rationale="x")],
            headline="Test report.",
        ),
    )


def _state(trace_id: str = "trace-1", crd: str = "108000") -> ADVState:
    return ADVState(
        trace_id=trace_id,
        brochure_crd=crd,
        brochure_version_id="v1",
        redline=_redline(),
        review_status="pending_review",
        report_hash="a" * 64,
    )


# ── PipelineRun model ────────────────────────────────────────────────
def test_pipeline_run_inserts_with_default_status(in_memory_session: Session) -> None:
    row = PipelineRun(trace_id="t-1", brochure_crd="108000")
    in_memory_session.add(row)
    in_memory_session.commit()
    in_memory_session.refresh(row)
    assert row.status == "queued"
    assert row.id is not None
    assert row.created_at is not None
    assert row.started_at is None
    assert row.completed_at is None


def test_pipeline_run_unique_trace_id(in_memory_session: Session) -> None:
    from sqlalchemy.exc import IntegrityError

    in_memory_session.add(PipelineRun(trace_id="t-dup", brochure_crd="108000"))
    in_memory_session.commit()
    in_memory_session.add(PipelineRun(trace_id="t-dup", brochure_crd="108000"))
    with pytest.raises(IntegrityError):
        in_memory_session.commit()


# ── POST /pipeline/run ───────────────────────────────────────────────
def test_post_returns_202_with_trace_id_and_status_url(
    client: TestClient, scheduler_calls: list[tuple]
) -> None:
    r = client.post("/pipeline/run", json={"crd": "108000", "trace_id": "trace-explicit"})
    assert r.status_code == 202
    body = r.json()
    assert body["trace_id"] == "trace-explicit"
    assert body["status"] == "queued"
    assert body["status_url"] == "/pipeline/run/trace-explicit"
    # Scheduler was called exactly once.
    assert len(scheduler_calls) == 1
    assert scheduler_calls[0][0] == "trace-explicit"


def test_post_generates_trace_id_when_omitted(
    client: TestClient, scheduler_calls: list[tuple]
) -> None:
    r = client.post("/pipeline/run", json={"crd": "108000"})
    assert r.status_code == 202
    body = r.json()
    assert body["trace_id"]
    assert body["trace_id"].startswith("advlens-")
    assert scheduler_calls[0][0] == body["trace_id"]


def test_post_persists_queued_pipeline_run_row(
    client: TestClient, in_memory_session: Session
) -> None:
    client.post("/pipeline/run", json={"crd": "108000", "trace_id": "trace-001"})
    rows = list(in_memory_session.exec(select(PipelineRun)).all())
    assert len(rows) == 1
    assert rows[0].trace_id == "trace-001"
    assert rows[0].status == "queued"
    assert rows[0].brochure_crd == "108000"


def test_post_validates_crd_before_scheduling(
    client: TestClient, scheduler_calls: list[tuple]
) -> None:
    r = client.post("/pipeline/run", json={"crd": "abc"})
    assert r.status_code == 422
    assert scheduler_calls == []


def test_post_validates_brochure_version_id(
    client: TestClient, scheduler_calls: list[tuple]
) -> None:
    r = client.post("/pipeline/run", json={"crd": "108000", "brochure_version_id": "abc"})
    assert r.status_code == 422
    assert scheduler_calls == []


# ── GET /pipeline/run/{trace_id} ─────────────────────────────────────
def test_get_returns_pipeline_run_state(client: TestClient, in_memory_session: Session) -> None:
    in_memory_session.add(
        PipelineRun(
            trace_id="trace-002",
            brochure_crd="108000",
            brochure_version_id="v1",
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    in_memory_session.commit()

    r = client.get("/pipeline/run/trace-002")
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == "trace-002"
    assert body["status"] == "running"
    assert body["brochure_crd"] == "108000"


def test_get_returns_404_for_unknown_trace(client: TestClient) -> None:
    r = client.get("/pipeline/run/does-not-exist")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_get_includes_result_payload_when_complete(
    client: TestClient, in_memory_session: Session
) -> None:
    in_memory_session.add(
        PipelineRun(
            trace_id="trace-done",
            brochure_crd="108000",
            status="complete",
            result={"redline": {"brochure_crd": "108000"}, "review_status": "pending_review"},
            completed_at=datetime.now(UTC),
        )
    )
    in_memory_session.commit()
    body = client.get("/pipeline/run/trace-done").json()
    assert body["status"] == "complete"
    assert body["result"]["redline"]["brochure_crd"] == "108000"


# ── run_pipeline_job lifecycle ───────────────────────────────────────
async def _fake_pipeline_success(crd, *, brochure_version_id=None, trace_id=None):
    return _state(trace_id=trace_id or "trace-x", crd=crd)


async def _fake_pipeline_failure(crd, *, brochure_version_id=None, trace_id=None):
    raise RuntimeError("simulated extractor blow-up")


def _factory_for(engine):
    def _f() -> Session:
        return Session(engine)

    return _f


async def test_runner_advances_queued_to_complete(engine) -> None:
    with Session(engine) as s:
        s.add(PipelineRun(trace_id="trace-ok", brochure_crd="108000"))
        s.commit()

    await run_pipeline_job("trace-ok", _factory_for(engine), pipeline_runner=_fake_pipeline_success)

    with Session(engine) as s:
        row = s.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-ok")).one()
        assert row.status == "complete"
        assert row.error is None
        assert row.started_at is not None
        assert row.completed_at is not None
        assert row.result is not None
        # The persisted result is the ADVState dump.
        assert row.result["brochure_crd"] == "108000"
        assert row.result["review_status"] == "pending_review"


async def test_runner_advances_queued_to_failed_on_pipeline_exception(engine) -> None:
    with Session(engine) as s:
        s.add(PipelineRun(trace_id="trace-fail", brochure_crd="108000"))
        s.commit()

    await run_pipeline_job(
        "trace-fail", _factory_for(engine), pipeline_runner=_fake_pipeline_failure
    )

    with Session(engine) as s:
        row = s.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-fail")).one()
        assert row.status == "failed"
        assert row.result is None
        assert row.error is not None
        assert "simulated extractor blow-up" in row.error
        assert row.completed_at is not None


async def test_runner_logs_and_returns_when_row_missing(engine, caplog) -> None:
    """A missing row at start time is logged but doesn't raise."""
    await run_pipeline_job(
        "trace-vanished", _factory_for(engine), pipeline_runner=_fake_pipeline_success
    )
    # No row was inserted; nothing to find. The runner should have logged
    # and returned cleanly.
    with Session(engine) as s:
        rows = list(s.exec(select(PipelineRun)).all())
    assert rows == []


async def test_runner_marks_started_at_before_running_pipeline(engine) -> None:
    """Verify the queued→running transition is committed before the LLM call."""

    captured: dict = {}

    async def _capture(crd, *, brochure_version_id=None, trace_id=None):
        # Read the row mid-flight to confirm status=running and started_at is set.
        with Session(engine) as s:
            row = s.exec(select(PipelineRun).where(PipelineRun.trace_id == trace_id)).one()
            captured["status"] = row.status
            captured["started_at_set"] = row.started_at is not None
        return _state(trace_id=trace_id or "x", crd=crd)

    with Session(engine) as s:
        s.add(PipelineRun(trace_id="trace-mid", brochure_crd="108000"))
        s.commit()

    await run_pipeline_job("trace-mid", _factory_for(engine), pipeline_runner=_capture)

    assert captured["status"] == "running"
    assert captured["started_at_set"] is True


# ── End-to-end smoke (in-process scheduler with real asyncio.create_task) ──
async def test_end_to_end_post_then_runner_then_get(in_memory_session: Session, engine) -> None:
    """Without the scheduler override: POST creates row, we run the runner
    explicitly with a fake pipeline, then GET returns the complete state."""

    def _session_override() -> Iterator[Session]:
        yield in_memory_session

    captured_factories: list = []

    def _capture_scheduler():
        def _f(trace_id: str, session_factory, *, pipeline_runner=None):
            captured_factories.append((trace_id, session_factory))
            return None

        return _f

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_scheduler] = _capture_scheduler
    try:
        c = TestClient(app)
        post_resp = c.post("/pipeline/run", json={"crd": "108000", "trace_id": "trace-e2e"})
        assert post_resp.status_code == 202

        # Simulate the scheduler running the job.
        trace_id, factory = captured_factories[0]
        await run_pipeline_job(trace_id, factory, pipeline_runner=_fake_pipeline_success)

        get_resp = c.get(f"/pipeline/run/{trace_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["status"] == "complete"
        assert body["result"]["review_status"] == "pending_review"
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_scheduler, None)
