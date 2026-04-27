"""RedlineWriter + scorer + node tests, plus pipeline fan-in topology."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adv_lens.app.graph.nodes.write_redline import write_redline_node
from adv_lens.app.graph.state import ADVState
from adv_lens.extractors.redline import RedlineWriter, build_redline_input
from adv_lens.extractors.schemas import (
    ConflictsExtraction,
    DisciplinaryExtraction,
    Extractions,
    FeeExtraction,
    Finding,
    RedlineReport,
    Scorecard,
    ScoreCategory,
)
from adv_lens.llm.audit import MemoryAuditSink
from adv_lens.llm.client import LLMClient
from eval.schemas import GoldenItem
from eval.scorers.redline import score_redline


# ── Schema sanity ────────────────────────────────────────────────────
def test_finding_round_trips() -> None:
    f = Finding(
        id="F-001",
        category="conflict_of_interest",
        severity="medium",
        item_reference=10,
        summary="Affiliated broker-dealer creates material conflict.",
        detail="Firm is owned by the same holding company as Synthetic Securities LLC.",
        sec_expectation_ref="Form ADV Part 2A Instructions, Item 10",
        recommendation="Document mitigation in compliance manual.",
    )
    again = Finding.model_validate(f.model_dump(mode="json"))
    assert again == f


def test_scorecard_requires_at_least_one_category() -> None:
    with pytest.raises(ValueError):
        Scorecard(overall_score=80, categories=[], headline="Headline.")


def test_redline_report_round_trips_with_minimal_payload() -> None:
    rr = RedlineReport(
        brochure_crd="999001",
        scorecard=Scorecard(
            overall_score=85,
            categories=[ScoreCategory(name="compliance", score=90, rationale="Clean.")],
            headline="Clean independent firm.",
        ),
    )
    again = RedlineReport.model_validate(rr.model_dump(mode="json"))
    assert again == rr


def test_finding_severity_high_critical_in_vocabulary() -> None:
    # The Literal type rejects unknown severities at validation time.
    with pytest.raises(ValueError):
        Finding(
            id="F-001",
            category="other",
            severity="catastrophic",  # not in the vocab
            summary="x",
            detail="x",
        )


# ── build_redline_input ──────────────────────────────────────────────
def test_build_redline_input_serialises_extractions_as_json() -> None:
    ex = Extractions(fee=FeeExtraction(accepts_performance_fees=False))
    prompt = build_redline_input("108000", "v1", ex, peer_context=[{"crd": "p1"}])
    assert "108000" in prompt
    assert "v1" in prompt
    assert '"accepts_performance_fees": false' in prompt
    assert "p1" in prompt


def test_build_redline_input_handles_empty_peer_context() -> None:
    ex = Extractions(fee=FeeExtraction())
    prompt = build_redline_input("108000", None, ex, peer_context=None)
    assert '"peer_context": []' in prompt


# ── Scorer ────────────────────────────────────────────────────────────
def _golden(crd: str = "999001") -> GoldenItem:
    return GoldenItem(
        id="t",
        brochure_crd=crd,
        section_id="s",
        section_type="redline",
        inputs={},
        expected={"_validator_only": True},
        labeled_by="test",
        labeled_at=datetime.now(UTC),
    )


def _well_formed_report(crd: str = "999001", n_findings: int = 5) -> dict:
    # Vary severity across findings so the distribution check is happy.
    severities = [
        "info",
        "low",
        "medium",
        "medium",
        "high",
        "info",
        "low",
        "medium",
        "low",
        "info",
        "medium",
        "info",
    ]
    findings = [
        {
            "id": f"F-{i + 1:03d}",
            "category": "conflict_of_interest",
            "severity": severities[i % len(severities)],
            "item_reference": 10,
            "summary": f"Finding {i + 1} headline.",
            "detail": f"Finding {i + 1} detail prose.",
            "sec_expectation_ref": "Form ADV Part 2A Instructions",
            "recommendation": "Review.",
        }
        for i in range(n_findings)
    ]
    return {
        "brochure_crd": crd,
        "brochure_version_id": "v1",
        "scorecard": {
            "overall_score": 80,
            "categories": [
                {"name": "compliance", "score": 80, "rationale": "x"},
                {"name": "transparency", "score": 80, "rationale": "x"},
                {"name": "conflicts_handling", "score": 80, "rationale": "x"},
                {"name": "fee_competitiveness", "score": 80, "rationale": "x"},
            ],
            "headline": "Composite headline sentence.",
        },
        "findings": findings,
        "peer_comparisons": [],
        "extraction_warnings_seen": [],
        "notes": None,
    }


def test_scorer_passes_well_formed_report() -> None:
    report = _well_formed_report()
    result = score_redline(_golden(), report)
    assert result.passed is True
    assert result.score == pytest.approx(1.0)
    assert result.detail["issues"] == []


def test_scorer_flags_missing_scorecard_categories() -> None:
    report = _well_formed_report()
    report["scorecard"]["categories"] = [{"name": "compliance", "score": 80, "rationale": "x"}]
    result = score_redline(_golden(), report)
    assert result.passed is False
    assert any("missing categories" in i for i in result.detail["issues"])


def test_scorer_flags_too_few_findings() -> None:
    report = _well_formed_report(n_findings=2)
    result = score_redline(_golden(), report)
    assert any("only 2 findings" in i for i in result.detail["issues"])


def test_scorer_flags_too_many_findings() -> None:
    report = _well_formed_report(n_findings=15)
    result = score_redline(_golden(), report)
    assert any("exceeds the 12 cap" in i for i in result.detail["issues"])


def test_scorer_flags_unanchored_high_severity() -> None:
    report = _well_formed_report()
    report["findings"][0]["severity"] = "high"
    report["findings"][0]["item_reference"] = None
    result = score_redline(_golden(), report)
    assert any("without item_reference" in i for i in result.detail["issues"])


def test_scorer_flags_pathological_severity_distribution() -> None:
    report = _well_formed_report(n_findings=10)
    for f in report["findings"]:
        f["severity"] = "info"  # 100% info
    result = score_redline(_golden(), report)
    assert any("severity distribution skewed" in i for i in result.detail["issues"])


def test_scorer_flags_brochure_crd_mismatch() -> None:
    report = _well_formed_report(crd="000000")
    result = score_redline(_golden(crd="999001"), report)
    assert any("brochure_crd mismatch" in i for i in result.detail["issues"])
    assert result.detail["metadata_score"] == 0.0


def test_scorer_flags_duplicate_finding_ids() -> None:
    report = _well_formed_report()
    report["findings"][1]["id"] = report["findings"][0]["id"]
    result = score_redline(_golden(), report)
    assert any("not unique" in i for i in result.detail["issues"])


# ── Writer (with fake LLM) ────────────────────────────────────────────
def _canned_report(crd: str = "108000") -> RedlineReport:
    return RedlineReport(
        brochure_crd=crd,
        brochure_version_id="v1",
        scorecard=Scorecard(
            overall_score=82,
            categories=[
                ScoreCategory(name="compliance", score=85, rationale="x"),
                ScoreCategory(name="transparency", score=80, rationale="x"),
                ScoreCategory(name="conflicts_handling", score=78, rationale="x"),
                ScoreCategory(name="fee_competitiveness", score=85, rationale="x"),
            ],
            headline="Mostly clean firm with disclosed soft-dollar arrangements.",
        ),
        findings=[
            Finding(
                id="F-001",
                category="fee_structure",
                severity="info",
                item_reference=5,
                summary="Standard tiered AUM schedule.",
                detail="Fees are 1.00% on the first $1M, declining thereafter.",
            ),
        ],
    )


class _FakeLLMClient(LLMClient):
    def __init__(self, response: RedlineReport) -> None:
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


async def test_writer_short_circuits_when_no_extractions_present() -> None:
    fake = _FakeLLMClient(_canned_report())  # the fake won't actually be called
    writer = RedlineWriter(fake)
    report = await writer.write(
        crd="108000",
        brochure_version_id="v1",
        extractions=Extractions(),  # all None
        trace_id="t-1",
    )
    assert report.brochure_crd == "108000"
    assert report.scorecard.overall_score == 0
    assert "no usable output" in (report.notes or "").lower()
    assert fake._audit.records == []


async def test_writer_invokes_llm_with_redline_node_label() -> None:
    fake = _FakeLLMClient(_canned_report())
    writer = RedlineWriter(fake)
    report = await writer.write(
        crd="108000",
        brochure_version_id="v1",
        extractions=Extractions(fee=FeeExtraction(accepts_performance_fees=False)),
        trace_id="t-1",
    )
    assert report.brochure_crd == "108000"
    record = fake._audit.records[0]
    assert record.node == "redline_writer"


async def test_writer_backfills_brochure_metadata_when_omitted() -> None:
    canned = _canned_report(crd="").model_copy(update={"brochure_version_id": None})
    fake = _FakeLLMClient(canned)
    writer = RedlineWriter(fake)
    report = await writer.write(
        crd="108000",
        brochure_version_id="v42",
        extractions=Extractions(fee=FeeExtraction()),
        trace_id="t-1",
    )
    assert report.brochure_crd == "108000"
    assert report.brochure_version_id == "v42"


# ── LangGraph node ────────────────────────────────────────────────────
def _state_with_extractions() -> ADVState:
    return ADVState(
        trace_id="t-1",
        brochure_crd="108000",
        brochure_version_id="v1",
        extractions=Extractions(
            fee=FeeExtraction(accepts_performance_fees=False),
            disciplinary=DisciplinaryExtraction(has_disciplinary_history=False),
            conflicts=ConflictsExtraction(),
        ),
    )


async def test_write_redline_node_writes_to_state_redline() -> None:
    canned = _canned_report()
    writer = RedlineWriter(_FakeLLMClient(canned))
    update = await write_redline_node(_state_with_extractions(), writer=writer)
    assert "redline" in update
    assert isinstance(update["redline"], RedlineReport)
    assert update["redline"].brochure_crd == "108000"


# ── Pipeline fan-in topology ─────────────────────────────────────────
def test_pipeline_includes_redline_node_after_retrieve_peers() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=True)
    g = compiled.get_graph()
    nodes = set(g.nodes)
    assert "write_redline" in nodes

    edges = {(e.source, e.target) for e in g.edges}
    # Day 11 inserted retrieve_peers before write_redline; Day 12 added
    # hitl_gate after write_redline. Verify the redline-relevant invariants:
    # the redline writer sits between retrieval and the HITL gate, and no
    # earlier node bypasses it.
    assert ("retrieve_peers", "write_redline") in edges
    assert ("write_redline", "hitl_gate") in edges
    end_edges = {src for (src, tgt) in edges if tgt == "__end__"}
    assert "write_redline" not in end_edges  # no longer terminal
    assert "extract_fee" not in end_edges
    assert "extract_disciplinary" not in end_edges
    assert "extract_conflicts" not in end_edges
    assert "retrieve_peers" not in end_edges


def test_pipeline_omits_redline_when_extractors_excluded() -> None:
    from adv_lens.app.graph.pipeline import build_pipeline

    compiled = build_pipeline(include_extractors=False)
    nodes = set(compiled.get_graph().nodes)
    assert "write_redline" not in nodes


# ── Fixture round-trip ────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["item_001.json", "item_002.json"])
def test_redline_fixtures_validate_as_golden_items(name: str) -> None:
    path = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "redline" / name
    item = GoldenItem.model_validate_json(path.read_text(encoding="utf-8"))
    assert item.section_type == "redline"
    # The inputs.extractions should validate as a real Extractions object.
    Extractions.model_validate(item.inputs["extractions"])


def test_redline_fixture_inputs_serialise_to_redline_prompt() -> None:
    path = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "redline" / "item_002.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    extractions = Extractions.model_validate(fixture["inputs"]["extractions"])
    prompt = build_redline_input(
        fixture["brochure_crd"],
        fixture["inputs"]["brochure_version_id"],
        extractions,
        peer_context=fixture["inputs"]["peer_context"],
    )
    # Sanity: the prompt mentions key signal terms from the fixture.
    assert "wrap" in prompt.lower()
    assert "finra" in prompt.lower()
