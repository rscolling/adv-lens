"""Disciplinary extractor + scorer + node tests, plus reducer composition."""

from __future__ import annotations

from pathlib import Path

import pytest

from adv_lens.app.graph.nodes.extract_disciplinary import extract_disciplinary_node
from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.disciplinary import DisciplinaryExtractor
from adv_lens.extractors.schemas import (
    DisciplinaryEvent,
    DisciplinaryExtraction,
    Extractions,
    FeeExtraction,
    merge_extractions,
)
from adv_lens.llm.audit import MemoryAuditSink
from adv_lens.llm.client import LLMClient
from adv_lens.segmenter import HeuristicSegmenter
from eval.schemas import GoldenItem
from eval.scorers.disciplinary import score_disciplinary

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "disciplinary"


def _load(name: str) -> GoldenItem:
    return GoldenItem.model_validate_json((FIXTURES / name).read_text(encoding="utf-8"))


# ── Schema sanity ─────────────────────────────────────────────────────
def test_disciplinary_event_round_trips() -> None:
    ev = DisciplinaryEvent(
        event_type="sec_administrative",
        event_year=2022,
        authority="SEC",
        involved_party_type="firm",
        allegation="Marketing rule violation.",
        resolution="consent_order",
        sanction_monetary_usd=150_000,
        sanction_other=["censure", "cease and desist"],
    )
    again = DisciplinaryEvent.model_validate(ev.model_dump(mode="json"))
    assert again == ev


def test_disciplinary_extraction_rejects_events_without_history_flag() -> None:
    with pytest.raises(ValueError, match="has events but has_disciplinary_history=False"):
        DisciplinaryExtraction(
            has_disciplinary_history=False,
            events=[
                DisciplinaryEvent(
                    event_type="sro",
                    involved_party_type="firm",
                    allegation="x",
                    resolution="settled",
                )
            ],
        )


# ── Reducer composition ──────────────────────────────────────────────
def test_merge_extractions_composes_disjoint_fields() -> None:
    fee_only = Extractions(fee=FeeExtraction(accepts_performance_fees=False))
    disc_only = Extractions(disciplinary=DisciplinaryExtraction(has_disciplinary_history=False))
    merged = merge_extractions(fee_only, disc_only)
    assert merged.fee is not None
    assert merged.disciplinary is not None
    assert merged.fee.accepts_performance_fees is False
    assert merged.disciplinary.has_disciplinary_history is False


def test_merge_extractions_right_wins_on_overlapping_fields() -> None:
    left = Extractions(fee=FeeExtraction(accepts_performance_fees=False))
    right = Extractions(fee=FeeExtraction(accepts_performance_fees=True))
    merged = merge_extractions(left, right)
    assert merged.fee is not None and merged.fee.accepts_performance_fees is True


# ── Scorer ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("fixture_name", ["item_001.json", "item_002.json", "item_003.json"])
def test_scorer_perfect_match_when_actual_equals_expected(fixture_name: str) -> None:
    item = _load(fixture_name)
    result = score_disciplinary(item, item.expected)
    assert result.score == pytest.approx(1.0)
    assert result.passed is True


def test_scorer_headline_wrong_drops_to_event_weight() -> None:
    item = _load("item_001.json")  # expected: no history, no events
    actual = {"has_disciplinary_history": True, "events": []}
    result = score_disciplinary(item, actual)
    # Headline wrong (0) + events both empty (1.0) = 0.5
    assert result.score == pytest.approx(0.5)
    assert result.passed is False


def test_scorer_missing_event_partially_credits_headline() -> None:
    item = _load("item_002.json")  # one event
    actual = {"has_disciplinary_history": True, "events": []}
    result = score_disciplinary(item, actual)
    # Headline correct (0.5) + event recall=0 → composite 0.5
    assert result.score == pytest.approx(0.5)
    assert result.detail["fn"] == 1


def test_scorer_event_year_matches_when_date_supplied_instead() -> None:
    """When the LLM emits a full event_date instead of event_year, the
    scorer extracts the year from the date and matches against int-typed
    expected event_year. Both sides must coerce to int."""
    item = _load("item_002.json")
    expected_event = item.expected["events"][0]
    actual_event = dict(expected_event)
    actual_event["event_year"] = None
    actual_event["event_date"] = f"{expected_event['event_year']}-06-15"
    actual = {"has_disciplinary_history": True, "events": [actual_event]}
    result = score_disciplinary(item, actual)
    assert result.score == pytest.approx(1.0)
    assert result.passed is True
    assert result.detail["fn"] == 0
    assert result.detail["fp"] == 0


def test_scorer_spurious_event_penalises_precision() -> None:
    item = _load("item_001.json")  # expected: no events
    spurious = {
        "event_type": "sro",
        "event_year": 2020,
        "involved_party_type": "firm",
        "sanction_monetary_usd": 5000,
        "sanction_suspension_days": None,
        "resolution": "settled",
    }
    actual = {"has_disciplinary_history": True, "events": [spurious]}
    result = score_disciplinary(item, actual)
    # Headline wrong (0) + event precision/recall both 0 → 0.0
    assert result.score == pytest.approx(0.0)
    assert result.detail["fp"] == 1


# ── Extractor (with fake LLM) ─────────────────────────────────────────
class _FakeLLMClient(LLMClient):
    def __init__(self, response: DisciplinaryExtraction) -> None:
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


async def test_disciplinary_extractor_short_circuits_on_empty_section() -> None:
    fake = _FakeLLMClient(DisciplinaryExtraction(has_disciplinary_history=False))
    extractor = DisciplinaryExtractor(fake)
    result = await extractor.extract("   \n   ", trace_id="t-1")
    assert result.has_disciplinary_history is False
    assert "Empty Item 9 body" in result.extraction_warnings[0]
    assert fake._audit.records == []


async def test_disciplinary_extractor_invokes_llm_with_correct_node_label() -> None:
    canned = DisciplinaryExtraction(has_disciplinary_history=False)
    fake = _FakeLLMClient(canned)
    extractor = DisciplinaryExtractor(fake)
    await extractor.extract("Item 9.\nNo history.", trace_id="t-1", brochure_crd="108000")
    assert fake._audit.records[0].node == "disciplinary_extractor"


# ── LangGraph node ────────────────────────────────────────────────────
def _state_with_item9(body: str = "Item 9. Disciplinary\nNo history.") -> ADVState:
    text = f"{body}\nItem 10. Other\nNot applicable.\n"
    segmented = HeuristicSegmenter().segment_text(text, source="unit-test")
    return ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )


async def test_extract_disciplinary_node_writes_to_state_extractions() -> None:
    canned = DisciplinaryExtraction(has_disciplinary_history=False)
    extractor = DisciplinaryExtractor(_FakeLLMClient(canned))

    update = await extract_disciplinary_node(_state_with_item9(), extractor=extractor)
    assert "extractions" in update
    assert update["extractions"].disciplinary is canned
    # The node returns ONLY its own field; the reducer composes downstream.
    assert update["extractions"].fee is None


async def test_extract_disciplinary_node_errors_when_section_missing() -> None:
    text = "Item 8. Methods\nBody.\nItem 10. Other\nBody.\n"
    segmented = HeuristicSegmenter().segment_text(text)
    state = ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_pdf_path="/tmp/x.pdf",
        segmented_brochure=segmented,
    )
    update = await extract_disciplinary_node(state)
    assert "errors" in update
    assert "Item 9 missing" in update["errors"][0]


# ── Pipeline-level reducer composition ───────────────────────────────
def test_pipeline_compiles_both_extractor_nodes_in_parallel() -> None:
    """Topology smoke: build_pipeline includes both extractors, both
    fanning out from segment_brochure and joining at END."""
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=True)
    g = compiled.get_graph()
    nodes = set(g.nodes)
    assert {"extract_fee", "extract_disciplinary"}.issubset(nodes)

    # Both extractors must have segment_brochure as a predecessor and END
    # as a successor — that's the parallel fan-out + fan-in shape.
    edges = {(e.source, e.target) for e in g.edges}
    assert ("segment_brochure", "extract_fee") in edges
    assert ("segment_brochure", "extract_disciplinary") in edges


def test_advstate_extractions_field_carries_reducer_annotation() -> None:
    """Sanity: the Annotated metadata is on the field so LangGraph sees it."""
    from typing import get_type_hints

    hints = get_type_hints(ADVState, include_extras=True)
    extractions_hint = hints["extractions"]
    # Annotated[Extractions, merge_extractions] — metadata accessible via __metadata__
    assert hasattr(extractions_hint, "__metadata__")
    assert merge_extractions in extractions_hint.__metadata__
