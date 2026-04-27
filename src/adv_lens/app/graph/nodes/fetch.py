"""Fetch-brochure LangGraph node.

Resolves a CRD (and optional brochure_version_id) into a cached PDF on disk
via ``IAPDClient``. If no ``brochure_version_id`` is set on the state, we
call the IAPD search API and pick the first current brochure.

Returns a partial state update; LangGraph merges it into ``ADVState``.
"""

from __future__ import annotations

import logging

from adv_lens.app.graph.state import ADVState
from adv_lens.ingestion import IAPDClient
from adv_lens.ingestion.models import BrochureRef

logger = logging.getLogger(__name__)


async def fetch_brochure_node(
    state: ADVState,
    *,
    client: IAPDClient | None = None,
) -> dict:
    """Populate ``brochure_pdf_path`` / ``brochure_sha256`` from IAPD.

    The optional ``client`` arg lets tests inject a mock-transport client;
    production callers leave it ``None`` and we own the lifecycle.
    """
    own_client = client is None
    iapd = client or IAPDClient()
    try:
        vid = state.brochure_version_id
        if vid is None:
            refs = await iapd.list_current_brochures(state.brochure_crd)
            if not refs:
                return {
                    "errors": [
                        f"fetch_brochure_node: no current brochures on IAPD for CRD={state.brochure_crd}"
                    ]
                }
            ref = refs[0]
        else:
            ref = BrochureRef(crd=state.brochure_crd, brochure_version_id=vid)

        result = await iapd.fetch_brochure(ref)
        return {
            "brochure_version_id": ref.brochure_version_id,
            "brochure_pdf_path": str(result.pdf_path),
            "brochure_sha256": result.sha256,
            "brochure_from_cache": result.from_cache,
        }
    except Exception as e:
        # Broad except is intentional — node boundary surfaces failures via
        # state.errors so the pipeline keeps composing.
        logger.exception("fetch_brochure_node failed for CRD=%s", state.brochure_crd)
        return {"errors": [f"fetch_brochure_node: {type(e).__name__}: {e}"]}
    finally:
        if own_client:
            await iapd.aclose()
