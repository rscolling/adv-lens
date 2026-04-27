"""Anthropic LLM client wrapper used by every extractor / redline node.

The pattern is the same for every node:

    extraction = await llm_client.extract(
        model=settings.model_fee_extractor,
        system=PROMPT,
        prompt=section.body,
        response_model=FeeExtraction,
        trace_id=state.trace_id,
        node="fee_extractor",
        brochure_crd=state.brochure_crd,
    )

``LLMClient`` owns audit logging — every call writes a row to the configured
``AuditSink`` (Postgres in production, in-memory list for tests, no-op
logger when Postgres is unreachable).
"""

from adv_lens.llm.audit import (
    AuditSink,
    LLMCallRecord,
    MemoryAuditSink,
    PostgresAuditSink,
    make_audit_sink,
)
from adv_lens.llm.client import LLMClient, LLMError, make_llm_client
from adv_lens.llm.cost import estimate_cost_usd

__all__ = [
    "AuditSink",
    "LLMCallRecord",
    "LLMClient",
    "LLMError",
    "MemoryAuditSink",
    "PostgresAuditSink",
    "estimate_cost_usd",
    "make_audit_sink",
    "make_llm_client",
]
