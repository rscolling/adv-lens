"""Conflicts extractor LangGraph node.

Reads Items 10, 11, and 12 from ``state.segmented_brochure`` and writes
``state.extractions.conflicts``. Third parallel branch off
``segment_brochure`` — joins at END alongside ``extract_fee`` and
``extract_disciplinary``. The ``merge_extractions`` reducer composes
all three writes (see ADR 0006).

If at least one of Items 10/11/12 is present we proceed; the extractor
itself handles the case where one or two are missing by passing the
placeholder text to the model. Only when all three are absent do we
short-circuit with an error.
"""

from __future__ import annotations

import logging

from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.conflicts import ConflictsExtractor
from adv_lens.extractors.schemas import Extractions
from adv_lens.llm.client import LLMClient, LLMError
from adv_lens.segmenter.models import ItemNumber

logger = logging.getLogger(__name__)


async def extract_conflicts_node(
    state: ADVState,
    *,
    extractor: ConflictsExtractor | None = None,
    llm: LLMClient | None = None,
) -> dict:
    if state.segmented_brochure is None:
        return {"errors": ["extract_conflicts_node: no segmented_brochure on state"]}

    seg = state.segmented_brochure
    item10 = seg.section(ItemNumber.OTHER_ACTIVITIES)
    item11 = seg.section(ItemNumber.CODE_OF_ETHICS)
    item12 = seg.section(ItemNumber.BROKERAGE_PRACTICES)

    if item10 is None and item11 is None and item12 is None:
        return {
            "errors": [
                "extract_conflicts_node: Items 10/11/12 all missing from segmentation; "
                "no extraction performed."
            ]
        }

    impl = extractor or _default_extractor(llm)
    try:
        conflicts = await impl.extract(
            item10.body if item10 else None,
            item11.body if item11 else None,
            item12.body if item12 else None,
            trace_id=state.trace_id,
            brochure_crd=state.brochure_crd,
        )
    except LLMError as e:
        logger.warning("extract_conflicts_node: %s", e)
        return {"errors": [f"extract_conflicts_node: {e}"]}

    return {"extractions": Extractions(conflicts=conflicts)}


def _default_extractor(llm: LLMClient | None) -> ConflictsExtractor:
    from adv_lens.llm.client import make_llm_client

    return ConflictsExtractor(llm or make_llm_client())
