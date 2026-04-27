"""Persisted pipeline-run state.

One row per ``POST /pipeline/run`` call. The ``status`` field walks
queued → running → (complete | failed). When ``complete``, ``result``
holds the full ``ADVState`` JSON (including the typed ``RedlineReport``
and ``review_status="pending_review"``); the audit table
``human_reviews`` joins on ``report_hash`` from inside that JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlmodel import JSON, Column, Field, SQLModel

PipelineJobStatus = Literal[
    "queued",
    "running",
    "complete",
    "failed",
]


class PipelineRun(SQLModel, table=True):
    """One pipeline invocation. Lives for the run's lifetime + audit retention."""

    __tablename__ = "pipeline_runs"

    id: int | None = Field(default=None, primary_key=True)
    trace_id: str = Field(index=True, unique=True)
    brochure_crd: str = Field(index=True)
    brochure_version_id: str | None = Field(default=None)

    status: str = Field(default="queued", index=True)  # PipelineJobStatus values

    # Populated when status transitions out of queued/running.
    result: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)

    # Lifecycle timestamps. created_at is set on insert; started_at when the
    # runner picks the job up; completed_at when it terminates either way.
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
