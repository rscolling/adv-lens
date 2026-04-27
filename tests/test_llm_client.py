"""LLMClient tests — fake Anthropic, real audit sink invocation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from adv_lens.app.settings import Settings
from adv_lens.llm.audit import LLMCallRecord, MemoryAuditSink, logging_audit_sink
from adv_lens.llm.client import LLMClient, LLMError
from adv_lens.llm.cost import estimate_cost_usd


class _Tiny(BaseModel):
    value: int


class _FakeChat:
    """Mimics ``instructor.from_anthropic(...).chat.completions``."""

    def __init__(self, payload: _Tiny, in_tokens: int = 100, out_tokens: int = 50) -> None:
        self._payload = payload
        self._in = in_tokens
        self._out = out_tokens
        self.calls: list[dict] = []

    async def create_with_completion(self, **kwargs):
        self.calls.append(kwargs)
        completion = SimpleNamespace(
            usage=SimpleNamespace(input_tokens=self._in, output_tokens=self._out)
        )
        return self._payload, completion


class _FakeInstructor:
    def __init__(self, fake_chat: _FakeChat) -> None:
        self.chat = SimpleNamespace(completions=fake_chat)


def _client_with_fake(payload: _Tiny, sink=None) -> tuple[LLMClient, MemoryAuditSink, _FakeChat]:
    sink = sink or MemoryAuditSink()
    client = LLMClient(sink, settings=Settings(anthropic_api_key="test-key"))
    fake_chat = _FakeChat(payload)
    client._instructor = _FakeInstructor(fake_chat)
    return client, sink, fake_chat


# ── Cost table ─────────────────────────────────────────────────────────
def test_estimate_cost_known_models() -> None:
    # Sonnet: $3/MTok in, $15/MTok out — 1M in + 1M out = $18
    assert estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_estimate_cost_unknown_model_returns_zero() -> None:
    assert estimate_cost_usd("claude-future-9000", 100, 100) == 0.0


# ── Client behaviour ───────────────────────────────────────────────────
async def test_extract_returns_parsed_payload_and_writes_audit_row() -> None:
    payload = _Tiny(value=42)
    client, sink, fake_chat = _client_with_fake(payload)

    result = await client.extract(
        model="claude-sonnet-4-6",
        system="be concise",
        prompt="answer with 42",
        response_model=_Tiny,
        trace_id="trace-1",
        node="unit_test",
        brochure_crd="108000",
    )

    assert result.value == 42
    assert len(sink.records) == 1
    record = sink.records[0]
    assert isinstance(record, LLMCallRecord)
    assert record.trace_id == "trace-1"
    assert record.node == "unit_test"
    assert record.brochure_crd == "108000"
    assert record.prompt_tokens == 100
    assert record.completion_tokens == 50
    assert record.cost_usd > 0  # sonnet pricing in the table

    # Verify the call shape sent to instructor.
    assert fake_chat.calls[0]["model"] == "claude-sonnet-4-6"
    assert fake_chat.calls[0]["system"] == "be concise"
    assert fake_chat.calls[0]["messages"][0]["content"] == "answer with 42"


async def test_extract_translates_provider_errors_to_llm_error() -> None:
    sink = MemoryAuditSink()
    client = LLMClient(sink, settings=Settings(anthropic_api_key="test-key"))

    class _BoomChat:
        async def create_with_completion(self, **kwargs):
            raise RuntimeError("provider down")

    client._instructor = _FakeInstructor(_BoomChat())  # type: ignore[arg-type]

    with pytest.raises(LLMError, match="RuntimeError: provider down"):
        await client.extract(
            model="claude-sonnet-4-6",
            system="x",
            prompt="y",
            response_model=_Tiny,
            trace_id="t",
            node="n",
        )
    assert sink.records == []  # nothing audited on failure


async def test_extract_does_not_lose_result_when_audit_sink_fails(caplog) -> None:
    payload = _Tiny(value=7)

    async def _exploding_sink(record: LLMCallRecord) -> None:
        raise RuntimeError("postgres unreachable")

    client = LLMClient(_exploding_sink, settings=Settings(anthropic_api_key="test-key"))
    client._instructor = _FakeInstructor(_FakeChat(payload))

    result = await client.extract(
        model="claude-sonnet-4-6",
        system="x",
        prompt="y",
        response_model=_Tiny,
        trace_id="t",
        node="n",
    )
    assert result.value == 7  # extraction survived audit failure


def test_get_instructor_requires_api_key() -> None:
    client = LLMClient(MemoryAuditSink(), settings=Settings(anthropic_api_key=""))
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        client._get_instructor()


# ── Logging sink (no-op fallback) ──────────────────────────────────────
async def test_logging_audit_sink_is_a_noop_that_does_not_raise() -> None:
    record = LLMCallRecord(
        trace_id="t", node="n", model="claude-sonnet-4-6", prompt={}, response={}
    )
    await logging_audit_sink(record)  # must not raise
