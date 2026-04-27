"""LangGraph pipeline factory.

Today's topology:

    START → fetch_brochure → segment_brochure → END

Week 2 inserts the parallel extractor nodes (fee / disciplinary / conflicts)
fanning out from segment_brochure. Week 3 adds peer-comparison retrieval,
the redline writer, and the HumanReviewGate.

Keeping this in one place means the LangGraph topology is a single readable
artifact — anyone evaluating the repo can answer "what does this pipeline
actually do?" by reading 30 lines.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from adv_lens.app.graph.nodes import (
    extract_conflicts_node,
    extract_disciplinary_node,
    extract_fee_node,
    fetch_brochure_node,
    hitl_gate_node,
    retrieve_peers_node,
    segment_brochure_node,
    write_redline_node,
)
from adv_lens.app.graph.state import ADVState
from adv_lens.app.observability import get_callbacks
from adv_lens.app.settings import settings as default_settings


def build_pipeline(*, include_extractors: bool | None = None) -> Any:
    """Compile the LangGraph for the current pipeline.

    Topology:

        START → fetch_brochure → segment_brochure
                                   ├─→ extract_fee ──────────┐
                                   ├─→ extract_disciplinary ─┼─→ retrieve_peers → write_redline → hitl_gate → END
                                   └─→ extract_conflicts ────┘

    The three extractor nodes fan out from ``segment_brochure`` and run
    concurrently. Each returns ``{"extractions": Extractions(<one_field>=...)}``;
    the ``merge_extractions`` reducer on ``ADVState.extractions`` composes
    them into a single populated container (see ADR 0006).

    ``retrieve_peers`` is the fan-in point — LangGraph waits for all
    three extractor branches before invoking it. It queries the hybrid
    PeerStore once per populated extraction and writes the union of hits
    to ``state.peer_context``. ``write_redline`` then composes the
    extractions + peer context into the typed ``RedlineReport``.
    ``hitl_gate`` is the terminal node that marks the report as
    awaiting CCO sign-off (``review_status="pending_review"``) and
    computes ``report_hash`` for audit citation. See ADR 0010.

    ``include_extractors`` defaults to True when an Anthropic API key is
    configured, False otherwise — keeping ``docker compose up`` runnable
    on a machine without Anthropic credentials. Pass an explicit bool to
    override (tests use this).
    """
    if include_extractors is None:
        include_extractors = bool(default_settings.anthropic_api_key)

    graph = StateGraph(ADVState)
    graph.add_node("fetch_brochure", fetch_brochure_node)
    graph.add_node("segment_brochure", segment_brochure_node)
    graph.add_edge(START, "fetch_brochure")
    graph.add_edge("fetch_brochure", "segment_brochure")

    if include_extractors:
        graph.add_node("extract_fee", extract_fee_node)
        graph.add_node("extract_disciplinary", extract_disciplinary_node)
        graph.add_node("extract_conflicts", extract_conflicts_node)
        graph.add_node("retrieve_peers", retrieve_peers_node)
        graph.add_node("write_redline", write_redline_node)
        graph.add_node("hitl_gate", hitl_gate_node)
        graph.add_edge("segment_brochure", "extract_fee")
        graph.add_edge("segment_brochure", "extract_disciplinary")
        graph.add_edge("segment_brochure", "extract_conflicts")
        graph.add_edge("extract_fee", "retrieve_peers")
        graph.add_edge("extract_disciplinary", "retrieve_peers")
        graph.add_edge("extract_conflicts", "retrieve_peers")
        graph.add_edge("retrieve_peers", "write_redline")
        graph.add_edge("write_redline", "hitl_gate")
        graph.add_edge("hitl_gate", END)
    else:
        graph.add_edge("segment_brochure", END)

    return graph.compile()


def new_trace_id(prefix: str = "advlens") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def run_pipeline(
    brochure_crd: str,
    *,
    brochure_version_id: str | None = None,
    trace_id: str | None = None,
) -> ADVState:
    """Convenience wrapper: build, invoke async, coerce result back to ADVState."""
    pipeline = build_pipeline()
    initial = ADVState(
        trace_id=trace_id or new_trace_id(),
        brochure_crd=brochure_crd,
        brochure_version_id=brochure_version_id,
    )
    result = await pipeline.ainvoke(initial, config={"callbacks": get_callbacks()})
    # LangGraph returns a dict-shaped state; rehydrate to the typed model.
    return ADVState.model_validate(result)
