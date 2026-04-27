"""Peer-corpus seeding.

Reads a JSON peer-spec file, runs the LangGraph pipeline per CRD, and
upserts each brochure's sections into Qdrant. Idempotent — re-running on
the same file refreshes in place rather than appending duplicates.

The peer-spec file shape (``data/peers/<file>.json``)::

    [
      {"crd": "108000", "aum_band": "$1B-$10B", "main_office_state": "NY"},
      {"crd": "108001", "brochure_version_id": "777", "aum_band": "$100M-$1B"}
    ]

Operators curate this list manually for week 1. Week 3 wires in IARD
bulk-CSV-driven peer discovery; for now an explicit list keeps the corpus
auditable.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter

from adv_lens.app.graph.pipeline import run_pipeline
from adv_lens.retrieval.qdrant_store import PeerSpec, PeerStore

logger = logging.getLogger(__name__)


_PEER_SPEC_LIST = TypeAdapter(list[PeerSpec])


def load_peer_specs(path: Path) -> list[PeerSpec]:
    return _PEER_SPEC_LIST.validate_json(Path(path).read_text(encoding="utf-8"))


class SeedReport(dict):
    """{ crd: {sections_indexed, errors, brochure_version_id} }."""


async def seed_peers(
    specs: Iterable[PeerSpec],
    store: PeerStore,
    *,
    fail_fast: bool = False,
) -> SeedReport:
    """Pipeline-per-CRD → upsert into Qdrant. Returns a per-CRD report."""
    store.ensure_collection()
    report: SeedReport = SeedReport()

    for spec in specs:
        entry: dict = {"sections_indexed": 0, "errors": []}
        try:
            state = await run_pipeline(spec.crd, brochure_version_id=spec.brochure_version_id)
        except Exception as e:
            msg = f"pipeline raised: {type(e).__name__}: {e}"
            logger.exception("seed_peers: %s", msg)
            entry["errors"].append(msg)
            report[spec.crd] = entry
            if fail_fast:
                raise
            continue

        if state.errors:
            entry["errors"].extend(state.errors)
        if state.segmented_brochure is None:
            entry["errors"].append("no segmented_brochure on state")
            report[spec.crd] = entry
            continue

        # Resolved version ID overrides whatever the operator passed
        # (handles the case where the spec didn't pin one).
        resolved_spec = spec.model_copy(
            update={"brochure_version_id": state.brochure_version_id or spec.brochure_version_id}
        )
        try:
            count = store.upsert_sections(
                resolved_spec,
                state.segmented_brochure.sections,
                brochure_sha256=state.brochure_sha256,
            )
            entry["sections_indexed"] = count
            entry["brochure_version_id"] = resolved_spec.brochure_version_id
        except Exception as e:
            msg = f"upsert raised: {type(e).__name__}: {e}"
            logger.exception("seed_peers: %s", msg)
            entry["errors"].append(msg)
            if fail_fast:
                raise

        report[spec.crd] = entry

    return report


def report_to_markdown(report: SeedReport) -> str:
    lines = [
        "| CRD | Brochure Version | Sections Indexed | Errors |",
        "| --- | --- | ---: | --- |",
    ]
    for crd, entry in sorted(report.items()):
        errs = "; ".join(entry.get("errors", [])) or "—"
        vid = entry.get("brochure_version_id") or "?"
        lines.append(f"| {crd} | {vid} | {entry.get('sections_indexed', 0)} | {errs} |")
    total = sum(int(e.get("sections_indexed", 0)) for e in report.values())
    lines.append(f"| **total** |  | **{total}** |  |")
    return "\n".join(lines) + "\n"


def write_report(path: Path, report: SeedReport) -> None:
    path = Path(path)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
