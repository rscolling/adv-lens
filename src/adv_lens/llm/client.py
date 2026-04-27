"""Async LLM client — Anthropic via Instructor with audit logging.

One ``extract`` method, one shape, all extractors call it. The structured
response model is whatever Pydantic class the extractor wants; Instructor
handles the schema-tool round-trip with the Anthropic API.

Tests inject a fake client (see tests/test_llm_client.py); production
uses ``make_llm_client()`` which wires the real Anthropic SDK.
"""

from __future__ import annotations

import logging
from typing import TypeVar

from pydantic import BaseModel

from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings
from adv_lens.llm.audit import (
    AuditSink,
    LLMCallRecord,
    MemoryAuditSink,
    make_audit_sink,
)
from adv_lens.llm.cost import estimate_cost_usd

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the LLM call fails after retries / validation."""


class LLMClient:
    """Thin Anthropic + Instructor wrapper with audit logging.

    Constructed with an audit sink (callable) and the settings object. The
    Anthropic client is lazy-loaded on first use so unit tests that inject
    a different ``_anthropic`` attribute don't pay the SDK import cost.
    """

    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_TEMPERATURE = 0.0

    def __init__(
        self,
        audit_sink: AuditSink,
        settings: Settings = default_settings,
    ) -> None:
        self._audit = audit_sink
        self._settings = settings
        self._instructor = None  # lazy
        self._anthropic = None  # exposed for tests to swap

    def _get_instructor(self):
        if self._instructor is not None:
            return self._instructor
        # Lazy imports — Instructor + Anthropic stay off the unit-test
        # import path when tests use a fake LLMClient.
        import anthropic
        import instructor

        if not self._settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set; cannot make a live LLM call.")
        self._anthropic = anthropic.AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        self._instructor = instructor.from_anthropic(self._anthropic)
        return self._instructor

    async def extract(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        response_model: type[T],
        trace_id: str,
        node: str,
        brochure_crd: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> T:
        """Call the model, validate against ``response_model``, write audit row, return."""
        client = self._get_instructor()
        temp = self.DEFAULT_TEMPERATURE if temperature is None else temperature
        max_tok = max_tokens or self.DEFAULT_MAX_TOKENS

        try:
            # Anthropic deprecated `temperature` on the claude-4 family
            # (Opus 4.7 hard-rejects it; Sonnet/Haiku will follow). Instructor's
            # tool-call scaffolding gives low entropy regardless. We still record
            # the requested temperature in the audit row to document intent.
            result, completion = await client.chat.completions.create_with_completion(
                model=model,
                response_model=response_model,
                max_tokens=max_tok,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e

        usage = getattr(completion, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        record = LLMCallRecord(
            trace_id=trace_id,
            node=node,
            brochure_crd=brochure_crd,
            model=model,
            temperature=temp,
            prompt={"system": system, "user": prompt},
            response={"parsed": result.model_dump(mode="json")},
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
        )
        try:
            await self._audit(record)
        except Exception as e:
            # Audit failure must not lose the extraction.
            logger.error("audit_sink raised; extraction kept. trace=%s err=%s", trace_id, e)

        # Side-effect: emit a Langfuse generation span. No-ops when Langfuse
        # isn't configured; never raises (own internal except).
        try:
            from adv_lens.app.observability import emit_generation_span

            emit_generation_span(record)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("Langfuse emit skipped: %s", e)

        return result


def make_llm_client(settings: Settings = default_settings) -> LLMClient:
    """Production factory — Postgres-backed audit sink (with logging fallback)."""
    return LLMClient(make_audit_sink(), settings=settings)


def make_test_llm_client(
    settings: Settings = default_settings,
) -> tuple[LLMClient, MemoryAuditSink]:
    """Convenience: a client wired to an in-memory audit sink for assertions."""
    sink = MemoryAuditSink()
    return LLMClient(sink, settings=settings), sink
