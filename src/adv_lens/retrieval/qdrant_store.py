"""Qdrant peer-corpus store with optional hybrid retrieval.

One vector per Item per brochure. Point IDs are deterministic UUIDs derived
from ``(crd, brochure_version_id, item_number)`` so reseeding is idempotent
- a re-run on the same fixtures upserts in place rather than appending
duplicates. Payload is queryable: filter by AUM band, state, item number,
or exclude the subject CRD.

The collection is created with named vectors so dense + sparse retrieval
co-exist without re-indexing. When a ``SparseEncoder`` is wired into the
store, ``upsert_sections`` writes both vectors and ``query_peers`` can
optionally fuse the two retrievers via Qdrant's server-side RRF; a
``Reranker`` post-orders the fused top-k for the caller. See ADR 0007.

Body text now lives in payload (revising ADR 0004 section 5) so the
cross-encoder reranker has something to score. The footprint is small —
~2KB per Item, 18 Items per brochure, 60 brochures, total ~= 2 MB.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings
from adv_lens.retrieval.embeddings import Embedder
from adv_lens.retrieval.rerank import Reranker
from adv_lens.retrieval.sparse import SparseEncoder

logger = logging.getLogger(__name__)

# UUID5 namespace — stable, project-private; do not change once seeded.
_POINT_NAMESPACE = uuid.UUID("a4d6f1c0-1c5b-4a0f-9b1f-adef0e5abee7")
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


class PeerSpec(BaseModel):
    """Operator-curated metadata for one peer firm.

    Lives in the JSON file passed to ``seed-peers``. AUM band + state are
    optional; when missing, the corresponding payload field is null and
    those filters won't match the point.
    """

    crd: str
    brochure_version_id: str | None = None
    aum_band: str | None = None
    main_office_state: str | None = None
    notes: str | None = None


class PeerHit(BaseModel):
    """One scored result from a peer query."""

    crd: str
    brochure_version_id: str
    item_number: int
    item_title: str
    score: float
    body_excerpt: str
    payload: dict[str, Any] = Field(default_factory=dict)


def point_id_for(crd: str, brochure_version_id: str, item_number: int) -> str:
    key = f"{crd}/{brochure_version_id}/{int(item_number)}"
    return str(uuid.uuid5(_POINT_NAMESPACE, key))


def section_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class PeerStore:
    """Thin wrapper over qdrant-client for the peer collection.

    Tests inject ``QdrantClient(":memory:")``; production gets a network
    client built from settings. The class never owns the connection
    lifecycle beyond the methods called on it.
    """

    def __init__(
        self,
        client: QdrantClient,
        embedder: Embedder,
        settings: Settings = default_settings,
        *,
        sparse_encoder: SparseEncoder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._settings = settings
        self._collection = settings.qdrant_collection_peers
        self._sparse = sparse_encoder
        self._reranker = reranker

    @property
    def collection_name(self) -> str:
        return self._collection

    @property
    def hybrid_enabled(self) -> bool:
        return self._sparse is not None

    # ── Schema ─────────────────────────────────────────────────────────
    def ensure_collection(self) -> None:
        """Create the collection if missing. Idempotent.

        Sparse vector config is included only when a ``SparseEncoder`` is
        wired into the store. A collection created in dense-only mode
        cannot be promoted to hybrid in place — Qdrant requires recreating
        with the sparse config, then re-upserting (see ADR 0007 backfill).
        """
        if self._client.collection_exists(self._collection):
            return

        kwargs: dict = {
            "collection_name": self._collection,
            "vectors_config": {
                DENSE_VECTOR_NAME: qm.VectorParams(
                    size=self._embedder.dim, distance=qm.Distance.COSINE
                ),
            },
        }
        if self._sparse is not None:
            kwargs["sparse_vectors_config"] = {SPARSE_VECTOR_NAME: qm.SparseVectorParams()}

        self._client.create_collection(**kwargs)

        # Payload indexes for the filters we know we'll use. The local
        # `:memory:` Qdrant client warns that these are no-ops; we skip
        # them in that mode rather than spam the test logs.
        if not _is_local(self._client):
            for field, schema in (
                ("crd", qm.PayloadSchemaType.KEYWORD),
                ("brochure_version_id", qm.PayloadSchemaType.KEYWORD),
                ("item_number", qm.PayloadSchemaType.INTEGER),
                ("aum_band", qm.PayloadSchemaType.KEYWORD),
                ("main_office_state", qm.PayloadSchemaType.KEYWORD),
            ):
                self._client.create_payload_index(self._collection, field, schema)

        logger.info(
            "Created Qdrant collection %s (dim=%d, hybrid=%s)",
            self._collection,
            self._embedder.dim,
            self.hybrid_enabled,
        )

    # ── Upsert ─────────────────────────────────────────────────────────
    def upsert_sections(
        self,
        spec: PeerSpec,
        sections: list[Any],  # adv_lens.segmenter.models.Section — avoid import cycle
        *,
        brochure_sha256: str | None = None,
    ) -> int:
        """Embed and upsert one brochure's sections. Returns the count written."""
        if not sections:
            return 0
        if not spec.brochure_version_id:
            raise ValueError("PeerSpec.brochure_version_id is required for upsert")

        # Skip placeholder sections — they're noise for peer comparison.
        meaningful = [s for s in sections if not s.is_placeholder]
        if not meaningful:
            return 0

        bodies = [s.body for s in meaningful]
        dense_vectors = self._embedder.embed(bodies)
        sparse_vectors: list[qm.SparseVector] | list[None]
        if self._sparse is not None:
            sparse_vectors = self._sparse.encode_documents(bodies)  # type: ignore[assignment]
        else:
            sparse_vectors = [None] * len(meaningful)

        now = datetime.now(UTC).isoformat()

        points = []
        for sec, dvec, svec in zip(meaningful, dense_vectors, sparse_vectors, strict=True):
            payload = {
                "crd": spec.crd,
                "brochure_version_id": spec.brochure_version_id,
                "item_number": int(sec.item_number),
                "item_title": sec.title,
                "section_sha256": section_sha256(sec.body),
                "brochure_sha256": brochure_sha256,
                "char_start": sec.char_start,
                "char_end": sec.char_end,
                "aum_band": spec.aum_band,
                "main_office_state": spec.main_office_state,
                "indexed_at": now,
                # Body lives in payload so the reranker has prose to score —
                # ADR 0007 revises the day-5 "no body in payload" call.
                "body": sec.body,
            }
            vectors: dict[str, Any] = {DENSE_VECTOR_NAME: dvec}
            if svec is not None:
                vectors[SPARSE_VECTOR_NAME] = svec
            points.append(
                qm.PointStruct(
                    id=point_id_for(spec.crd, spec.brochure_version_id, int(sec.item_number)),
                    vector=vectors,
                    payload=payload,
                )
            )

        self._client.upsert(collection_name=self._collection, points=points)
        return len(points)

    # ── Query ──────────────────────────────────────────────────────────
    def query_peers(
        self,
        query_text: str,
        *,
        item_number: int | None = None,
        aum_band: str | None = None,
        main_office_state: str | None = None,
        exclude_crd: str | None = None,
        k: int = 5,
        excerpt_chars: int = 280,
        hybrid: bool = False,
        rerank: bool = True,
    ) -> list[PeerHit]:
        """Semantic search over the peer corpus, with payload filters.

        - ``hybrid=False`` (default): dense-only, backward-compatible.
        - ``hybrid=True``: dense + sparse with server-side RRF fusion.
          Requires a ``SparseEncoder`` wired into the store.
        - ``rerank=True`` and a reranker present: the fused top
          ``settings.rerank_top_k`` hits are re-scored by the cross-encoder
          and re-ordered before truncating to ``k``.
        """
        flt = self._build_filter(item_number, aum_band, main_office_state, exclude_crd)
        if hybrid and self._sparse is None:
            raise ValueError("hybrid=True requires a SparseEncoder; pass one to PeerStore(...)")

        if hybrid:
            scored_points = self._query_hybrid(query_text, flt)
        else:
            scored_points = self._query_dense(
                query_text, flt, limit=k if not rerank else max(k, self._settings.rerank_top_k)
            )

        # Optional cross-encoder rerank.
        if rerank and self._reranker is not None and scored_points:
            scored_points = self._apply_rerank(query_text, scored_points)

        return self._materialize_hits(scored_points[:k], excerpt_chars=excerpt_chars)

    def _build_filter(
        self,
        item_number: int | None,
        aum_band: str | None,
        main_office_state: str | None,
        exclude_crd: str | None,
    ) -> qm.Filter | None:
        must: list[qm.FieldCondition] = []
        must_not: list[qm.FieldCondition] = []
        if item_number is not None:
            must.append(
                qm.FieldCondition(key="item_number", match=qm.MatchValue(value=int(item_number)))
            )
        if aum_band is not None:
            must.append(qm.FieldCondition(key="aum_band", match=qm.MatchValue(value=aum_band)))
        if main_office_state is not None:
            must.append(
                qm.FieldCondition(
                    key="main_office_state", match=qm.MatchValue(value=main_office_state)
                )
            )
        if exclude_crd is not None:
            must_not.append(qm.FieldCondition(key="crd", match=qm.MatchValue(value=exclude_crd)))
        if not (must or must_not):
            return None
        return qm.Filter(must=must or None, must_not=must_not or None)

    def _query_dense(self, query_text: str, flt: qm.Filter | None, *, limit: int) -> list:
        if hasattr(self._embedder, "embed_query"):
            vector = self._embedder.embed_query(query_text)  # type: ignore[attr-defined]
        else:
            vector = self._embedder.embed([query_text])[0]
        result = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            using=DENSE_VECTOR_NAME,
            limit=limit,
            query_filter=flt,
            with_payload=True,
        )
        return list(result.points)

    def _query_hybrid(self, query_text: str, flt: qm.Filter | None) -> list:
        assert self._sparse is not None  # guarded by caller
        if hasattr(self._embedder, "embed_query"):
            dense_vec = self._embedder.embed_query(query_text)  # type: ignore[attr-defined]
        else:
            dense_vec = self._embedder.embed([query_text])[0]
        sparse_vec = self._sparse.encode_query(query_text)
        prefetch_limit = self._settings.hybrid_prefetch_limit
        result = self._client.query_points(
            collection_name=self._collection,
            prefetch=[
                qm.Prefetch(
                    query=dense_vec,
                    using=DENSE_VECTOR_NAME,
                    limit=prefetch_limit,
                    filter=flt,
                ),
                qm.Prefetch(
                    query=sparse_vec,
                    using=SPARSE_VECTOR_NAME,
                    limit=prefetch_limit,
                    filter=flt,
                ),
            ],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            limit=max(self._settings.rerank_top_k, prefetch_limit),
            query_filter=flt,
            with_payload=True,
        )
        return list(result.points)

    def _apply_rerank(self, query_text: str, scored_points: list) -> list:
        assert self._reranker is not None
        top = scored_points[: self._settings.rerank_top_k]
        bodies = [(p.payload or {}).get("body", "") for p in top]
        rerank_scores = self._reranker.rerank(query_text, bodies)
        # Replace each point's score with the rerank score and sort desc.
        rescored = []
        for point, new_score in zip(top, rerank_scores, strict=True):
            # qdrant ScoredPoint is immutable; reconstruct via model_copy when
            # available, otherwise mutate the score field directly.
            try:
                rescored.append(point.model_copy(update={"score": float(new_score)}))
            except AttributeError:
                point.score = float(new_score)
                rescored.append(point)
        rescored.sort(key=lambda p: p.score, reverse=True)
        return rescored

    def _materialize_hits(self, scored_points: list, *, excerpt_chars: int) -> list[PeerHit]:
        hits: list[PeerHit] = []
        for scored in scored_points:
            payload = scored.payload or {}
            body = payload.get("body") or ""
            if body:
                excerpt = body.strip()[:excerpt_chars]
                if len(body) > excerpt_chars:
                    excerpt += "…"
            else:
                # Fallback for legacy points indexed before body-in-payload landed.
                excerpt = (
                    f"{payload.get('item_title', '?')} "
                    f"(chars {payload.get('char_start')}-{payload.get('char_end')})"
                )[:excerpt_chars]
            hits.append(
                PeerHit(
                    crd=payload.get("crd", ""),
                    brochure_version_id=payload.get("brochure_version_id", ""),
                    item_number=int(payload.get("item_number", 0)),
                    item_title=payload.get("item_title", ""),
                    score=float(scored.score),
                    body_excerpt=excerpt,
                    payload=payload,
                )
            )
        return hits

    # ── Diagnostics ────────────────────────────────────────────────────
    def count(self) -> int:
        info = self._client.count(self._collection, exact=True)
        return int(info.count)


def _is_local(client: QdrantClient) -> bool:
    """True when QdrantClient is running in-process (`:memory:` or local path)."""
    inner = getattr(client, "_client", None)
    return inner is not None and type(inner).__name__ == "QdrantLocal"


def make_peer_store(
    settings: Settings = default_settings,
    embedder: Embedder | None = None,
    *,
    hybrid: bool = True,
    rerank: bool = True,
) -> PeerStore:
    """Production factory — connects to the configured Qdrant URL.

    Defaults to hybrid + rerank. Pass ``hybrid=False`` / ``rerank=False`` to
    skip lazy-loading the sparse encoder / cross-encoder for callers that
    only need dense retrieval (e.g., one-off diagnostic scripts).
    """
    from adv_lens.retrieval.embeddings import get_embedder
    from adv_lens.retrieval.rerank import get_reranker
    from adv_lens.retrieval.sparse import get_sparse_encoder

    client = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        prefer_grpc=False,
    )
    sparse = get_sparse_encoder(settings) if hybrid else None
    reranker = get_reranker(settings) if rerank else None
    return PeerStore(
        client,
        embedder or get_embedder(settings),
        settings,
        sparse_encoder=sparse,
        reranker=reranker,
    )
