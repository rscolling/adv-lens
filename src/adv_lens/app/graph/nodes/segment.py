"""Segment-brochure LangGraph node.

Reads ``state.brochure_pdf_path``, runs the configured segmenter backend,
optionally rescues missing/tiny Items via the LLM fallback (ADR 0014),
and returns the populated ``SegmentedBrochure`` for downstream extractors.

``SegmenterError`` (e.g. scanned PDF, empty text) lands in ``state.errors``
rather than blowing up the graph.
"""

from __future__ import annotations

import logging
from pathlib import Path

from adv_lens.app.graph.state import ADVState
from adv_lens.llm.client import LLMClient, make_llm_client
from adv_lens.segmenter import HeuristicSegmenter, Segmenter, SegmenterError
from adv_lens.segmenter.llm_fallback import (
    RESCUE_ITEMS,
    RESCUE_THRESHOLD_CHARS,
    rescue_missing_items,
)
from adv_lens.segmenter.text_extract import extract_text_from_pdf

logger = logging.getLogger(__name__)


async def segment_brochure_node(
    state: ADVState,
    *,
    segmenter: Segmenter | None = None,
    llm_client: LLMClient | None = None,
) -> dict:
    if state.brochure_pdf_path is None:
        return {
            "errors": ["segment_brochure_node: no brochure_pdf_path on state (fetch node failed?)"]
        }

    impl = segmenter or HeuristicSegmenter()
    pdf_path = Path(state.brochure_pdf_path)
    try:
        segmented = impl.segment_pdf(pdf_path)
    except (SegmenterError, FileNotFoundError) as e:
        logger.warning("segment_brochure_node: %s", e)
        return {"errors": [f"segment_brochure_node: {type(e).__name__}: {e}"]}

    # ADR 0014: rescue missing/tiny extractor-consumed Items via Haiku
    # 4.5. Skipped silently when no Anthropic key is set (dev / offline
    # mode), no LLM client is available, or nothing needs rescue.
    if _needs_rescue(segmented):
        client = llm_client or _maybe_make_llm_client()
        if client is not None:
            try:
                text = extract_text_from_pdf(pdf_path)
            except SegmenterError as e:
                logger.warning(
                    "segment_brochure_node: rescue skipped, text re-extract failed: %s", e
                )
            else:
                segmented = await rescue_missing_items(
                    text,
                    segmented,
                    client,
                    trace_id=state.trace_id,
                    brochure_crd=state.brochure_crd,
                )

    update: dict = {"segmented_brochure": segmented}
    if segmented.missing_items:
        update["errors"] = [
            "segment_brochure_node: missing items "
            + ",".join(str(int(i)) for i in segmented.missing_items)
        ]
    return update


def _needs_rescue(segmented) -> bool:
    found = {int(s.item_number): s for s in segmented.sections}
    return any(n not in found or len(found[n].body) < RESCUE_THRESHOLD_CHARS for n in RESCUE_ITEMS)


def _maybe_make_llm_client() -> LLMClient | None:
    """Return a real LLM client when configured; None when no API key."""
    from adv_lens.app.settings import settings as default_settings

    if not default_settings.anthropic_api_key:
        return None
    try:
        return make_llm_client()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("segment_brochure_node: cannot build LLM client: %s", e)
        return None
