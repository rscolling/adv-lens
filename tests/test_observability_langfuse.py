"""Tests for Langfuse observability hooks."""

from __future__ import annotations

from unittest.mock import MagicMock

from adv_lens.app import observability
from adv_lens.app.settings import settings
from adv_lens.llm.audit import LLMCallRecord


def _record(trace_id: str = "t-1", node: str = "fee_extractor") -> LLMCallRecord:
    return LLMCallRecord(
        trace_id=trace_id,
        node=node,
        brochure_crd="110181",
        model="claude-sonnet-4-6",
        temperature=0.0,
        prompt={"system": "s", "user": "u"},
        response={"parsed": {"x": 1}},
        prompt_tokens=120,
        completion_tokens=40,
        cost_usd=0.0012,
    )


def test_get_callbacks_returns_empty_when_keys_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    assert observability.get_callbacks() == []


def test_emit_generation_span_no_op_without_keys(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    observability.reset_client_cache_for_tests()
    # Should silently no-op; nothing to assert other than "no exception."
    observability.emit_generation_span(_record())


def test_emit_generation_span_calls_start_observation_when_configured(monkeypatch) -> None:
    """When Langfuse is configured, emit_generation_span builds a generation
    span with our model + token counts + cost and ends it."""
    fake_client = MagicMock()
    fake_client.create_trace_id.return_value = "lf-trace-deadbeef" * 2
    fake_obs = MagicMock()
    fake_client.start_observation.return_value = fake_obs

    monkeypatch.setattr(settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk")
    observability.reset_client_cache_for_tests()
    monkeypatch.setattr(observability, "_LANGFUSE_CLIENT", fake_client)

    observability.emit_generation_span(_record(trace_id="t-42", node="redline_writer"))

    fake_client.create_trace_id.assert_called_once_with(seed="t-42")
    _, kwargs = fake_client.start_observation.call_args
    assert kwargs["name"] == "redline_writer"
    assert kwargs["as_type"] == "generation"
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["usage_details"] == {"input": 120, "output": 40}
    assert kwargs["cost_details"] == {"total": 0.0012}
    assert kwargs["metadata"]["brochure_crd"] == "110181"
    fake_obs.end.assert_called_once()


def test_emit_generation_span_swallows_internal_exceptions(monkeypatch, caplog) -> None:
    """If Langfuse raises mid-emit, the call returns silently — observability
    must never block extraction."""
    fake_client = MagicMock()
    fake_client.create_trace_id.side_effect = RuntimeError("simulated langfuse outage")

    monkeypatch.setattr(settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk")
    observability.reset_client_cache_for_tests()
    monkeypatch.setattr(observability, "_LANGFUSE_CLIENT", fake_client)

    # Should not raise.
    observability.emit_generation_span(_record())


def test_get_trace_url_returns_none_without_config(monkeypatch) -> None:
    monkeypatch.setattr(settings, "langfuse_public_key", "")
    monkeypatch.setattr(settings, "langfuse_secret_key", "")
    observability.reset_client_cache_for_tests()
    assert observability.get_trace_url("trace-123") is None


def test_get_trace_url_constructs_host_relative_url(monkeypatch) -> None:
    fake_client = MagicMock()
    fake_client.create_trace_id.return_value = "lf-abc-123"
    monkeypatch.setattr(settings, "langfuse_public_key", "pk")
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk")
    monkeypatch.setattr(settings, "langfuse_host", "http://localhost:3000")
    observability.reset_client_cache_for_tests()
    monkeypatch.setattr(observability, "_LANGFUSE_CLIENT", fake_client)

    url = observability.get_trace_url("trace-xyz")
    assert url == "http://localhost:3000/trace/lf-abc-123"
