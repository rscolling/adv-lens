"""Fee extractor LangGraph node.

Reads ``state.segmented_brochure.section(5)`` and writes a populated
``state.extractions.fee``. When the brochure has no Item 5 (rare; the
segmenter would have surfaced it via missing_items), the node records the
condition in ``state.errors`` and leaves the field None.

The LLMClient defaults to ``make_llm_client()``; tests inject a fake.
"""

from __future__ import annotations

import logging

from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.fee import FeeExtractor
from adv_lens.extractors.schemas import Extractions
from adv_lens.llm.client import LLMClient, LLMError
from adv_lens.segmenter.models import ItemNumber

logger = logging.getLogger(__name__)


async def extract_fee_node(
    state: ADVState,
    *,
    extractor: FeeExtractor | None = None,
    llm: LLMClient | None = None,
) -> dict:
    if state.segmented_brochure is None:
        return {"errors": ["extract_fee_node: no segmented_brochure on state"]}

    section = state.segmented_brochure.section(ItemNumber.FEES_AND_COMPENSATION)
    if section is None or section.is_placeholder:
        return {
            "errors": ["extract_fee_node: Item 5 missing or placeholder; no extraction performed."]
        }

    impl = extractor or _default_extractor(llm)
    try:
        fee = await impl.extract(
            section.body, trace_id=state.trace_id, brochure_crd=state.brochure_crd
        )
    except LLMError as e:
        logger.warning("extract_fee_node: %s", e)
        return {"errors": [f"extract_fee_node: {e}"]}

    # Return only the partial we own; the LangGraph reducer composes it
    # into ADVState.extractions alongside any parallel extractor branches.
    return {"extractions": Extractions(fee=fee)}


def _default_extractor(llm: LLMClient | None) -> FeeExtractor:
    from adv_lens.llm.client import make_llm_client

    return FeeExtractor(llm or make_llm_client())
