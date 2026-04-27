"""Review UI routes — list, detail, decision.

Routes mounted at ``/review`` by ``main.py``. Server-rendered Jinja2,
HTMX for the decision form. The redline body is reused verbatim from
``render_redline_html`` and served at ``/review/{trace_id}/redline.html``
for an iframe — no template surgery on the existing renderer.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from adv_lens.app.graph.pipeline import new_trace_id
from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.jobs.scheduler import get_scheduler
from adv_lens.app.settings import settings
from adv_lens.app.storage.audit import HumanReview
from adv_lens.app.storage.db import get_session
from adv_lens.extractors.schemas import RedlineReport
from adv_lens.redline.render import render_redline_html

router = APIRouter(tags=["review-ui"])

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

SessionDep = Annotated[Session, Depends(get_session)]
SchedulerDep = Annotated[object, Depends(get_scheduler)]

_VALID_DECISIONS = {"approved", "rejected", "revise"}
_CRD_OK = re.compile(r"^\d+$")

# 25MB ceiling on uploads — covers every real-world Part 2A brochure
# (Brown Advisory's is 666KB; the largest in the field-test set was ~3MB).
# Defends against accidental gigabyte-scale uploads pegging memory.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Synthetic-CRD prefix for drafts. Real SEC CRDs rarely exceed 8 digits
# in 2026; "99" + 10 hash-derived digits gives 12 digits that are
# trivially distinguishable from real ones.
_DRAFT_CRD_PREFIX = "99"


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
def review_list(
    request: Request,
    session: SessionDep,
    queued: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    runs = session.exec(
        select(PipelineRun).order_by(PipelineRun.created_at.desc())  # type: ignore[union-attr]
    ).all()
    rows = [_summary_for_run(r) for r in runs]
    return templates.TemplateResponse(
        request,
        "review_list.html.j2",
        {
            "rows": rows,
            "anthropic_configured": bool(settings.anthropic_api_key),
            "queued_trace_id": queued,
            "error_message": error,
        },
    )


@router.post("/review/runs", response_class=HTMLResponse)
def schedule_run_from_ui(
    session: SessionDep,
    scheduler: SchedulerDep,
    crd: Annotated[str, Form()],
    brochure_version_id: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Kick off a pipeline run from the UI form.

    Mirrors ``POST /pipeline/run`` but accepts form-encoded input and
    redirects back to the dashboard. Validation is intentionally tighter
    than the JSON endpoint's because a stray space pasted into a CRD
    field would otherwise hit the SEC fetcher and 404.
    """
    crd_clean = crd.strip()
    vid_clean = brochure_version_id.strip()

    if not _CRD_OK.match(crd_clean):
        return RedirectResponse(
            url=f"/review?error={_url_encode('CRD must be numeric')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if vid_clean and not _CRD_OK.match(vid_clean):
        return RedirectResponse(
            url=f"/review?error={_url_encode('Brochure version ID must be numeric if provided')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    trace_id = new_trace_id()
    session.add(
        PipelineRun(
            trace_id=trace_id,
            brochure_crd=crd_clean,
            brochure_version_id=vid_clean or None,
            status="queued",
        )
    )
    session.commit()

    engine = session.get_bind()

    def _factory() -> Session:
        return Session(engine)

    scheduler(trace_id, _factory)  # type: ignore[operator]

    return RedirectResponse(
        url=f"/review?queued={trace_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _url_encode(s: str) -> str:
    """Tiny percent-encoder for redirect query strings (avoids urllib import noise)."""
    from urllib.parse import quote

    return quote(s, safe="")


def _synthetic_crd_for_draft(sha256: str) -> str:
    """Deterministic numeric pseudo-CRD for an uploaded draft brochure.

    Same content uploaded twice gets the same identifier, which dedupes the
    on-disk cache naturally. The 99-prefix marks it as not-a-real-CRD.
    """
    n = int(sha256[:8], 16) % 10**10
    return f"{_DRAFT_CRD_PREFIX}{n:010d}"


def _draft_label_slug(label: str) -> str:
    """Sanitize a free-text firm label into a URL-safe slug for trace_id."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return cleaned[:32]


@router.post("/review/runs/upload", response_class=HTMLResponse)
async def schedule_run_from_upload(
    session: SessionDep,
    scheduler: SchedulerDep,
    pdf: Annotated[UploadFile, File()],
    firm_label: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Score a pre-file (draft) brochure uploaded as a PDF.

    Different from the CRD path in two ways: (a) we already have the bytes,
    so we write them to the same cache path ``fetch_brochure_node`` would
    populate and the existing pipeline runs unchanged; (b) the brochure has
    no real CRD/version yet, so we fabricate numeric ones (synthetic CRD
    with a "99" prefix) that the rest of the pipeline treats normally.

    Returns 303 redirect to ``/review`` with a queued/error flash.
    """
    if pdf.filename is None:
        return RedirectResponse(
            url=f"/review?error={_url_encode('No file selected')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    contents = await pdf.read()
    if len(contents) == 0:
        return RedirectResponse(
            url=f"/review?error={_url_encode('Uploaded file is empty')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if len(contents) > _MAX_UPLOAD_BYTES:
        return RedirectResponse(
            url=(
                "/review?error="
                + _url_encode(f"PDF too large (>{_MAX_UPLOAD_BYTES // 1024 // 1024}MB)")
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if not contents.startswith(b"%PDF"):
        return RedirectResponse(
            url=f"/review?error={_url_encode('Upload must be a PDF (missing %PDF header)')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    sha = hashlib.sha256(contents).hexdigest()
    synthetic_crd = _synthetic_crd_for_draft(sha)
    synthetic_vid = "0"

    # Write to the cache path fetch_brochure_node would otherwise populate.
    # The fetch node's iapd.fetch_brochure(ref) checks path.exists() first,
    # so it returns this cached bytes without ever hitting SEC IAPD.
    cache_path = settings.data_dir / "brochures" / synthetic_crd / f"{synthetic_vid}.pdf"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        cache_path.write_bytes(contents)

    label_slug = _draft_label_slug(firm_label)
    if label_slug:
        trace_id = f"draft-{label_slug}-{sha[:8]}"
    else:
        trace_id = f"draft-{sha[:8]}-{new_trace_id().split('-', 1)[1]}"

    session.add(
        PipelineRun(
            trace_id=trace_id,
            brochure_crd=synthetic_crd,
            brochure_version_id=synthetic_vid,
            status="queued",
        )
    )
    session.commit()

    engine = session.get_bind()

    def _factory() -> Session:
        return Session(engine)

    scheduler(trace_id, _factory)  # type: ignore[operator]

    return RedirectResponse(
        url=f"/review?queued={trace_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/review/{trace_id}", response_class=HTMLResponse)
def review_detail(request: Request, trace_id: str, session: SessionDep) -> HTMLResponse:
    run = session.exec(select(PipelineRun).where(PipelineRun.trace_id == trace_id)).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"trace_id {trace_id!r} not found")

    decisions = session.exec(
        select(HumanReview).where(HumanReview.trace_id == trace_id).order_by(HumanReview.ts)  # type: ignore[arg-type]
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
    run = session.exec(select(PipelineRun).where(PipelineRun.trace_id == trace_id)).first()
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

    run = session.exec(select(PipelineRun).where(PipelineRun.trace_id == trace_id)).first()
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
        select(HumanReview).where(HumanReview.trace_id == trace_id).order_by(HumanReview.ts)  # type: ignore[arg-type]
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
