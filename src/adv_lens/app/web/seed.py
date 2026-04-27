"""Seed a PipelineRun row from `docs/examples/sample-state.json`.

Run once after a fresh DB so the review UI has a row to click on:

    uv run python -m adv_lens.app.web.seed

Idempotent — re-running with the same trace_id replaces the existing
row's status/result, so a fresher sample-state.json overrides cleanly.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, select

from adv_lens.app.jobs.models import PipelineRun
from adv_lens.app.storage.db import engine

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_SAMPLE = _REPO_ROOT / "docs" / "examples" / "sample-state.json"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a sample PipelineRun row.")
    parser.add_argument(
        "--sample",
        type=Path,
        default=_DEFAULT_SAMPLE,
        help=f"Path to sample state JSON (default: {_DEFAULT_SAMPLE})",
    )
    args = parser.parse_args(argv)

    trace_id = seed_sample(args.sample)
    print(f"Seeded PipelineRun trace_id={trace_id} from {args.sample}")
    print("Open http://localhost:8000/review to view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
