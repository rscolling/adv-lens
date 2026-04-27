"""Disciplinary extractor LangGraph node.

Reads ``state.segmented_brochure.section(9)`` and writes a populated
``state.extractions.disciplinary``. Runs in parallel with
``extract_fee_node`` — both fan out from ``segment_brochure``, both
return only their own field of ``Extractions``, and the
``merge_extractions`` reducer composes the writes (see ADR 0006).
"""

from __future__ import annotations

import logging

from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.disciplinary import DisciplinaryExtractor
from adv_lens.extractors.schemas import Extractions
from adv_lens.llm.client import LLMClient, LLMError
from adv_lens.segmenter.models import ItemNumber

logger = logging.getLogger(__name__)


async def extract_disciplinary_node(
    state: ADVState,
    *,
    extractor: DisciplinaryExtractor | None = None,
    llm: LLMClient | None = None,
) -> dict:
    if state.segmented_brochure is None:
        return {"errors": ["extract_disciplinary_node: no segmented_brochure on state"]}

    section = state.segmented_brochure.section(ItemNumber.DISCIPLINARY_INFORMATION)
    if section is None:
        return {
            "errors": [
                "extract_disciplinary_node: Item 9 missing from segmentation; no extraction performed."
            ]
        }

    impl = extractor or _default_extractor(llm)
    try:
        disciplinary = await impl.extract(
            section.body, trace_id=state.trace_id, brochure_crd=state.brochure_crd
        )
    except LLMError as e:
        logger.warning("extract_disciplinary_node: %s", e)
        return {"errors": [f"extract_disciplinary_node: {e}"]}

    return {"extractions": Extractions(disciplinary=disciplinary)}


def _default_extractor(llm: LLMClient | None) -> DisciplinaryExtractor:
    from adv_lens.llm.client import make_llm_client

    return DisciplinaryExtractor(llm or make_llm_client())
