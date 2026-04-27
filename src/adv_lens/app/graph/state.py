"""Shared LangGraph state for the ADV-Lens pipeline.

Every node-to-node handoff validates against this model or a typed sub-model
(see CLAUDE.md: strings between nodes are a smell). Today the pipeline
covers fetch + segment; week 2 adds the extractor nodes; week 3 adds the
peer retriever, redline writer, and HITL gate.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from adv_lens.extractors.schemas import Extractions, RedlineReport, merge_extractions
from adv_lens.segmenter.models import SegmentedBrochure

ReviewStatus = Literal[
    "not_started",
    "pending_review",
    "approved",
    "rejected",
    "revise_requested",
]


class ADVState(BaseModel):
    """Pipeline state. Inputs are required; node outputs default to empty/None."""

    model_config = ConfigDict(arbitrary_types_allowed=False)

    # ── Inputs ───────────────────────────────────────────────────────────
    trace_id: str
    brochure_crd: str
    brochure_version_id: str | None = None  # if None, fetch_node resolves via IAPD search
    # AUM band ("$1B-$10B" etc) — populated by a future IARDLookupNode in
    # week-3+; passed straight to the peer-retrieval filter when present.
    brochure_aum_band: str | None = None

    # ── fetch_brochure_node output ───────────────────────────────────────
    brochure_pdf_path: str | None = None
    brochure_sha256: str | None = None
    brochure_from_cache: bool | None = None

    # ── segment_brochure_node output ─────────────────────────────────────
    segmented_brochure: SegmentedBrochure | None = None

    # ── Per-Item structured extractions ─────────────────────────────────
    # Annotated with merge_extractions so parallel extractor branches compose
    # into one Extractions object (see ADR 0006). Without the reducer,
    # LangGraph's last-write-wins default would clobber concurrent fee +
    # disciplinary writes.
    extractions: Annotated[Extractions, merge_extractions] = Field(default_factory=Extractions)

    # ── Peer retrieval output (week-3 retrieve_peers_node populates this) ─
    # Kept as list[dict] (not list[PeerHit]) to avoid pulling qdrant_client
    # into the import path of every state-handling test. The dicts carry
    # the same fields PeerHit serialises to.
    peer_context: list[dict] = Field(default_factory=list)

    # ── Final-stage output (write_redline_node populates this) ──────────
    redline: RedlineReport | None = None

    # ── HITL gate output (hitl_gate_node populates these) ──────────────
    review_status: ReviewStatus = "not_started"
    # SHA256 of the canonical RedlineReport JSON. Stable handle for the
    # human_reviews audit table — survives re-runs as long as the report
    # bytes don't change.
    report_hash: str | None = None

    # ── Diagnostics ──────────────────────────────────────────────────────
    errors: list[str] = Field(default_factory=list)
