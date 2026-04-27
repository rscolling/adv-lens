from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Anthropic
    anthropic_api_key: str = ""
    model_segmenter: str = "claude-haiku-4-5-20251001"
    model_fee_extractor: str = "claude-sonnet-4-6"
    model_disciplinary: str = "claude-haiku-4-5-20251001"
    model_conflicts: str = "claude-sonnet-4-6"
    model_redline: str = "claude-opus-4-7"

    # Langfuse
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Postgres
    postgres_dsn: str = "postgresql+psycopg://adv_lens:adv_lens_dev@localhost:5432/adv_lens"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection_peers: str = "adv_peers"

    # Embeddings (dense)
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    embedding_device: str = "cpu"

    # Hybrid retrieval (sparse + rerank)
    sparse_vocab_size: int = 65536  # hashed-vocabulary dimension for BM25
    rerank_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    hybrid_prefetch_limit: int = 20  # per-prefetch top-k before RRF fusion
    rerank_top_k: int = 50  # how many fused hits to send through the reranker

    # Peer-retrieval node
    peer_query_top_k_per_item: int = 5  # peer hits per Item kept on state.peer_context

    # Pipeline jobs (async worker)
    # Stuck-row reaper threshold: a row stays in `running` longer than this
    # is assumed to belong to a dead worker process (see ADR 0011 section 6).
    pipeline_run_reap_threshold_minutes: int = 10

    # SEC
    sec_iapd_base_url: str = "https://adviserinfo.sec.gov"
    sec_iapd_files_base_url: str = "https://files.adviserinfo.sec.gov"
    sec_iapd_api_base_url: str = "https://api.adviserinfo.sec.gov"
    # The reports.* host serves the regulatory Form ADV Part 1A as
    # /reports/ADV/<CRD>/PDF/<CRD>.pdf — useful for firm registration data
    # but NOT the Part 2A narrative brochure. Part 2A brochures stayed on
    # files.adviserinfo.sec.gov keyed by BRCHR_VRSN_ID; my earlier patch
    # mistakenly switched the brochure path to /reports/ADV which served
    # Part 1A and broke downstream extractors. Both URLs are valid for
    # different purposes.
    sec_iapd_reports_base_url: str = "https://reports.adviserinfo.sec.gov"
    # SEC's CDN does naive User-Agent bot detection on files.* — any UA
    # without "Mozilla" / "Gecko" gets a 404 even though SEC's official
    # guidance is "descriptive UA with contact info". The polite-bot
    # hybrid below mirrors Googlebot's pattern: browser-shaped prefix to
    # pass the filter, plus our identification + contact for log-readers.
    sec_user_agent: str = "Mozilla/5.0 (compatible; ADV-Lens/0.1; +mailto:robert.colling@gmail.com)"
    sec_rate_limit_rps: float = 5.0
    sec_request_timeout_s: float = 30.0
    sec_max_retries: int = 3

    # Local data
    data_dir: Path = _REPO_ROOT / "data"

    # Parsing fallback
    llama_cloud_api_key: str = ""

    # Feature flags
    use_ollama_fallback: bool = False
    ollama_host: str = "http://localhost:11434"
    enable_hitl: bool = True
    log_level: str = Field(default="INFO")


settings = Settings()
