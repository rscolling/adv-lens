"""Stuck-row reaper tests for pipeline_runs."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.jobs.reaper import REAPED_ERROR_PREFIX, reap_stuck_runs
from adv_lens.app.storage.audit import HumanReview  # noqa: F401  ensure tables registered


# ── Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture()
def session() -> Iterator[Session]:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _now() -> datetime:
    # Anchor wall-clock so tests are deterministic.
    return datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


def _row(
    trace_id: str,
    status: str = "running",
    started_minutes_ago: int | None = None,
    completed: bool = False,
) -> PipelineRun:
    started_at = (
        _now() - timedelta(minutes=started_minutes_ago) if started_minutes_ago is not None else None
    )
    return PipelineRun(
        trace_id=trace_id,
        brochure_crd="108000",
        brochure_version_id="v1",
        status=status,
        started_at=started_at,
        completed_at=_now() if completed else None,
    )


# ── Core function ────────────────────────────────────────────────────
def test_reaps_running_row_older_than_threshold(session: Session) -> None:
    session.add(_row("trace-stuck", status="running", started_minutes_ago=15))
    session.commit()

    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now())
    assert reaped == ["trace-stuck"]

    row = session.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-stuck")).one()
    assert row.status == "failed"
    assert row.error is not None and row.error.startswith(REAPED_ERROR_PREFIX)
    # SQLite drops tzinfo on DateTime round-trip; compare via replace().
    assert row.completed_at is not None
    assert row.completed_at.replace(tzinfo=UTC) == _now()


def test_leaves_fresh_running_row_alone(session: Session) -> None:
    session.add(_row("trace-fresh", status="running", started_minutes_ago=2))
    session.commit()

    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now())
    assert reaped == []

    row = session.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-fresh")).one()
    assert row.status == "running"
    assert row.error is None


def test_leaves_terminal_rows_alone_even_if_old(session: Session) -> None:
    """Reaper only touches `running`. Complete and failed rows are immutable."""
    session.add(
        _row("trace-old-complete", status="complete", started_minutes_ago=999, completed=True)
    )
    session.add(_row("trace-old-failed", status="failed", started_minutes_ago=999, completed=True))
    session.commit()

    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now())
    assert reaped == []
    rows = list(session.exec(select(PipelineRun)).all())
    assert {r.status for r in rows} == {"complete", "failed"}


def test_leaves_queued_row_alone(session: Session) -> None:
    """A row stuck in `queued` (no started_at) isn't a reaper concern."""
    session.add(_row("trace-queued", status="queued", started_minutes_ago=None))
    session.commit()

    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now())
    assert reaped == []
    row = session.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-queued")).one()
    assert row.status == "queued"


def test_dry_run_reports_without_mutating(session: Session) -> None:
    session.add(_row("trace-stuck-1", status="running", started_minutes_ago=15))
    session.add(_row("trace-stuck-2", status="running", started_minutes_ago=20))
    session.commit()

    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now(), dry_run=True)
    assert sorted(reaped) == ["trace-stuck-1", "trace-stuck-2"]

    rows = list(session.exec(select(PipelineRun)).all())
    assert {r.status for r in rows} == {"running"}
    assert all(r.error is None for r in rows)


def test_threshold_boundary_inclusive(session: Session) -> None:
    """A row right at the threshold (started exactly N minutes ago) is NOT reaped.

    The cutoff is `started_at < now - threshold` (strict), so a row at the
    boundary is borderline-fresh and we leave it.
    """
    session.add(_row("trace-edge", status="running", started_minutes_ago=10))
    session.commit()

    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now())
    assert reaped == []


def test_uses_settings_default_when_threshold_omitted(session: Session) -> None:
    from adv_lens.app.settings import Settings

    s = Settings(pipeline_run_reap_threshold_minutes=5)
    session.add(_row("trace-stuck", status="running", started_minutes_ago=8))
    session.commit()

    reaped = reap_stuck_runs(session, settings=s, now=_now())
    assert reaped == ["trace-stuck"]


def test_handles_empty_table_cleanly(session: Session) -> None:
    reaped = reap_stuck_runs(session, threshold_minutes=10, now=_now())
    assert reaped == []


def test_reaper_message_includes_threshold(session: Session) -> None:
    session.add(_row("trace-stuck", status="running", started_minutes_ago=15))
    session.commit()
    reap_stuck_runs(session, threshold_minutes=7, now=_now())
    row = session.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-stuck")).one()
    assert "7min" in (row.error or "")


# ── CLI ──────────────────────────────────────────────────────────────
def test_cli_help_lists_flags() -> None:
    from adv_lens.app.jobs.reaper import _build_parser

    parser = _build_parser()
    help_text = parser.format_help()
    assert "--threshold-minutes" in help_text
    assert "--dry-run" in help_text
    assert "--verbose" in help_text


def test_cli_dry_run_prints_count_and_returns_zero(session: Session, monkeypatch, capsys) -> None:
    """End-to-end CLI smoke with the engine swapped to the in-memory test DB."""
    from adv_lens.app.jobs import reaper as mod

    session.add(_row("trace-stuck-cli", status="running", started_minutes_ago=15))
    session.commit()

    monkeypatch.setattr("adv_lens.app.storage.db.engine", session.get_bind(), raising=False)

    rc = mod.main(["--dry-run", "--threshold-minutes", "10", "--verbose"])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "would reap: 1" in captured
    assert "trace-stuck-cli" in captured

    # Dry-run didn't touch state.
    row = session.exec(select(PipelineRun).where(PipelineRun.trace_id == "trace-stuck-cli")).one()
    assert row.status == "running"
