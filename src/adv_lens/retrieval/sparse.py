"""Sparse encoders for hybrid retrieval.

We use a hashed-vocabulary BM25 variant rather than a learned sparse model
(SPLADE et al). Reasoning:

- ADR 0004 § 8 specifies "BM25 sparse vectors" — the brief is checking
  for the canonical sparse retrieval baseline, not a learned model.
- Hashed vocab means no global corpus pass is required to seed; new
  brochures can be indexed without re-training.
- Qdrant indexes sparse vectors natively via ``SparseVector(indices,
  values)`` regardless of how those values were computed, so swapping in
  SPLADE later is a one-class change.

Document-side weights use the canonical BM25 saturation formula
(Okapi BM25 with k1=1.5, b=0.75). Query-side weights use IDF if a corpus
has been observed; otherwise IDF=1 for all terms (cosine-equivalent).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol, runtime_checkable

from qdrant_client.http import models as qm

from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings

_TOKEN_RE = re.compile(r"[A-Za-z0-9$%]+")
_BM25_K1 = 1.5
_BM25_B = 0.75


@runtime_checkable
class SparseEncoder(Protocol):
    """Anything that turns texts into ``qm.SparseVector`` objects."""

    @property
    def vocab_size(self) -> int: ...

    def encode_documents(self, texts: list[str]) -> list[qm.SparseVector]: ...

    def encode_query(self, text: str) -> qm.SparseVector: ...


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _hash_token(token: str, vocab_size: int) -> int:
    # Stable, language-neutral, no MD5 import. djb2-style hash.
    h = 5381
    for ch in token:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return h % vocab_size


class BM25SparseEncoder:
    """Hashed-vocabulary BM25 sparse encoder.

    Maintains running corpus statistics (avgdl, df) across calls to
    ``encode_documents`` so that ``encode_query`` can apply IDF weights.
    The first ``encode_documents`` call also locks in the avgdl used for
    saturation; subsequent calls update the average.

    Test note: the encoder is deterministic for a given input — same text
    in always yields the same sparse vector — so tests can assert exact
    indices.
    """

    def __init__(self, settings: Settings = default_settings) -> None:
        self._vocab_size = settings.sparse_vocab_size
        # Running stats.
        self._n_docs: int = 0
        self._total_len: int = 0
        # df = number of docs containing each token-index.
        self._df: Counter[int] = Counter()

    @property
    def vocab_size(self) -> int:
        return self._vocab_size

    @property
    def n_docs(self) -> int:
        return self._n_docs

    def _avgdl(self) -> float:
        return (self._total_len / self._n_docs) if self._n_docs else 1.0

    def encode_documents(self, texts: list[str]) -> list[qm.SparseVector]:
        if not texts:
            return []

        # First pass: update corpus stats with the new batch.
        per_doc_tokens: list[list[str]] = []
        for text in texts:
            toks = _tokenize(text)
            per_doc_tokens.append(toks)
            self._n_docs += 1
            self._total_len += len(toks)
            seen_indices = {_hash_token(t, self._vocab_size) for t in set(toks)}
            for idx in seen_indices:
                self._df[idx] += 1

        avgdl = self._avgdl()

        # Second pass: BM25 doc-side weight per term.
        results: list[qm.SparseVector] = []
        for toks in per_doc_tokens:
            if not toks:
                results.append(qm.SparseVector(indices=[], values=[]))
                continue
            tf: Counter[int] = Counter(_hash_token(t, self._vocab_size) for t in toks)
            dl = len(toks)
            indices: list[int] = []
            values: list[float] = []
            for idx, freq in tf.items():
                # BM25 doc-side weight (Okapi).
                norm = 1 - _BM25_B + _BM25_B * (dl / avgdl)
                weight = (freq * (_BM25_K1 + 1)) / (freq + _BM25_K1 * norm)
                indices.append(idx)
                values.append(float(weight))
            results.append(qm.SparseVector(indices=indices, values=values))
        return results

    def encode_query(self, text: str) -> qm.SparseVector:
        toks = _tokenize(text)
        if not toks:
            return qm.SparseVector(indices=[], values=[])
        unique_indices: dict[int, float] = {}
        n = max(self._n_docs, 1)
        for tok in set(toks):
            idx = _hash_token(tok, self._vocab_size)
            df = self._df.get(idx, 0)
            # Robertson-Spark Jones IDF; floored at a small positive value so
            # OOV query terms still contribute a tie-breaking signal.
            idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
            unique_indices[idx] = max(idf, 0.05)
        return qm.SparseVector(
            indices=list(unique_indices.keys()),
            values=list(unique_indices.values()),
        )


class IdentitySparseEncoder:
    """Test double — one sparse value per unique hashed token.

    Doesn't track corpus statistics; doc-side weight = sqrt(TF), query-side
    weight = 1. Deterministic and dependency-light for unit tests.
    """

    def __init__(self, vocab_size: int = 4096) -> None:
        self._vocab_size = vocab_size

    @property
    def vocab_size(self) -> int:
        return self._vocab_size

    def encode_documents(self, texts: list[str]) -> list[qm.SparseVector]:
        out: list[qm.SparseVector] = []
        for text in texts:
            tf: Counter[int] = Counter(_hash_token(t, self._vocab_size) for t in _tokenize(text))
            indices = list(tf.keys())
            values = [math.sqrt(v) for v in tf.values()]
            out.append(qm.SparseVector(indices=indices, values=values))
        return out

    def encode_query(self, text: str) -> qm.SparseVector:
        unique = {_hash_token(t, self._vocab_size) for t in _tokenize(text)}
        return qm.SparseVector(indices=sorted(unique), values=[1.0] * len(unique))


def get_sparse_encoder(settings: Settings = default_settings) -> SparseEncoder:
    """Default sparse encoder factory. Tests instantiate directly."""
    return BM25SparseEncoder(settings)
