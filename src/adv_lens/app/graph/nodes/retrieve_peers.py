"""Retrieve peer brochure sections relevant to each populated extraction.

Sits between the parallel extractor branches and ``write_redline``. Reads
``state.extractions`` (composed by the ``merge_extractions`` reducer),
issues one hybrid query per populated extraction against the peer corpus,
and writes the union of hits to ``state.peer_context`` as ``PeerHit``
dicts (the redline writer consumes the dump form, not the typed model,
to keep the prompt JSON-serialisable).

Filters: each query is anchored by its ``item_number`` and excludes the
subject CRD. AUM band is applied when ``state.brochure_aum_band`` is set
(future IARDLookupNode populates it).

Graceful degradation: any failure to reach Qdrant or the peer corpus
yields an empty ``peer_context`` plus an entry in ``state.errors`` —
``write_redline`` already handles the "no peer context" case by saying
so in the headline.
"""

from __future__ import annotations

import logging
from typing import Any

from adv_lens.app.graph.state import ADVState
from adv_lens.app.settings import settings as default_settings
from adv_lens.segmenter.models import ItemNumber

logger = logging.getLogger(__name__)


# Static per-Item query anchors. The bge-small embedder + BM25 sparse will
# match Item-specific peer prose well even with a generic query — the
# Item-number filter does most of the narrowing. Extraction-derived queries
# (e.g., "tiered AUM" vs "wrap fee") become a refinement in week-3+.
_ITEM_QUERIES: dict[ItemNumber, str] = {
    ItemNumber.FEES_AND_COMPENSATION: "fee schedule pricing tiered AUM hourly fixed",
    ItemNumber.DISCIPLINARY_INFORMATION: "disciplinary history regulatory enforcement",
    ItemNumber.OTHER_ACTIVITIES: "other financial industry activities affiliations broker dealer",
    ItemNumber.CODE_OF_ETHICS: "code of ethics personal trading preclearance",
    ItemNumber.BROKERAGE_PRACTICES: "brokerage practices soft dollars directed brokerage",
}


def _items_to_query(state: ADVState) -> list[ItemNumber]:
    """Decide which Items to fetch peer context for, based on what extractors populated."""
    items: list[ItemNumber] = []
    if state.extractions.fee is not None:
        items.append(ItemNumber.FEES_AND_COMPENSATION)
    if state.extractions.disciplinary is not None:
        items.append(ItemNumber.DISCIPLINARY_INFORMATION)
    if state.extractions.conflicts is not None:
        # Conflicts spans Items 10/11/12; pull peer context for all three.
        items.extend(
            [
                ItemNumber.OTHER_ACTIVITIES,
                ItemNumber.CODE_OF_ETHICS,
                ItemNumber.BROKERAGE_PRACTICES,
            ]
        )
    return items


async def retrieve_peers_node(
    state: ADVState,
    *,
    store: Any | None = None,  # PeerStore — typed loosely to avoid qdrant import
) -> dict:
    items = _items_to_query(state)
    if not items:
        # Nothing to compare against — extractors all failed upstream. Leave
        # peer_context empty and let write_redline surface the gap.
        return {"peer_context": []}

    impl = store or _default_store()
    if impl is None:
        # Qdrant unreachable / not configured. Don't fail the pipeline.
        return {
            "peer_context": [],
            "errors": ["retrieve_peers_node: peer store unavailable; skipping peer comparison."],
        }

    settings = default_settings
    aggregated: list[dict] = []
    errors: list[str] = []

    for item in items:
        query = _ITEM_QUERIES.get(item)
        if query is None:
            continue
        try:
            hits = impl.query_peers(
                query,
                item_number=int(item),
                aum_band=state.brochure_aum_band,
                exclude_crd=state.brochure_crd,
                k=settings.peer_query_top_k_per_item,
                hybrid=getattr(impl, "hybrid_enabled", False),
                rerank=True,
            )
        except Exception as e:
            logger.warning("retrieve_peers_node item=%d: %s", int(item), e)
            errors.append(f"retrieve_peers_node item={int(item)}: {type(e).__name__}: {e}")
            continue
        for h in hits:
            aggregated.append(h.model_dump(mode="json"))

    update: dict = {"peer_context": aggregated}
    if errors:
        update["errors"] = errors
    return update


def _default_store() -> Any | None:
    """Build the production PeerStore lazily. Returns None if construction fails."""
    try:
        from adv_lens.retrieval.qdrant_store import make_peer_store

        return make_peer_store(hybrid=True, rerank=True)
    except Exception as e:
        logger.warning("retrieve_peers_node: failed to build PeerStore: %s", e)
        return None
