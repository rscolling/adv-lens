"""Async pipeline-job runner.

Reads a ``PipelineRun`` row by trace_id, advances its lifecycle:

    queued → running → (complete | failed)

Each transition is its own short transaction (open, mutate, commit, close)
so a long-running pipeline doesn't hold a Session open for minutes — that
would block other requests on the same connection in single-connection
test setups (StaticPool) and is bad form generally.

The pipeline runner is injectable so tests can pass a fast canned coroutine.
Production gets ``adv_lens.app.graph.pipeline.run_pipeline``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlmodel import Session, select

from adv_lens.app.graph.state import ADVState
from adv_lens.app.jobs.models import PipelineRun

logger = logging.getLogger(__name__)

# Type alias for the injected pipeline runner. Matches run_pipeline's signature.
PipelineRunner = Callable[..., Awaitable[ADVState]]


async def run_pipeline_job(
    trace_id: str,
    session_factory: Callable[[], Session],
    *,
    pipeline_runner: PipelineRunner | None = None,
) -> None:
    """Drive one PipelineRun row through its lifecycle.

    Never raises — failures land in ``row.error`` with status=failed so the
    caller polling ``GET /pipeline/run/{trace_id}`` always sees a terminal
    state, never a stuck "running".
    """
    runner = pipeline_runner or _default_pipeline_runner()

    # ── queued → running ───────────────────────────────────────────────
    crd: str | None = None
    vid: str | None = None
    try:
        with session_factory() as session:
            row = _fetch(session, trace_id)
            if row is None:
                logger.error("run_pipeline_job: no PipelineRun row for trace_id=%s", trace_id)
                return
            row.status = "running"
            row.started_at = datetime.now(UTC)
            session.add(row)
            session.commit()
            crd = row.brochure_crd
            vid = row.brochure_version_id
    except Exception as e:
        logger.exception("run_pipeline_job: could not transition to running: %s", e)
        return

    # ── running → complete | failed ────────────────────────────────────
    result_payload: dict | None = None
    error_message: str | None = None
    try:
        state = await runner(crd, brochure_version_id=vid, trace_id=trace_id)
        result_payload = state.model_dump(mode="json")
    except Exception as e:
        logger.exception("run_pipeline_job: pipeline raised for trace_id=%s", trace_id)
        error_message = f"{type(e).__name__}: {e}"

    final_status = "complete" if error_message is None else "failed"

    try:
        with session_factory() as session:
            row = _fetch(session, trace_id)
            if row is None:
                # Row deleted mid-flight — nothing to update. Loud log; don't raise.
                logger.error("run_pipeline_job: row vanished while running trace_id=%s", trace_id)
                return
            row.status = final_status
            row.result = result_payload
            row.error = error_message
            row.completed_at = datetime.now(UTC)
            session.add(row)
            session.commit()
    except Exception as e:
        # If even the terminal write fails the row is stuck "running". A
        # Day-14 reaper sweeps stuck rows after a timeout.
        logger.exception("run_pipeline_job: terminal write failed for trace_id=%s: %s", trace_id, e)


def _fetch(session: Session, trace_id: str) -> PipelineRun | None:
    return session.exec(select(PipelineRun).where(PipelineRun.trace_id == trace_id)).first()


def _default_pipeline_runner() -> PipelineRunner:
    """Lazy import so tests using a fake runner don't pull the LangGraph stack."""
    from adv_lens.app.graph.pipeline import run_pipeline

    return run_pipeline
