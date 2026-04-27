"""Stuck-row reaper for the ``pipeline_runs`` table.

Per ADR 0011 the in-process pipeline worker dies on process restart, OOM,
or network partition — any in-flight job stays in ``status="running"``
forever. The reaper sweeps those rows after a configurable threshold and
marks them ``status="failed"`` with an ``error="reaped: ..."`` so the
caller polling ``GET /pipeline/run/{trace_id}`` always reaches a terminal
state.

Operational shape:

- **Function** (`reap_stuck_runs`) — the core; testable, callable from
  any context (CLI, lifespan task, future cron job).
- **CLI** (`python -m adv_lens.app.jobs.reaper`) — cron-friendly. Prints
  the count reaped (and per-row trace_ids on ``--verbose``).

This file deliberately does NOT install a periodic background task in
the FastAPI lifespan. That's a deployment-shape decision (cron vs
in-process loop vs k8s job) that belongs in the deployment runbook,
not in code that ships.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings

logger = logging.getLogger(__name__)

REAPED_ERROR_PREFIX = "reaped: stuck in running for >"


def reap_stuck_runs(
    session: Session,
    *,
    threshold_minutes: int | None = None,
    settings: Settings = default_settings,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[str]:
    """Mark stuck ``running`` rows as ``failed``. Returns reaped trace_ids.

    A row is reaped iff:

    - ``status == "running"``
    - ``started_at IS NOT NULL`` (defensive — should always be true for running rows)
    - ``started_at < now - threshold``

    ``dry_run=True`` returns the trace_ids that would be reaped without
    mutating any rows. Useful for the ``--dry-run`` CLI flag and for
    operators sanity-checking before blowing away in-flight work.

    The reaper does not delete rows; it transitions them to a terminal
    state so the audit trail of "this run was attempted" survives.
    """
    threshold = (
        threshold_minutes
        if threshold_minutes is not None
        else (settings.pipeline_run_reap_threshold_minutes)
    )
    now_ts = now or datetime.now(UTC)
    cutoff = now_ts - timedelta(minutes=threshold)

    candidates = list(
        session.exec(
            select(PipelineRun).where(
                PipelineRun.status == "running",
                PipelineRun.started_at.is_not(None),  # type: ignore[union-attr]
                PipelineRun.started_at < cutoff,  # type: ignore[operator]
            )
        ).all()
    )

    reaped_trace_ids: list[str] = []
    for row in candidates:
        reaped_trace_ids.append(row.trace_id)
        if dry_run:
            continue
        row.status = "failed"
        row.error = f"{REAPED_ERROR_PREFIX}{threshold}min (worker died or stalled)"
        row.completed_at = now_ts
        session.add(row)

    if not dry_run and reaped_trace_ids:
        session.commit()

    if reaped_trace_ids:
        logger.warning(
            "reaper: %s %d stuck pipeline_runs row(s) (threshold=%dmin)",
            "would reap" if dry_run else "reaped",
            len(reaped_trace_ids),
            threshold,
        )

    return reaped_trace_ids


# ── CLI ────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adv-lens-reaper",
        description="Sweep stuck pipeline_runs rows (status=running past threshold).",
    )
    p.add_argument(
        "--threshold-minutes",
        type=int,
        default=None,
        help="Override settings.pipeline_run_reap_threshold_minutes.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be reaped without mutating any rows.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each reaped trace_id, one per line.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Lazy import the engine so the CLI doesn't fail at import time when
    # the DSN isn't configured (e.g. running --help in CI).
    from adv_lens.app.storage.db import engine

    with Session(engine) as session:
        reaped = reap_stuck_runs(
            session,
            threshold_minutes=args.threshold_minutes,
            dry_run=args.dry_run,
        )

    verb = "would reap" if args.dry_run else "reaped"
    print(f"{verb}: {len(reaped)} stuck pipeline_runs row(s)")
    if args.verbose and reaped:
        for tid in reaped:
            print(f"  - {tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
