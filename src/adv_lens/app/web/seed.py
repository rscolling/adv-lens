"""Seed PipelineRun rows from `docs/examples/sample-state.json`.

Run once after a fresh DB so the review UI has rows to click on:

    uv run python -m adv_lens.app.web.seed

Seeds two rows:

1. The original Brown Advisory **filed-brochure** run as-is.
2. A **draft-brochure** companion row built from the same brochure
   bytes, surfaced through the upload code path with a synthetic
   ``99``-prefixed CRD. Same redline content, different metadata —
   demonstrates the pre-file self-review use case in the dashboard
   without requiring an actual upload.

Idempotent — re-running with the same trace_id replaces the existing
row's status/result, so a fresher sample-state.json overrides cleanly.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, select

from adv_lens.app.graph.nodes.hitl_gate import compute_report_hash
from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.settings import settings
from adv_lens.app.storage.db import engine
from adv_lens.extractors.schemas import RedlineReport

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_SAMPLE = _REPO_ROOT / "docs" / "examples" / "sample-state.json"

_DRAFT_TRACE_ID = "draft-brown-advisory-self-review"
_DRAFT_DEMO_NOTE = (
    "Demo seed: this run reuses the Brown Advisory brochure bytes "
    "(real CRD 110181) shown via the draft-upload code path so reviewers "
    "can see what a pre-file self-review looks like in the dashboard. "
    "The synthetic '99'-prefixed CRD marks the row as not-a-real-CRD; "
    "the redline body is identical to the filed-brochure run because "
    "the underlying analysis ran on the same PDF."
)


def _synthetic_crd_for_draft(sha256: str) -> str:
    """Mirror routes._synthetic_crd_for_draft (same content -> same id)."""
    n = int(sha256[:8], 16) % 10**10
    return f"99{n:010d}"


def seed_sample(sample_path: Path = _DEFAULT_SAMPLE) -> str:
    """Upsert a PipelineRun row from the on-disk sample state. Returns trace_id."""
    if not sample_path.exists():
        raise FileNotFoundError(f"Sample state not found at {sample_path}")

    with sample_path.open(encoding="utf-8") as f:
        state = json.load(f)

    trace_id = state.get("trace_id")
    if not trace_id:
        raise ValueError(f"sample state at {sample_path} has no trace_id")

    SQLModel.metadata.create_all(engine)

    now = datetime.now(UTC)
    with Session(engine) as session:
        existing = session.exec(
            select(PipelineRun).where(PipelineRun.trace_id == trace_id)
        ).first()
        if existing is None:
            row = PipelineRun(
                trace_id=trace_id,
                brochure_crd=str(state.get("brochure_crd", "")),
                brochure_version_id=(
                    str(state["brochure_version_id"])
                    if state.get("brochure_version_id") is not None
                    else None
                ),
                status="complete",
                result=state,
                started_at=now,
                completed_at=now,
            )
            session.add(row)
        else:
            existing.status = "complete"
            existing.result = state
            existing.completed_at = now
            session.add(existing)
        session.commit()

    return trace_id


def seed_draft_from_filed(sample_path: Path = _DEFAULT_SAMPLE) -> str | None:
    """Upsert a synthetic draft-row built from the filed sample. Returns trace_id.

    Returns ``None`` if the cached PDF the filed sample references is
    missing — the draft seed is a nice-to-have, not a must-have, so it
    no-ops rather than failing the whole seed run.
    """
    if not sample_path.exists():
        raise FileNotFoundError(f"Sample state not found at {sample_path}")

    with sample_path.open(encoding="utf-8") as f:
        filed_state = json.load(f)

    sha256 = filed_state.get("brochure_sha256")
    if not sha256:
        return None
    synthetic_crd = _synthetic_crd_for_draft(sha256)
    synthetic_vid = "0"

    # Locate the source PDF via the canonical cache convention
    # (data_dir / brochures / <crd> / <vid>.pdf). The path stored in the
    # JSON is whatever absolute path the run-time machine happened to use;
    # don't trust it across machines.
    filed_crd = str(filed_state.get("brochure_crd", ""))
    filed_vid = str(filed_state.get("brochure_version_id", ""))
    src_pdf = settings.data_dir / "brochures" / filed_crd / f"{filed_vid}.pdf"
    if not src_pdf.exists():
        # No cached PDF on this machine — skip the draft seed; the iframe
        # would still work because we keep the redline content, but the
        # demo's 'click run from the upload form on this same brochure'
        # story breaks if the cache file isn't there.
        return None

    dst_pdf = settings.data_dir / "brochures" / synthetic_crd / f"{synthetic_vid}.pdf"
    dst_pdf.parent.mkdir(parents=True, exist_ok=True)
    if not dst_pdf.exists():
        dst_pdf.write_bytes(src_pdf.read_bytes())

    draft_state = copy.deepcopy(filed_state)
    draft_state["trace_id"] = _DRAFT_TRACE_ID
    draft_state["brochure_crd"] = synthetic_crd
    draft_state["brochure_version_id"] = synthetic_vid
    draft_state["brochure_pdf_path"] = str(dst_pdf)

    redline = draft_state["redline"]
    redline["brochure_crd"] = synthetic_crd
    redline["brochure_version_id"] = synthetic_vid
    existing_notes = redline.get("notes")
    redline["notes"] = (
        f"{_DRAFT_DEMO_NOTE}\n\n{existing_notes}" if existing_notes else _DRAFT_DEMO_NOTE
    )

    # Recompute report_hash on the modified redline so the HITL gate's
    # bytes-pin invariant holds for the seeded row.
    rl_model = RedlineReport.model_validate(redline)
    draft_state["report_hash"] = compute_report_hash(rl_model.model_dump_json())

    SQLModel.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        existing = session.exec(
            select(PipelineRun).where(PipelineRun.trace_id == _DRAFT_TRACE_ID)
        ).first()
        if existing is None:
            row = PipelineRun(
                trace_id=_DRAFT_TRACE_ID,
                brochure_crd=synthetic_crd,
                brochure_version_id=synthetic_vid,
                status="complete",
                result=draft_state,
                started_at=now,
                completed_at=now,
            )
            session.add(row)
        else:
            existing.brochure_crd = synthetic_crd
            existing.brochure_version_id = synthetic_vid
            existing.status = "complete"
            existing.result = draft_state
            existing.completed_at = now
            session.add(existing)
        session.commit()

    return _DRAFT_TRACE_ID


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed sample PipelineRun rows.")
    parser.add_argument(
        "--sample",
        type=Path,
        default=_DEFAULT_SAMPLE,
        help=f"Path to sample state JSON (default: {_DEFAULT_SAMPLE})",
    )
    parser.add_argument(
        "--no-draft",
        action="store_true",
        help="Skip the draft-row seed (filed-row only).",
    )
    args = parser.parse_args(argv)

    filed_trace = seed_sample(args.sample)
    print(f"Seeded filed PipelineRun trace_id={filed_trace} from {args.sample}")

    if not args.no_draft:
        draft_trace = seed_draft_from_filed(args.sample)
        if draft_trace:
            print(f"Seeded draft PipelineRun trace_id={draft_trace}")
        else:
            print("Skipped draft seed (cached PDF missing — run a real fetch first).")

    print("Open http://localhost:8000/review to view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
