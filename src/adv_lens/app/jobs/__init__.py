"""Async pipeline job persistence + scheduling.

A FastAPI request that triggers ``POST /pipeline/run`` doesn't block on the
~30-90s pipeline. It inserts a ``PipelineRun`` row with status=queued,
schedules the runner via ``asyncio.create_task``, and returns 202. Callers
poll ``GET /pipeline/run/{trace_id}``.

In-process scheduling is a deliberate week-3 choice — see ADR 0011 for why
not arq/procrastinate yet and how a swap looks.
"""

from adv_lens.app.jobs.models import (
    PipelineJobStatus,
    PipelineRun,
)
from adv_lens.app.jobs.runner import run_pipeline_job
from adv_lens.app.jobs.scheduler import schedule_pipeline_job

__all__ = [
    "PipelineJobStatus",
    "PipelineRun",
    "run_pipeline_job",
    "schedule_pipeline_job",
]
