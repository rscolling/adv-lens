"""Audit sinks for LLM calls.

Three implementations:

- ``PostgresAuditSink`` — production. Inserts one row into
  ``llm_calls`` per call, via the existing SQLModel + SQLAlchemy engine.
- ``MemoryAuditSink`` — tests. Collects ``LLMCallRecord`` instances in a
  list so assertions can verify the right call shape was made.
- ``LoggingAuditSink`` — dev fallback when Postgres is unreachable. Logs
  the call at INFO and drops it. Keeps the pipeline running when an
  engineer is iterating without docker compose up.

The sink contract is a single async callable; no protocol class needed.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LLMCallRecord(BaseModel):
    """One LLM invocation. Persisted to the audit table; surfaced in tests."""

    trace_id: str
    node: str
    brochure_crd: str | None = None
    model: str
    temperature: float = 0.0
    prompt: dict[str, Any]  # {"system": ..., "messages": ...}
    response: dict[str, Any]  # parsed structured output dump + raw flag
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Sink contract: async callable that receives one record and returns nothing.
AuditSink = Callable[[LLMCallRecord], Awaitable[None]]


# ── Memory sink (tests) ────────────────────────────────────────────────
class MemoryAuditSink:
    """Test double — collects records in ``.records`` for assertions."""

    def __init__(self) -> None:
        self.records: list[LLMCallRecord] = []

    async def __call__(self, record: LLMCallRecord) -> None:
        self.records.append(record)


# ── Logging sink (dev fallback) ────────────────────────────────────────
async def logging_audit_sink(record: LLMCallRecord) -> None:
    logger.info(
        "llm_call (audit-skipped, no DB) trace=%s node=%s model=%s tokens=%d/%d cost=$%.4f",
        record.trace_id,
        record.node,
        record.model,
        record.prompt_tokens,
        record.completion_tokens,
        record.cost_usd,
    )


# ── Postgres sink (production) ─────────────────────────────────────────
class PostgresAuditSink:
    """Inserts one ``LLMCall`` row per invocation.

    The session is opened per-call (sync engine in async context via
    ``asyncio.to_thread``) — workable for the modest call volume of the
    extractor pipeline. Switch to async-Postgres if throughput needs grow.
    """

    def __init__(self, dsn: str | None = None) -> None:
        from adv_lens.app.settings import settings

        self._dsn = dsn or settings.postgres_dsn
        self._engine: Any = None  # lazy; typed Any to avoid pulling sqlalchemy.engine at import

    def _get_engine(self) -> Any:
        if self._engine is None:
            from sqlmodel import create_engine

            self._engine = create_engine(self._dsn, echo=False, pool_pre_ping=True)
        return self._engine

    async def __call__(self, record: LLMCallRecord) -> None:
        import asyncio

        await asyncio.to_thread(self._write_sync, record)

    def _write_sync(self, record: LLMCallRecord) -> None:
        from sqlmodel import Session

        from adv_lens.app.storage.audit import LLMCall

        engine = self._get_engine()
        row = LLMCall(
            trace_id=record.trace_id,
            node=record.node,
            brochure_crd=record.brochure_crd,
            model=record.model,
            temperature=record.temperature,
            prompt=record.prompt,
            response=record.response,
            prompt_tokens=record.prompt_tokens,
            completion_tokens=record.completion_tokens,
            cost_usd=record.cost_usd,
            ts=record.ts,
        )
        with Session(engine) as session:
            session.add(row)
            session.commit()


# ── Factory ────────────────────────────────────────────────────────────
def make_audit_sink() -> AuditSink:
    """Pick the production-default sink.

    Returns ``PostgresAuditSink`` when a DSN is configured. The first call
    that fails to write will fall back to logging — see CLAUDE.md: audit
    must never block the pipeline, but missing audit must be loud.
    """
    from adv_lens.app.settings import settings

    if not settings.postgres_dsn:
        return logging_audit_sink

    pg = PostgresAuditSink(settings.postgres_dsn)

    async def safe_pg(record: LLMCallRecord) -> None:
        try:
            await pg(record)
        except Exception as e:
            logger.error(
                "Postgres audit write failed (record dropped to log): %s. trace=%s node=%s",
                e,
                record.trace_id,
                record.node,
            )
            await logging_audit_sink(record)

    return safe_pg
