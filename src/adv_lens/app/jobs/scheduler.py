"""Pipeline-job scheduler.

Production: ``asyncio.create_task`` schedules the runner inside the
FastAPI event loop. The task continues after the request returns. It
dies on process restart — the Day-14 reaper handles "stuck running"
rows. ADR 0011 has the full restart-semantics discussion.

Tests override via ``app.dependency_overrides[get_scheduler]`` so they
can capture the call without actually launching a task — keeps async
tests deterministic.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from sqlmodel import Session

from adv_lens.app.jobs.runner import PipelineRunner, run_pipeline_job

logger = logging.getLogger(__name__)


def schedule_pipeline_job(
    trace_id: str,
    session_factory: Callable[[], Session],
    *,
    pipeline_runner: PipelineRunner | None = None,
) -> Any:
    """Schedule ``run_pipeline_job`` and return the task handle.

    Production: returns the asyncio.Task. Tests typically override this
    function to record the call without launching anything; the runner
    itself is exercised separately.
    """
    return asyncio.create_task(
        run_pipeline_job(trace_id, session_factory, pipeline_runner=pipeline_runner),
        name=f"pipeline_job:{trace_id}",
    )


def get_scheduler() -> Callable:
    """FastAPI dependency hook — production returns the real scheduler.

    Override via ``app.dependency_overrides[get_scheduler] = lambda: my_fake``
    to swap out scheduling behaviour in tests.
    """
    return schedule_pipeline_job
