"""Review UI routes — list, detail, decision.

Routes mounted at ``/review`` by ``main.py``. Server-rendered Jinja2,
HTMX for the decision form. The redline body is reused verbatim from
``render_redline_html`` and served at ``/review/{trace_id}/redline.html``
for an iframe — no template surgery on the existing renderer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.storage.audit import HumanReview
from adv_lens.app.storage.db import get_session
from adv_lens.extractors.schemas import RedlineReport
from adv_lens.redline.render import render_redline_html

router = APIRouter(tags=["review-ui"])

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

SessionDep = Annotated[Session, Depends(get_session)]

_VALID_DECISIONS = {"approved", "rejected", "revise"}


def _redline_from_run(run: PipelineRun) -> RedlineReport | None:
    """Pull a `RedlineReport` from a stored pipeline-run JSON, or None."""
    if run.result is None:
        return None
    redline = run.result.get("redline") if isinstance(run.result, dict) else None
    if not redline:
        return None
    return RedlineReport.model_validate(redline)


def _report_hash_from_run(run: PipelineRun) -> str | None:
    if run.result is None or not isinstance(run.result, dict):
        return None
    return run.result.get("report_hash")


def _summary_for_run(run: PipelineRun) -> dict[str, object]:
    """Compact dict for the list view."""
    redline = _redline_from_run(run)
    score = redline.scorecard.overall_score if redline else None
    headline = redline.scorecard.headline if redline else None
    finding_count = len(redline.findings) if redline else 0
    return {
        "trace_id": run.trace_id,
        "brochure_crd": run.brochure_crd,
        "brochure_version_id": run.brochure_version_id,
        "status": run.status,
        "score": score,
        "headline": headline,
        "finding_count": finding_count,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }


@router.get("/review", response_class=HTMLResponse)
def review_list(request: Request, session: SessionDep) -> HTMLResponse:
    runs = session.exec(
        select(PipelineRun).order_by(PipelineRun.created_at.desc())  # type: ignore[union-attr]
    ).all()
    rows = [_summary_for_run(r) for r in runs]
    return templates.TemplateResponse(
        request,
        "review_list.html.j2",
        {"rows": rows},
    )


@router.get("/review/{trace_id}", response_class=HTMLResponse)
def review_detail(request: Request, trace_id: str, session: SessionDep) -> HTMLResponse:
    run = session.exec(
        select(PipelineRun).where(PipelineRun.trace_id == trace_id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"trace_id {trace_id!r} not found")

    decisions = session.exec(
        select(HumanReview)
        .where(HumanReview.trace_id == trace_id)
        .order_by(HumanReview.ts)  # type: ignore[arg-type]
    ).all()

    report_hash = _report_hash_from_run(run)
    redline = _redline_from_run(run)

    return templates.TemplateResponse(
        request,
        "review_detail.html.j2",
        {
            "run": run,
            "summary": _summary_for_run(run),
            "decisions": list(decisions),
            "report_hash": report_hash,
            "has_redline": redline is not None,
        },
    )


@router.get("/review/{trace_id}/redline.html", response_class=HTMLResponse)
def review_redline_iframe(trace_id: str, session: SessionDep) -> HTMLResponse:
    """Serve the existing standalone redline HTML, for the detail page's iframe.

    Reuses ``render_redline_html`` unchanged — same bytes the email/PDF path
    would produce.
    """
    run = session.exec(
        select(PipelineRun).where(PipelineRun.trace_id == trace_id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"trace_id {trace_id!r} not found")

    redline = _redline_from_run(run)
    if redline is None:
        return HTMLResponse(
            "<!doctype html><html><body style='font-family:sans-serif;padding:2em;color:#666'>"
            "<p><em>No redline produced for this run yet "
            f"(status: {run.status}).</em></p></body></html>"
        )

    meta = {
        "trace_id": run.trace_id,
        "brochure_sha256": (run.result or {}).get("brochure_sha256")
        if isinstance(run.result, dict)
        else None,
        "report_hash": _report_hash_from_run(run),
    }
    html = render_redline_html(redline, meta=meta)
    return HTMLResponse(html)


@router.post("/review/{trace_id}/decide", response_class=HTMLResponse)
def review_decide(
    request: Request,
    trace_id: str,
    session: SessionDep,
    decision: Annotated[str, Form()],
    reviewer: Annotated[str, Form()],
    rationale: Annotated[str, Form()],
    report_hash: Annotated[str, Form()],
) -> HTMLResponse:
    """Submit a CCO decision via HTMX form.

    Validates and writes a row to ``human_reviews`` then renders the
    updated decisions panel as an HTMX partial.
    """
    if decision not in _VALID_DECISIONS:
        raise HTTPException(status_code=400, detail=f"decision must be one of {_VALID_DECISIONS}")
    if not reviewer.strip():
        raise HTTPException(status_code=400, detail="reviewer is required")
    if not rationale.strip():
        raise HTTPException(status_code=400, detail="rationale is required")
    if len(rationale) > 4096:
        raise HTTPException(status_code=400, detail="rationale must be <=4096 chars")
    if len(report_hash) != 64 or any(c not in "0123456789abcdef" for c in report_hash):
        raise HTTPException(status_code=400, detail="report_hash must be 64-hex chars")

    run = session.exec(
        select(PipelineRun).where(PipelineRun.trace_id == trace_id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"trace_id {trace_id!r} not found")

    row = HumanReview(
        trace_id=trace_id,
        brochure_crd=run.brochure_crd,
        reviewer=reviewer.strip(),
        decision=decision,
        rationale=rationale.strip(),
        report_hash=report_hash,
    )
    session.add(row)
    session.commit()

    # Re-query the full decision history for the panel render
    decisions = session.exec(
        select(HumanReview)
        .where(HumanReview.trace_id == trace_id)
        .order_by(HumanReview.ts)  # type: ignore[arg-type]
    ).all()

    return templates.TemplateResponse(
        request,
        "decisions_panel.html.j2",
        {
            "decisions": list(decisions),
            "trace_id": trace_id,
            "report_hash": report_hash,
            "just_recorded": True,
            "now": datetime.now(UTC),
        },
    )


@router.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Send the bare host to /review so the demo entry point is obvious."""
    return RedirectResponse(url="/review", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
