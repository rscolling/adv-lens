"""Cross-encoder rerankers for the post-fusion top-k.

Hybrid retrieval (dense + BM25 with RRF) gives a balanced candidate list,
but RRF treats each retriever as a black-box ranker. The cross-encoder
re-scores (query, document) pairs jointly so two passages that look
similar on a dot product but differ in semantic match can be reranked.

Default model is ``cross-encoder/ms-marco-MiniLM-L-6-v2`` — small (~80MB),
trained on web-passage retrieval, fast on CPU. Lazy import of
``sentence_transformers.CrossEncoder`` keeps the torch dependency off
unit-test imports.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings

logger = logging.getLogger(__name__)


@runtime_checkable
class Reranker(Protocol):
    """Anything that re-orders (query, doc) pairs and returns scored hits."""

    def rerank(self, query: str, docs: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    """sentence-transformers ``CrossEncoder`` wrapper, lazy-loaded."""

    def __init__(self, settings: Settings = default_settings) -> None:
        self._settings = settings
        self._model = None  # lazy

    def _load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import CrossEncoder

        logger.info("Loading rerank model %s", self._settings.rerank_model_name)
        self._model = CrossEncoder(self._settings.rerank_model_name)

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        self._load()
        scores = self._model.predict([(query, d) for d in docs])  # type: ignore[union-attr]
        # CrossEncoder returns a numpy array.
        return [float(s) for s in scores]


class IdentityReranker:
    """Test double — assigns a deterministic score based on token overlap.

    Higher score for documents sharing more tokens with the query. No ML
    dependency; unit tests can assert exact ordering.
    """

    def rerank(self, query: str, docs: list[str]) -> list[float]:
        from adv_lens.retrieval.sparse import _tokenize

        q_terms = set(_tokenize(query))
        if not q_terms:
            return [0.0] * len(docs)
        scores: list[float] = []
        for d in docs:
            d_terms = set(_tokenize(d))
            scores.append(len(q_terms & d_terms) / len(q_terms))
        return scores


def get_reranker(settings: Settings = default_settings) -> Reranker:
    """Default reranker factory. Tests instantiate directly."""
    return CrossEncoderReranker(settings)
