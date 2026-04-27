"""Fee extractor + scorer tests.

End-to-end: load each fee fixture, hand the expected payload back as the
fake LLM's "response", verify the scorer reports F1=1.0. This proves the
schema, the scorer, and the fixture format all line up before the real
LLM ever runs against them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adv_lens.app.graph.nodes.extract_fee import extract_fee_node
from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.fee import FeeExtractor
from adv_lens.extractors.schemas import (
    Extractions,
    FeeExtraction,
    FeeSchedule,
    FeeTier,
)
from adv_lens.llm.audit import MemoryAuditSink
from adv_lens.llm.client import LLMClient
from adv_lens.segmenter import HeuristicSegmenter
from adv_lens.segmenter.models import ItemNumber
from eval.schemas import GoldenItem
from eval.scorers.fee import score_fee

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "fee"


def _load_fixture(name: str) -> GoldenItem:
    return GoldenItem.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


# ── Schema sanity ─────────────────────────────────────────────────────
def test_fee_tier_requires_rate_or_flat() -> None:
    with pytest.raises(ValueError):
        FeeTier(min_assets_usd=0, max_assets_usd=1_000_000)


def test_fee_extraction_round_trips_through_json() -> None:
    fee = FeeExtraction(
        schedules=[
            FeeSchedule(
                pricing_model="aum_tiered",
                tiers=[FeeTier(rate_basis_points=100)],
                billing_frequency="quarterly",
                billing_timing="advance",
            )
        ],
        accepts_performance_fees=False,
    )
    payload = fee.model_dump(mode="json")
    again = FeeExtraction.model_validate(payload)
    assert again == fee


def test_extractions_merge_keeps_existing_when_other_is_none() -> None:
    base = Extractions(fee=FeeExtraction(accepts_performance_fees=True))
    merged = base.merge(Extractions())  # other carries nothing
    assert merged.fee is not None
    assert merged.fee.accepts_performance_fees is True


def test_extractions_merge_overwrites_when_other_provides_value() -> None:
    base = Extractions(fee=FeeExtraction(accepts_performance_fees=True))
    new_fee = FeeExtraction(accepts_performance_fees=False)
    merged = base.merge(Extractions(fee=new_fee))
    assert merged.fee is not None
    assert merged.fee.accepts_performance_fees is False


# ── Scorer ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fixture_name", ["item_001.json", "item_002.json", "item_003.json"])
def test_scorer_reports_perfect_match_when_actual_equals_expected(fixture_name: str) -> None:
    item = _load_fixture(fixture_name)
    result = score_fee(item, item.expected)
    assert result.score == pytest.approx(1.0)
    assert result.passed is True
    assert result.detail["missing"] == []
    assert result.detail["spurious"] == []


def test_scorer_partial_match_below_threshold_fails() -> None:
    item = _load_fixture("item_001.json")
    # Strip out billing fields → recall drops below 0.8.
    actual = json.loads(json.dumps(item.expected))  # deep copy
    for sched in actual["schedules"]:
        sched["billing_frequency"] = None
        sched["billing_timing"] = None
        sched["fees_negotiable"] = None
        sched["tiers"] = sched["tiers"][:1]  # drop most tiers
    result = score_fee(item, actual)
    assert result.score < 0.8
    assert result.passed is False
    assert result.detail["fn"] > 0


def test_scorer_penalizes_spurious_pricing_models() -> None:
    item = _load_fixture("item_001.json")
    actual = json.loads(json.dumps(item.expected))
    actual["schedules"].append(
        {
            "pricing_model": "performance",
            "program_name": "Phantom Performance Fee",
            "tiers": [],
            "hourly_rate_low_usd": None,
            "hourly_rate_high_usd": None,
            "minimum_annual_fee_usd": None,
            "minimum_account_size_usd": None,
            "billing_frequency": "varies",
            "billing_timing": "varies",
            "fees_negotiable": None,
        }
    )
    result = score_fee(item, actual)
    assert result.detail["fp"] > 0
    assert result.score < 1.0


def test_scorer_aligns_verbose_program_names_to_expected() -> None:
    """A schedule labelled 'Investment Management Services' must be matched
    to the fixture's 'Investment Management' — same fields, same values,
    no F1 penalty for the cosmetic suffix."""
    item = _load_fixture("item_002.json")
    actual = json.loads(json.dumps(item.expected))
    for sched in actual["schedules"]:
        if sched["program_name"] == "Investment Management":
            sched["program_name"] = "Investment Management Services"
        elif sched["program_name"] == "Financial Planning":
            sched["program_name"] = "Standalone Financial Planning"
    result = score_fee(item, actual)
    assert result.score == pytest.approx(1.0)
    assert result.passed is True
    assert result.detail["missing"] == []
    assert result.detail["spurious"] == []


def test_scorer_aligns_singular_plural_program_names() -> None:
    """'Qualified Client' should match 'Qualified Clients' — token stemming
    folds trailing-s so singular/plural variants don't suppress alignment."""
    item = _load_fixture("item_002.json")
    actual = json.loads(json.dumps(item.expected))
    for sched in actual["schedules"]:
        if sched["program_name"] == "Investment Management":
            sched["program_name"] = "Investment Managements"  # plural
        elif sched["program_name"] == "Financial Planning":
            sched["program_name"] = "Plans"  # also stemmed via -s
    # Plans alone has Jaccard 0 with Financial Planning so it won't align;
    # Investment Managements ↔ Investment Management should still align.
    result = score_fee(item, actual)
    # The Investment Management schedule's fields should be TPs again.
    assert any("Investment Management" in s for s in (result.detail.get("missing") or [])) is False


def test_scorer_does_not_align_unrelated_program_names() -> None:
    """Token-Jaccard < threshold → no alignment, fields count as FP/FN."""
    item = _load_fixture("item_002.json")
    actual = json.loads(json.dumps(item.expected))
    for sched in actual["schedules"]:
        sched["program_name"] = "Retirement Plan Consulting"  # zero overlap
    result = score_fee(item, actual)
    assert result.score < 1.0
    assert result.detail["fn"] > 0


def test_scorer_snaps_tier_breakpoints_within_one_dollar() -> None:
    """Off-by-one on tier min/max (right-exclusive vs left-inclusive
    phrasing) should not penalize when rate + flat_fee match exactly."""
    item = _load_fixture("item_001.json")
    actual = json.loads(json.dumps(item.expected))
    for sched in actual["schedules"]:
        for tier in sched.get("tiers", []):
            if tier.get("min_assets_usd") is not None:
                tier["min_assets_usd"] += 1
            if tier.get("max_assets_usd") is not None:
                tier["max_assets_usd"] += 1
    result = score_fee(item, actual)
    assert result.score == pytest.approx(1.0)
    assert result.passed is True


def test_scorer_does_not_snap_tier_breakpoints_beyond_tolerance() -> None:
    """A $1,000 tier-boundary disagreement is a real bug — must register."""
    item = _load_fixture("item_001.json")
    actual = json.loads(json.dumps(item.expected))
    for sched in actual["schedules"]:
        for tier in sched.get("tiers", []):
            if tier.get("min_assets_usd") is not None:
                tier["min_assets_usd"] += 1000
    result = score_fee(item, actual)
    assert result.score < 1.0
    assert result.detail["fp"] > 0


def test_scorer_does_not_snap_tier_when_rate_disagrees() -> None:
    """Tier boundary snap is gated on (rate, flat_fee) exact match — a
    different rate at the same boundary is a real disagreement."""
    item = _load_fixture("item_001.json")
    actual = json.loads(json.dumps(item.expected))
    for sched in actual["schedules"]:
        for tier in sched.get("tiers", []):
            if tier.get("rate_basis_points") is not None:
                tier["rate_basis_points"] += 5  # 5bp off, well outside snap intent
    result = score_fee(item, actual)
    assert result.score < 1.0


# ── Extractor (with fake LLM) ─────────────────────────────────────────
class _FakeLLMClient(LLMClient):
    """LLMClient subclass that returns a canned object without touching Anthropic."""

    def __init__(self, response: FeeExtraction) -> None:
        super().__init__(
            MemoryAuditSink(),
            settings=__import__("adv_lens.app.settings", fromlist=["settings"]).settings,
        )
        self._response = response

    async def extract(self, **kwargs):  # type: ignore[override]
        # Audit the call so tests can verify wiring.
        from adv_lens.llm.audit import LLMCallRecord

        record = LLMCallRecord(
            trace_id=kwargs["trace_id"],
            node=kwargs["node"],
            brochure_crd=kwargs.get("brochure_crd"),
            model=kwargs["model"],
            prompt={"system": kwargs["system"], "user": kwargs["prompt"]},
            response={"parsed": self._response.model_dump(mode="json")},
        )
        await self._audit(record)
        return self._response


async def test_fee_extractor_passes_section_body_through() -> None:
    canned = FeeExtraction(accepts_performance_fees=False)
    fake = _FakeLLMClient(canned)
    extractor = FeeExtractor(fake)

    result = await extractor.extract(
        "Item 5 — Fees and Compensation\n1.00% AUM fee.",
        trace_id="t-1",
        brochure_crd="108000",
    )
    assert result is canned
    assert fake._audit.records[0].node == "fee_extractor"
    assert fake._audit.records[0].brochure_crd == "108000"


async def test_fee_extractor_short_circuits_on_empty_section() -> None:
    fake = _FakeLLMClient(FeeExtraction())
    extractor = FeeExtractor(fake)
    result = await extractor.extract("   \n   ", trace_id="t-1")
    assert "Empty Item 5 body" in (
        result.extraction_warnings[0] if result.extraction_warnings else ""
    )
    # No LLM call was made (sink stayed empty).
    assert fake._audit.records == []


# ── LangGraph node ────────────────────────────────────────────────────
def _state_with_item5(body: str = "Item 5. Fees\n1.00% AUM fee.") -> ADVState:
    text = f"{body}\nItem 6. Performance\nNot applicable.\n"
    segmented = HeuristicSegmenter().segment_text(text, source="unit-test")
    state = ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )
    return state


async def test_extract_fee_node_writes_to_state_extractions() -> None:
    canned = FeeExtraction(accepts_performance_fees=False)
    fake = _FakeLLMClient(canned)
    extractor = FeeExtractor(fake)

    update = await extract_fee_node(_state_with_item5(), extractor=extractor)
    assert "extractions" in update
    new_state_extractions = update["extractions"]
    assert new_state_extractions.fee is canned


async def test_extract_fee_node_errors_when_segmentation_missing() -> None:
    state = ADVState(trace_id="t-1", brochure_crd="108000")
    update = await extract_fee_node(state)
    assert "errors" in update
    assert "no segmented_brochure" in update["errors"][0]


async def test_extract_fee_node_errors_when_item5_is_placeholder() -> None:
    text = "Item 5. Fees\nNot applicable.\nItem 6. Performance\nNot applicable.\n"
    segmented = HeuristicSegmenter().segment_text(text)
    assert segmented.section(ItemNumber.FEES_AND_COMPENSATION) is not None  # sanity
    state = ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )
    update = await extract_fee_node(state)
    assert "errors" in update
    assert "placeholder" in update["errors"][0]


# ── Pipeline factory wiring ───────────────────────────────────────────
def test_build_pipeline_excludes_extractors_without_api_key(monkeypatch) -> None:
    from adv_lens.app.graph import pipeline as pmod

    monkeypatch.setattr(pmod.default_settings, "anthropic_api_key", "", raising=False)
    compiled = pmod.build_pipeline()
    nodes = set(compiled.get_graph().nodes)
    assert "extract_fee" not in nodes


def test_build_pipeline_includes_extractors_when_explicitly_requested() -> None:
    from adv_lens.app.graph import pipeline as pmod

    compiled = pmod.build_pipeline(include_extractors=True)
    nodes = set(compiled.get_graph().nodes)
    assert "extract_fee" in nodes
