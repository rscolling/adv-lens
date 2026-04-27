"""HumanReviewGate — terminal node that marks a report as awaiting CCO sign-off.

Per CLAUDE.md, every node that produces output a human consumes routes
through an explicit gate. The gate's job is twofold:

1. **Marker.** Set ``state.review_status = "pending_review"`` and compute
   ``state.report_hash`` (SHA256 of the canonical ``RedlineReport`` JSON).
   The hash is the citation handle the audit table uses, stable across
   serialisation round-trips.

2. **Defensive default.** When ``state.redline`` is None — every upstream
   step failed — the gate marks the run "rejected" with a note. No
   blank report goes out under "pending_review" status.

The gate doesn't write to ``human_reviews`` itself; that table records
**human** decisions, not the system marker. The decision endpoint
(``POST /report/decision``) writes the row when a CCO acts. See ADR 0010.

True async pause-and-resume (LangGraph ``interrupt_before`` mechanic)
becomes Day-13 work alongside the background pipeline worker. For now
the pipeline runs to completion and returns the report in pending status;
the caller (UI, downstream system) is responsible for surfacing it for
review.
"""

from __future__ import annotations

import hashlib
import logging

from adv_lens.app.graph.state import ADVState
from adv_lens.app.settings import settings as default_settings

logger = logging.getLogger(__name__)


def compute_report_hash(redline_json: str) -> str:
    """SHA256 of the canonical RedlineReport JSON."""
    return hashlib.sha256(redline_json.encode("utf-8")).hexdigest()


def hitl_gate_node(state: ADVState) -> dict:
    if not default_settings.enable_hitl:
        # HITL disabled (e.g. dev mode) — auto-approve so the rest of the
        # pipeline can be exercised end-to-end. The setting is False only in
        # explicit dev / batch modes; production keeps it True.
        return {"review_status": "approved"}

    if state.redline is None:
        logger.warning(
            "hitl_gate_node: no redline on state for trace=%s; rejecting", state.trace_id
        )
        return {"review_status": "rejected"}

    canonical = state.redline.model_dump_json()
    report_hash = compute_report_hash(canonical)
    return {
        "review_status": "pending_review",
        "report_hash": report_hash,
    }
