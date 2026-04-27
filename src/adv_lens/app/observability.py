"""Observability hooks — Langfuse trace emission.

Two surfaces:

* ``get_callbacks()`` returns LangChain-compatible callbacks for the
  LangGraph node-level traces. Wired into ``pipeline.ainvoke`` config.
* ``emit_generation_span(record)`` emits one Langfuse generation span
  per LLM call from ``LLMClient.extract``. Side-effect; never raises;
  silently no-ops when Langfuse isn't configured.

Both functions are guarded so unit tests and dev runs without a
Langfuse instance don't pay any cost.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from adv_lens.app.settings import settings

if TYPE_CHECKING:
    from adv_lens.llm.audit import LLMCallRecord

logger = logging.getLogger(__name__)

# Module-level singleton client cache. None means "not configured";
# False means "configured but failed to initialize" (don't retry forever).
_LANGFUSE_CLIENT: object | None = None


def get_callbacks() -> list:
    """Return LangChain-compatible callbacks for Langfuse, or [] if disabled."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return []
    try:
        # Lazy import — Langfuse is heavy and we don't want it on the import path
        # of unit tests that never touch observability.
        from langfuse.callback import CallbackHandler

        return [
            CallbackHandler(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        ]
    except Exception as e:
        # Broad except is intentional — observability must never block the pipeline.
        logger.warning("Langfuse callback unavailable: %s", e)
        return []


def _get_langfuse_client():
    """Lazily build (and cache) the Langfuse client. Returns None when
    Langfuse isn't configured or failed to initialize."""
    global _LANGFUSE_CLIENT
    if _LANGFUSE_CLIENT is not None:
        return _LANGFUSE_CLIENT or None  # False → None for caller convenience
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        _LANGFUSE_CLIENT = False
        return None
    try:
        from langfuse import Langfuse

        _LANGFUSE_CLIENT = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as e:
        logger.warning("Langfuse client init failed: %s", e)
        _LANGFUSE_CLIENT = False
        return None
    return _LANGFUSE_CLIENT


def emit_generation_span(record: LLMCallRecord) -> None:
    """Emit one Langfuse generation span for an `LLMCallRecord`.

    Side-effect; broadly except'd so an observability hiccup never blocks
    a real pipeline run. Spans roll up under one Langfuse trace per
    pipeline-invocation `trace_id` (deterministic mapping via
    `Langfuse.create_trace_id(seed=...)`).
    """
    client = _get_langfuse_client()
    if client is None:
        return
    try:
        from langfuse.types import TraceContext

        lf_trace_id = client.create_trace_id(seed=record.trace_id)
        observation = client.start_observation(
            name=record.node,
            as_type="generation",
            trace_context=TraceContext(trace_id=lf_trace_id),
            model=record.model,
            input=record.prompt,
            output=record.response,
            usage_details={
                "input": record.prompt_tokens,
                "output": record.completion_tokens,
            },
            cost_details={"total": record.cost_usd},
            metadata={
                "brochure_crd": record.brochure_crd,
                "audited_temperature": record.temperature,
            },
        )
        observation.end()
    except Exception as e:
        # Broad except is intentional — observability must never block extraction.
        logger.warning("Langfuse span emit failed: %s", e)


def get_trace_url(trace_id: str) -> str | None:
    """Return the Langfuse UI URL for a given pipeline `trace_id`, or
    None when Langfuse isn't configured."""
    client = _get_langfuse_client()
    if client is None:
        return None
    try:
        lf_trace_id = client.create_trace_id(seed=trace_id)
        host = settings.langfuse_host.rstrip("/")
        return f"{host}/trace/{lf_trace_id}"
    except Exception:  # pragma: no cover - defensive
        return None


def reset_client_cache_for_tests() -> None:
    """Test helper: clear the singleton so a re-configured settings reads fresh."""
    global _LANGFUSE_CLIENT
    _LANGFUSE_CLIENT = None
