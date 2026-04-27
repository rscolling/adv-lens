"""Redline Writer — composes extractor outputs + peer context into a CCO scorecard.

This is the final-stage output node. The CCO who reads ADV-Lens reads this
artifact. Inputs:

- ``Extractions`` — the typed bag of fee, disciplinary, and conflicts
  extractions populated by the parallel extractor branches.
- ``peer_context`` — list of ``PeerHit``-shaped dicts from the retrieval
  layer (week-3 ``retrieve_peers_node`` populates this; today the writer
  runs with an empty list and notes the gap in the report).
- Brochure metadata (CRD, version ID).

Default model is Opus 4.7 — this is the prose a human reads, the highest
cost tier is appropriate per ADR 0001's per-node cost rationale.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from adv_lens.extractors.schemas import (
    Extractions,
    RedlineReport,
    Scorecard,
    ScoreCategory,
)
from adv_lens.llm.client import LLMClient

logger = logging.getLogger(__name__)

REDLINE_SYSTEM_PROMPT = """\
You are a senior compliance analyst preparing a defensible scorecard on a
U.S. SEC Form ADV Part 2A brochure for a Chief Compliance Officer (CCO).

You will receive structured extractions of the brochure (fee schedule,
disciplinary history, conflicts of interest) and, when available, peer
context from comparable RIAs. Compose a RedlineReport that the CCO will
use on exam.

Hard requirements:

1. **Findings.** Write 4-12 findings. Each finding:
   - Has a unique ``id`` of the form ``F-001``, ``F-002``, ... per report.
   - Picks the most-applicable ``category`` from the controlled vocabulary.
   - Picks ``severity`` from {info, low, medium, high, critical}. Use:
     - ``critical`` ONLY for disclosed criminal events, undisclosed
       material conflicts, or unresolved active enforcement.
     - ``high`` for disclosed regulatory sanctions, soft-dollar
       arrangements outside Section 28(e), or required directed
       brokerage to an affiliated broker-dealer.
     - ``medium`` for typical disclosed conflicts (12b-1, hybrid
       BD/RIA, soft-dollar within 28(e)).
     - ``low`` / ``info`` for benign observations and positive signals.
   - Cites ``item_reference`` (the ADV Item number, 1-18) when the
     finding maps cleanly to one Item.
   - Cites ``sec_expectation_ref`` (e.g., "Form ADV Part 2A
     Instructions, Item 5") when grounded in SEC plain-English
     expectations.
   - Includes ``peer_comparison`` when peer context is provided AND the
     comparison is meaningful. Otherwise leave it null.
   - Includes a concrete ``recommendation`` for the CCO (review,
     document, escalate, no action required).

2. **Scorecard.** Always populate:
   - All four ``categories`` (compliance, transparency, conflicts_handling,
     fee_competitiveness), each scored 0-100 with a one-paragraph rationale.
   - ``overall_score`` should reflect a weighted view, not a strict average.
   - ``headline``: one sentence the CCO could paste into a memo. Do NOT
     editorialize beyond what the extractions support.

3. **Peer comparisons.** If peer context is provided, populate
   ``peer_comparisons`` (one entry per Item where peer data exists). If
   peer context is empty, leave the list empty AND mention the gap in
   ``Scorecard.headline`` (e.g., "Scorecard generated without peer
   comparison; see notes.").

4. **Conservative tone.** This document is positioned as analyst aid, not
   legal advice. Do not assert that any conduct is "in violation" unless
   the extractions explicitly carry a regulatory finding. Hedge with
   "appears", "may", "warrants review" where the source is ambiguous.

5. **Audit hygiene.** Echo any ``extraction_warnings`` from the inputs
   into ``extraction_warnings_seen`` so a reviewer can drill back to the
   source extraction. Add a ``notes`` paragraph if relevant context didn't
   fit anywhere else.

Never invent regulatory events, peer statistics, or sanctions not present
in the inputs.
"""


def build_redline_input(
    crd: str,
    brochure_version_id: str | None,
    extractions: Extractions,
    peer_context: list[dict[str, Any]] | None,
) -> str:
    """Render the structured inputs into a single prompt body for the LLM."""
    payload = {
        "brochure": {
            "crd": crd,
            "brochure_version_id": brochure_version_id,
        },
        "extractions": extractions.model_dump(mode="json"),
        "peer_context": peer_context or [],
    }
    return (
        "Compose a RedlineReport from the structured inputs below.\n\n"
        "```json\n" + json.dumps(payload, indent=2, default=str) + "\n```"
    )


class RedlineWriter:
    """Wraps an LLMClient with the redline prompt + RedlineReport schema."""

    NODE_NAME = "redline_writer"

    def __init__(self, llm: LLMClient, model: str | None = None) -> None:
        self._llm = llm
        from adv_lens.app.settings import settings

        self._model = model or settings.model_redline

    async def write(
        self,
        *,
        crd: str,
        brochure_version_id: str | None,
        extractions: Extractions,
        peer_context: list[dict[str, Any]] | None = None,
        trace_id: str,
    ) -> RedlineReport:
        if not _has_any_extraction(extractions):
            # No extractor populated anything — return a minimal report rather
            # than asking the model to invent findings from nothing.
            return RedlineReport(
                brochure_crd=crd,
                brochure_version_id=brochure_version_id,
                scorecard=Scorecard(
                    overall_score=0,
                    categories=[
                        ScoreCategory(
                            name="compliance",
                            score=0,
                            rationale="No extractor outputs available; cannot score.",
                        )
                    ],
                    headline="Report skipped: no extractor outputs reached the redline writer.",
                ),
                notes="Upstream extractors produced no usable output; review state.errors.",
            )

        prompt = build_redline_input(crd, brochure_version_id, extractions, peer_context)
        report = await self._llm.extract(
            model=self._model,
            system=REDLINE_SYSTEM_PROMPT,
            prompt=prompt,
            response_model=RedlineReport,
            trace_id=trace_id,
            node=self.NODE_NAME,
            brochure_crd=crd,
            max_tokens=8192,  # redline reports are longer than per-Item extractions
        )
        # Backfill brochure metadata if the model omitted it.
        if not report.brochure_crd:
            report = report.model_copy(update={"brochure_crd": crd})
        if not report.brochure_version_id and brochure_version_id is not None:
            report = report.model_copy(update={"brochure_version_id": brochure_version_id})
        return report


def _has_any_extraction(extractions: Extractions) -> bool:
    return any(
        v is not None for v in (extractions.fee, extractions.disciplinary, extractions.conflicts)
    )
