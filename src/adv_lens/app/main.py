from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from adv_lens import __version__
from adv_lens.app.graph.pipeline import new_trace_id
from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.jobs.scheduler import get_scheduler
from adv_lens.app.settings import settings
from adv_lens.app.storage.audit import HumanReview
from adv_lens.app.storage.db import get_session
from adv_lens.ingestion import IAPDClient
from adv_lens.ingestion.models import BrochureRef

# Annotated dependency form keeps Depends() out of default-argument position
# (ruff B008) while still letting tests override via app.dependency_overrides.
SessionDep = Annotated[Session, Depends(get_session)]
SchedulerDep = Annotated[object, Depends(get_scheduler)]

app = FastAPI(
    title="ADV-Lens",
    description="Form ADV Part 2A intelligence + peer benchmarking.",
    version=__version__,
)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, object]:
    """Liveness + component-configured probe.

    Returns component-level 'configured' booleans (not live pings) so the
    endpoint stays fast and doesn't cascade failures. Live dependency checks
    live on dedicated routes (``/brochure/{crd}`` exercises SEC IAPD).
    """
    return {
        "status": "ok",
        "version": __version__,
        "components": {
            "anthropic_configured": bool(settings.anthropic_api_key),
            "langfuse_configured": bool(
                settings.langfuse_public_key and settings.langfuse_secret_key
            ),
            "qdrant_url": settings.qdrant_url,
            "data_dir": str(settings.data_dir),
            "sec_rate_limit_rps": settings.sec_rate_limit_rps,
            "hitl_enabled": settings.enable_hitl,
        },
    }


@app.get("/brochure/{crd}", tags=["ingestion"])
async def list_brochures(crd: str) -> dict[str, object]:
    """List current Part 2A brochure references for a CRD.

    Metadata only — doesn't download PDFs. Use the CLI (``python -m
    adv_lens.ingestion.cli fetch-brochure <CRD>``) for the bytes.
    """
    if not crd.strip().isdigit():
        raise HTTPException(status_code=400, detail="crd must be numeric")
    async with IAPDClient() as client:
        refs: list[BrochureRef] = await client.list_current_brochures(crd)
    return {"crd": crd, "count": len(refs), "brochures": [r.model_dump() for r in refs]}


class PipelineRunRequest(BaseModel):
    crd: str = Field(min_length=1, pattern=r"^\d+$")
    brochure_version_id: str | None = Field(default=None, pattern=r"^\d+$")
    trace_id: str | None = None


class PipelineRunAccepted(BaseModel):
    """202 response for ``POST /pipeline/run``.

    The caller polls ``status_url`` until ``status`` reaches ``complete``
    (with a populated ``result.redline``) or ``failed``. See ADR 0011.
    """

    trace_id: str
    status: Literal["queued"]
    status_url: str


@app.post(
    "/pipeline/run",
    tags=["pipeline"],
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PipelineRunAccepted,
)
def pipeline_run(
    body: PipelineRunRequest,
    session: SessionDep,
    scheduler: SchedulerDep,
) -> PipelineRunAccepted:
    """Enqueue a pipeline run, return 202 immediately.

    The pipeline takes 30-90s end-to-end on a real brochure (three Sonnet
    /Haiku extractor calls + Opus redline + cross-encoder rerank). Holding
    a sync HTTP connection that long doesn't scale and times out behind
    most reverse proxies.

    The handler inserts a ``PipelineRun`` row with status=queued, schedules
    the runner, and returns the trace_id + status URL. Poll
    ``GET /pipeline/run/{trace_id}`` for progress.
    """
    trace_id = body.trace_id or new_trace_id()
    session.add(
        PipelineRun(
            trace_id=trace_id,
            brochure_crd=body.crd,
            brochure_version_id=body.brochure_version_id,
            status="queued",
        )
    )
    session.commit()

    # Build a per-task session factory bound to the same engine, so the
    # background task gets its own short-lived sessions.
    engine = session.get_bind()

    def _factory() -> Session:
        return Session(engine)

    scheduler(trace_id, _factory)  # type: ignore[operator]

    return PipelineRunAccepted(
        trace_id=trace_id,
        status="queued",
        status_url=f"/pipeline/run/{trace_id}",
    )


class PipelineRunStatus(BaseModel):
    """GET /pipeline/run/{trace_id} response shape."""

    trace_id: str
    brochure_crd: str
    brochure_version_id: str | None
    status: str
    result: dict | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@app.get(
    "/pipeline/run/{trace_id}",
    tags=["pipeline"],
    response_model=PipelineRunStatus,
)
def get_pipeline_run(trace_id: str, session: SessionDep) -> PipelineRun:
    row = session.exec(select(PipelineRun).where(PipelineRun.trace_id == trace_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"trace_id {trace_id!r} not found")
    return row


# ── HITL decision endpoints (Day 12) ───────────────────────────────────
ReviewDecision = Literal["approved", "rejected", "revise"]


class DecisionRequest(BaseModel):
    """A CCO's decision on a pending RedlineReport.

    `report_hash` is the SHA256 the gate computed (``state.report_hash``).
    Sending it on the decision means the audit row pins to the exact bytes
    the reviewer saw — a later report regeneration with new numbers gets
    a new hash and won't satisfy the same approval.
    """

    trace_id: str = Field(min_length=1)
    brochure_crd: str = Field(min_length=1, pattern=r"^\d+$")
    report_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1)
    decision: ReviewDecision
    rationale: str = Field(min_length=1, max_length=4096)


class DecisionResponse(BaseModel):
    id: int
    trace_id: str
    brochure_crd: str
    reviewer: str
    decision: str
    report_hash: str
    ts: datetime


@app.post(
    "/report/decision",
    tags=["hitl"],
    status_code=status.HTTP_201_CREATED,
    response_model=DecisionResponse,
)
def post_decision(body: DecisionRequest, session: SessionDep) -> HumanReview:
    """Record a CCO decision on a pending RedlineReport.

    Writes one row to ``human_reviews``. Idempotency is up to the caller —
    re-posting the same decision creates a new row, which is the right
    audit semantic ("the reviewer acted twice").
    """
    row = HumanReview(
        trace_id=body.trace_id,
        brochure_crd=body.brochure_crd,
        reviewer=body.reviewer,
        decision=body.decision,
        rationale=body.rationale,
        report_hash=body.report_hash,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.get(
    "/report/decision/{trace_id}",
    tags=["hitl"],
    response_model=list[DecisionResponse],
)
def list_decisions(trace_id: str, session: SessionDep) -> list[HumanReview]:
    """All decisions recorded for one trace_id, oldest first.

    Multiple decisions on the same trace_id are normal: a "revise"
    decision is followed by a re-run and a new "approved" decision. The
    audit trail keeps every step.
    """
    rows = session.exec(
        select(HumanReview).where(HumanReview.trace_id == trace_id).order_by(HumanReview.ts)
    ).all()
    return list(rows)
