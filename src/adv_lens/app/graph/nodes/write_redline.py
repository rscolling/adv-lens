"""Redline-writer LangGraph node.

Joins after the three parallel extractor branches. Reads
``state.extractions`` (composed by the ``merge_extractions`` reducer) and
``state.peer_context`` (populated by the week-3 retrieve_peers node;
empty until then), and writes ``state.redline``.

LLM cost note: Opus 4.7 by default per ADR 0001. One call per pipeline
run, after fan-in, so the total Opus spend per brochure is bounded.
"""

from __future__ import annotations

import logging

from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.redline import RedlineWriter
from adv_lens.llm.client import LLMClient, LLMError

logger = logging.getLogger(__name__)


async def write_redline_node(
    state: ADVState,
    *,
    writer: RedlineWriter | None = None,
    llm: LLMClient | None = None,
) -> dict:
    impl = writer or _default_writer(llm)
    try:
        report = await impl.write(
            crd=state.brochure_crd,
            brochure_version_id=state.brochure_version_id,
            extractions=state.extractions,
            peer_context=state.peer_context,
            trace_id=state.trace_id,
        )
    except LLMError as e:
        logger.warning("write_redline_node: %s", e)
        return {"errors": [f"write_redline_node: {e}"]}

    return {"redline": report}


def _default_writer(llm: LLMClient | None) -> RedlineWriter:
    from adv_lens.llm.client import make_llm_client

    return RedlineWriter(llm or make_llm_client())
