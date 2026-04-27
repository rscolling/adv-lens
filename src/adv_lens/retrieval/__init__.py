"""Peer-comparison retrieval.

Embeddings, sparse encoders, rerankers, vector store wiring, and the seed
flow that pushes brochure sections into Qdrant. Hybrid retrieval (BM25 +
RRF + cross-encoder rerank) is the default in ``make_peer_store()``;
dense-only is opt-in for callers that don't need the lift.
"""

from adv_lens.retrieval.embeddings import (
    BgeSmallEnEmbedder,
    Embedder,
    RandomEmbedder,
    get_embedder,
)
from adv_lens.retrieval.qdrant_store import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    PeerHit,
    PeerSpec,
    PeerStore,
    make_peer_store,
    point_id_for,
)
from adv_lens.retrieval.rerank import (
    CrossEncoderReranker,
    IdentityReranker,
    Reranker,
    get_reranker,
)
from adv_lens.retrieval.sparse import (
    BM25SparseEncoder,
    IdentitySparseEncoder,
    SparseEncoder,
    get_sparse_encoder,
)

__all__ = [
    "DENSE_VECTOR_NAME",
    "SPARSE_VECTOR_NAME",
    "BM25SparseEncoder",
    "BgeSmallEnEmbedder",
    "CrossEncoderReranker",
    "Embedder",
    "IdentityReranker",
    "IdentitySparseEncoder",
    "PeerHit",
    "PeerSpec",
    "PeerStore",
    "RandomEmbedder",
    "Reranker",
    "SparseEncoder",
    "get_embedder",
    "get_reranker",
    "get_sparse_encoder",
    "make_peer_store",
    "point_id_for",
]
