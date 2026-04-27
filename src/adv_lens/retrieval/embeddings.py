"""Embedding backends.

``Embedder`` is the protocol; ``BgeSmallEnEmbedder`` is the production
implementation backed by sentence-transformers; ``RandomEmbedder`` is a
deterministic test double that doesn't need the heavy ML stack on the
import path.

sentence-transformers is imported lazily on first use so unit tests that
never touch the real model don't pay the torch-import cost.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol, runtime_checkable

from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings

logger = logging.getLogger(__name__)


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns a list of strings into a list of float vectors."""

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class BgeSmallEnEmbedder:
    """sentence-transformers wrapper for ``BAAI/bge-small-en-v1.5``.

    bge-small encourages a query-side instruction prefix; we apply it on
    ``embed_query`` and skip it for ``embed`` (which is for stored docs).
    """

    QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

    def __init__(self, settings: Settings = default_settings) -> None:
        self._settings = settings
        self._model = None  # lazy

    def _load(self) -> None:
        if self._model is not None:
            return
        # Lazy import keeps torch off the test import path.
        from sentence_transformers import SentenceTransformer

        logger.info(
            "Loading embedding model %s on %s",
            self._settings.embedding_model_name,
            self._settings.embedding_device,
        )
        self._model = SentenceTransformer(
            self._settings.embedding_model_name,
            device=self._settings.embedding_device,
        )

    @property
    def dim(self) -> int:
        return self._settings.embedding_dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        assert self._model is not None  # _load() populated it
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed([self.QUERY_INSTRUCTION + text])[0]


class RandomEmbedder:
    """Deterministic test embedder — sha256(text) seeds a unit-norm vector.

    Same input always returns the same vector, so similarity tests have a
    stable baseline. Same output dimension as the production model.
    """

    def __init__(self, dim: int = 384) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector_for(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector_for(text)

    def _vector_for(self, text: str) -> list[float]:
        import random as _random

        # Seed an RNG from the sha256 prefix — finite, deterministic, sane.
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = _random.Random(seed)
        floats = [rng.uniform(-1.0, 1.0) for _ in range(self._dim)]
        norm = sum(x * x for x in floats) ** 0.5 or 1.0
        return [x / norm for x in floats]


def get_embedder(settings: Settings = default_settings) -> Embedder:
    """Default embedder factory. Tests instantiate RandomEmbedder directly."""
    return BgeSmallEnEmbedder(settings)
