"""Conflicts extractor + scorer + node tests; three-way reducer composition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adv_lens.app.graph.nodes.extract_conflicts import extract_conflicts_node
from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.conflicts import ConflictsExtractor, build_combined_prompt
from adv_lens.extractors.schemas import (
    AffiliationsItem10,
    BrokeragePracticesItem12,
    CodeOfEthicsItem11,
    ConflictsExtraction,
    DisciplinaryExtraction,
    Extractions,
    FeeExtraction,
    merge_extractions,
)
from adv_lens.llm.audit import MemoryAuditSink
from adv_lens.llm.client import LLMClient
from adv_lens.segmenter import HeuristicSegmenter
from eval.schemas import GoldenItem
from eval.scorers.conflicts import score_conflicts

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "conflicts"


def _load(name: str) -> GoldenItem:
    return GoldenItem.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


# ── Schema sanity ─────────────────────────────────────────────────────
def test_conflicts_extraction_round_trips() -> None:
    ce = ConflictsExtraction(
        item_10_affiliations=AffiliationsItem10(
            affiliated_broker_dealer=True, affiliations=["broker-dealer"]
        ),
        item_11_code_of_ethics=CodeOfEthicsItem11(
            has_code_of_ethics=True, requires_personal_trade_preclearance=True
        ),
        item_12_brokerage=BrokeragePracticesItem12(
            accepts_soft_dollars=True, soft_dollar_within_28e_safe_harbor=True
        ),
    )
    again = ConflictsExtraction.model_validate(ce.model_dump(mode="json"))
    assert again == ce


def test_conflicts_extraction_defaults_to_empty_sub_models() -> None:
    ce = ConflictsExtraction()
    assert ce.item_10_affiliations.affiliated_broker_dealer is None
    assert ce.item_10_affiliations.affiliations == []
    assert ce.item_11_code_of_ethics.has_code_of_ethics is None
    assert ce.item_12_brokerage.accepts_soft_dollars is None


# ── Combined-prompt builder ──────────────────────────────────────────
def test_build_combined_prompt_includes_all_three_headers() -> None:
    p = build_combined_prompt("a body", "b body", "c body")
    assert "=== Item 10" in p
    assert "=== Item 11" in p
    assert "=== Item 12" in p
    assert "a body" in p and "b body" in p and "c body" in p


def test_build_combined_prompt_substitutes_placeholder_for_missing_section() -> None:
    p = build_combined_prompt(None, "body 11", "")
    assert "Item 10 not present" in p
    assert "Item 12 not present" in p
    assert "body 11" in p


# ── Reducer with three-way composition ──────────────────────────────
def test_merge_extractions_composes_three_disjoint_partials() -> None:
    fee = Extractions(fee=FeeExtraction(accepts_performance_fees=False))
    disc = Extractions(disciplinary=DisciplinaryExtraction(has_disciplinary_history=False))
    conf = Extractions(
        conflicts=ConflictsExtraction(
            item_10_affiliations=AffiliationsItem10(affiliated_broker_dealer=True)
        )
    )
    merged = merge_extractions(merge_extractions(fee, disc), conf)
    assert merged.fee is not None
    assert merged.disciplinary is not None
    assert merged.conflicts is not None
    assert merged.conflicts.item_10_affiliations.affiliated_broker_dealer is True


# ── Scorer ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fixture_name", ["item_001.json", "item_002.json", "item_003.json"])
def test_scorer_perfect_match(fixture_name: str) -> None:
    item = _load(fixture_name)
    result = score_conflicts(item, item.expected)
    assert result.score == pytest.approx(1.0)
    assert result.passed is True


def test_scorer_one_wrong_boolean_drops_below_perfect() -> None:
    item = _load("item_001.json")
    actual = json.loads(json.dumps(item.expected))
    actual["item_10_affiliations"]["affiliated_broker_dealer"] = True  # was False
    result = score_conflicts(item, actual)
    assert result.score < 1.0
    assert result.detail["bool_correct"] < result.detail["bool_scored"]


def test_scorer_abstaining_actual_is_treated_as_wrong() -> None:
    item = _load("item_001.json")
    actual = json.loads(json.dumps(item.expected))
    actual["item_12_brokerage"]["accepts_soft_dollars"] = None  # abstain
    result = score_conflicts(item, actual)
    # We graded one fewer correct boolean — accuracy < 1.
    assert result.detail["bool_correct"] == result.detail["bool_scored"] - 1


def test_scorer_affiliations_set_overlap_normalises_case() -> None:
    item = _load("item_002.json")  # expected: ["broker-dealer", "insurance agency", "fund complex"]
    actual = json.loads(json.dumps(item.expected))
    actual["item_10_affiliations"]["affiliations"] = [
        "Broker-Dealer",  # casing difference
        "INSURANCE AGENCY",
        "Fund Complex",
    ]
    result = score_conflicts(item, actual)
    assert result.detail["affiliation_f1"] == pytest.approx(1.0)


def test_scorer_below_threshold_with_many_wrong_booleans() -> None:
    item = _load("item_001.json")
    # Flip every disclosed boolean to its opposite — accuracy collapses.
    actual = json.loads(json.dumps(item.expected))
    for sub in ("item_10_affiliations", "item_11_code_of_ethics", "item_12_brokerage"):
        for k, v in list(actual[sub].items()):
            if isinstance(v, bool):
                actual[sub][k] = not v
    result = score_conflicts(item, actual)
    assert result.passed is False
    assert result.score < 0.75


# ── Extractor (with fake LLM) ─────────────────────────────────────────
class _FakeLLMClient(LLMClient):
    def __init__(self, response: ConflictsExtraction) -> None:
        from adv_lens.app.settings import settings

        super().__init__(MemoryAuditSink(), settings=settings)
        self._response = response

    async def extract(self, **kwargs):  # type: ignore[override]
        from adv_lens.llm.audit import LLMCallRecord

        await self._audit(
            LLMCallRecord(
                trace_id=kwargs["trace_id"],
                node=kwargs["node"],
                brochure_crd=kwargs.get("brochure_crd"),
                model=kwargs["model"],
                prompt={"system": kwargs["system"], "user": kwargs["prompt"]},
                response={"parsed": self._response.model_dump(mode="json")},
            )
        )
        return self._response


async def test_conflicts_extractor_short_circuits_when_all_sections_missing() -> None:
    fake = _FakeLLMClient(ConflictsExtraction())
    extractor = ConflictsExtractor(fake)
    result = await extractor.extract(None, "  ", "", trace_id="t-1")
    assert "Items 10/11/12 all missing" in result.extraction_warnings[0]
    assert fake._audit.records == []


async def test_conflicts_extractor_invokes_llm_with_combined_prompt() -> None:
    canned = ConflictsExtraction()
    fake = _FakeLLMClient(canned)
    extractor = ConflictsExtractor(fake)
    await extractor.extract(
        "Item 10 body", "Item 11 body", "Item 12 body", trace_id="t-1", brochure_crd="108000"
    )
    assert len(fake._audit.records) == 1
    record = fake._audit.records[0]
    assert record.node == "conflicts_extractor"
    user_prompt = record.prompt["user"]
    assert "=== Item 10" in user_prompt
    assert "Item 11 body" in user_prompt


# ── LangGraph node ────────────────────────────────────────────────────
def _state_with_items_10_11_12() -> ADVState:
    text = (
        "Item 10. Other Activities\nNo affiliations.\n"
        "Item 11. Code of Ethics\nWe have a Code.\n"
        "Item 12. Brokerage Practices\nNo soft dollars.\n"
        "Item 13. Reviews\nQuarterly.\n"
    )
    segmented = HeuristicSegmenter().segment_text(text, source="unit-test")
    return ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )


async def test_extract_conflicts_node_writes_to_state_extractions() -> None:
    canned = ConflictsExtraction(
        item_10_affiliations=AffiliationsItem10(affiliated_broker_dealer=False)
    )
    extractor = ConflictsExtractor(_FakeLLMClient(canned))
    update = await extract_conflicts_node(_state_with_items_10_11_12(), extractor=extractor)
    assert "extractions" in update
    assert update["extractions"].conflicts is canned
    # The node returns ONLY its own field — fee + disciplinary stay None.
    assert update["extractions"].fee is None
    assert update["extractions"].disciplinary is None


async def test_extract_conflicts_node_proceeds_when_only_one_section_present() -> None:
    # Only Item 11 is present in the segmentation.
    text = "Item 11. Code of Ethics\nWe have a Code.\nItem 12. Brokerage\nBody.\n"
    segmented = HeuristicSegmenter().segment_text(text)
    state = ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )
    canned = ConflictsExtraction()
    extractor = ConflictsExtractor(_FakeLLMClient(canned))
    update = await extract_conflicts_node(state, extractor=extractor)
    assert "extractions" in update
    assert update["extractions"].conflicts is canned


async def test_extract_conflicts_node_errors_when_all_sections_missing() -> None:
    text = "Item 1. Cover\nBody.\nItem 5. Fees\nBody.\n"
    segmented = HeuristicSegmenter().segment_text(text)
    state = ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )
    update = await extract_conflicts_node(state)
    assert "errors" in update
    assert "all missing" in update["errors"][0]


# ── Pipeline-level: three-way fan-out + reducer ───────────────────────
def test_pipeline_compiles_three_extractor_branches() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=True)
    g = compiled.get_graph()
    nodes = set(g.nodes)
    assert {"extract_fee", "extract_disciplinary", "extract_conflicts"}.issubset(nodes)

    edges = {(e.source, e.target) for e in g.edges}
    assert ("segment_brochure", "extract_fee") in edges
    assert ("segment_brochure", "extract_disciplinary") in edges
    assert ("segment_brochure", "extract_conflicts") in edges
