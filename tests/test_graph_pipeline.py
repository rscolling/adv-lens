"""LangGraph pipeline tests — fetch + segment, fully offline."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from adv_lens.app.graph.nodes import fetch_brochure_node, segment_brochure_node
from adv_lens.app.graph.pipeline import build_pipeline, new_trace_id
from adv_lens.app.graph.state import ADVState
from adv_lens.app.main import app
from adv_lens.app.settings import Settings
from adv_lens.ingestion.iapd import IAPDClient
from adv_lens.segmenter import HeuristicSegmenter, SegmentedBrochure
from adv_lens.segmenter.base import Segmenter
from adv_lens.segmenter.models import ITEM_TITLES

PDF_SIG_HEADER = b"%PDF-1.7\n"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, sec_rate_limit_rps=100.0, sec_max_retries=1)


def _real_pdf_bytes() -> bytes:
    """Minimal real PDF with extractable text. Generated via pypdf."""
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    # pypdf doesn't expose a simple "add text" API; it does accept a blank
    # page for our purposes (the segmenter test mocks the segmenter itself,
    # so we don't need text in the PDF — just bytes that pypdf can open).
    writer.add_blank_page(width=612, height=792)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ── Fetch node ────────────────────────────────────────────────────────
async def test_fetch_node_with_explicit_vid_populates_path(tmp_path: Path) -> None:
    pdf = _real_pdf_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        # Part 2A brochures: files.adviserinfo.sec.gov keyed on BRCHR_VRSN_ID.
        # (reports.* serves Part 1A regulatory data, a different document.)
        assert request.url.host == "files.adviserinfo.sec.gov"
        assert request.url.params["BRCHR_VRSN_ID"] == "999001"
        return httpx.Response(200, content=pdf)

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        update = await fetch_brochure_node(
            ADVState(trace_id="t", brochure_crd="108000", brochure_version_id="999001"),
            client=client,
        )

    assert update["brochure_pdf_path"].endswith("999001.pdf")
    assert update["brochure_sha256"]
    assert update["brochure_from_cache"] is False
    assert "errors" not in update


async def test_fetch_node_resolves_vid_when_missing(tmp_path: Path) -> None:
    pdf = _real_pdf_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.adviserinfo.sec.gov":
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "iacontent": {"brochures": [{"brochureVersionId": "777"}]}
                                }
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, content=pdf)

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        update = await fetch_brochure_node(
            ADVState(trace_id="t", brochure_crd="108000"), client=client
        )

    assert update["brochure_version_id"] == "777"
    assert update["brochure_pdf_path"].endswith("777.pdf")


async def test_fetch_node_records_error_when_no_brochures(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": {"hits": []}})

    async with IAPDClient(_settings(tmp_path), transport=httpx.MockTransport(handler)) as client:
        update = await fetch_brochure_node(
            ADVState(trace_id="t", brochure_crd="108000"), client=client
        )
    assert "errors" in update
    assert "no current brochures" in update["errors"][0]


# ── Segment node ──────────────────────────────────────────────────────
class _FakeSegmenter(Segmenter):
    """Test double that returns a canned SegmentedBrochure regardless of PDF."""

    def __init__(self, segmented: SegmentedBrochure) -> None:
        self._segmented = segmented

    def segment_pdf(self, pdf_path: Path) -> SegmentedBrochure:
        return self._segmented

    def segment_text(self, text: str, *, source: str = "inline") -> SegmentedBrochure:
        return self._segmented


def _full_brochure_text() -> str:
    lines = []
    for n in range(1, 19):
        lines.append(f"Item {n}. {ITEM_TITLES[n]}")
        lines.append(f"Body for item {n}.")
    return "\n".join(lines)


async def test_segment_node_records_segmentation(tmp_path: Path) -> None:
    pdf_path = tmp_path / "brochure.pdf"
    pdf_path.write_bytes(b"%PDF-fake")  # body never read; segmenter is faked
    segmented = HeuristicSegmenter().segment_text(_full_brochure_text(), source=str(pdf_path))

    update = await segment_brochure_node(
        ADVState(trace_id="t", brochure_crd="108000", brochure_pdf_path=str(pdf_path)),
        segmenter=_FakeSegmenter(segmented),
    )
    assert update["segmented_brochure"] is segmented
    assert "errors" not in update


async def test_segment_node_records_missing_items(tmp_path: Path) -> None:
    pdf_path = tmp_path / "brochure.pdf"
    pdf_path.write_bytes(b"%PDF-fake")
    partial_text = "Item 1. Cover\nBody.\nItem 2. Material Changes\nBody.\n"
    segmented = HeuristicSegmenter().segment_text(partial_text, source=str(pdf_path))

    update = await segment_brochure_node(
        ADVState(trace_id="t", brochure_crd="108000", brochure_pdf_path=str(pdf_path)),
        segmenter=_FakeSegmenter(segmented),
    )
    assert update["segmented_brochure"] is segmented
    assert "errors" in update
    assert "missing items" in update["errors"][0]


async def test_segment_node_errors_when_no_pdf_path() -> None:
    update = await segment_brochure_node(ADVState(trace_id="t", brochure_crd="108000"))
    assert "errors" in update
    assert "no brochure_pdf_path" in update["errors"][0]


async def test_segment_node_handles_segmenter_failure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "missing.pdf"
    update = await segment_brochure_node(
        ADVState(trace_id="t", brochure_crd="108000", brochure_pdf_path=str(pdf_path))
    )
    assert "errors" in update
    assert "FileNotFoundError" in update["errors"][0]


# ── End-to-end pipeline (offline) ─────────────────────────────────────
async def test_pipeline_runs_fetch_then_segment(monkeypatch, tmp_path: Path) -> None:
    pdf = _real_pdf_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf)

    settings_for_test = _settings(tmp_path)
    transport = httpx.MockTransport(handler)

    # Build pipeline + override the fetch node's IAPDClient via partial closure.
    from langgraph.graph import END, START, StateGraph

    from adv_lens.app.graph.nodes.segment import segment_brochure_node as seg_node

    test_segmenter = _FakeSegmenter(HeuristicSegmenter().segment_text(_full_brochure_text()))

    async def _fetch(state: ADVState) -> dict:
        async with IAPDClient(settings_for_test, transport=transport) as c:
            return await fetch_brochure_node(state, client=c)

    async def _seg(state: ADVState) -> dict:
        return await seg_node(state, segmenter=test_segmenter)

    graph = StateGraph(ADVState)
    graph.add_node("fetch_brochure", _fetch)
    graph.add_node("segment_brochure", _seg)
    graph.add_edge(START, "fetch_brochure")
    graph.add_edge("fetch_brochure", "segment_brochure")
    graph.add_edge("segment_brochure", END)
    pipeline = graph.compile()

    initial = ADVState(trace_id=new_trace_id(), brochure_crd="108000", brochure_version_id="999001")
    result_dict = await pipeline.ainvoke(initial)
    state = ADVState.model_validate(result_dict)

    assert state.brochure_pdf_path is not None
    assert state.segmented_brochure is not None
    assert len(state.segmented_brochure.sections) == 18
    assert state.errors == []


def test_build_pipeline_accepts_state_and_compiles() -> None:
    # Topological smoke-check: compile succeeds and exposes ainvoke.
    pipeline = build_pipeline()
    assert hasattr(pipeline, "ainvoke")


async def test_state_errors_accumulates_from_parallel_branches() -> None:
    """Regression: ``state.errors`` must use a reducer so parallel branches
    can each append without colliding.

    Reproduces a real failure observed in the live demo: a corrupted
    cached PDF made segmentation fail, then the three parallel
    extractor branches each short-circuited with their own error
    message, and LangGraph raised
    ``InvalidUpdateError: At key 'errors': Can receive only one value
    per step. Use an Annotated key to handle multiple values.``
    The fix annotates ``ADVState.errors`` with ``operator.add``; this
    test pins the behavior in place by fanning out three error-emitting
    nodes and asserting all three messages survive the fan-in.
    """
    from langgraph.graph import END, START, StateGraph

    async def _branch_a(state: ADVState) -> dict:
        return {"errors": ["branch_a failed"]}

    async def _branch_b(state: ADVState) -> dict:
        return {"errors": ["branch_b failed"]}

    async def _branch_c(state: ADVState) -> dict:
        return {"errors": ["branch_c failed"]}

    async def _fanin(state: ADVState) -> dict:
        return {}

    graph = StateGraph(ADVState)
    graph.add_node("a", _branch_a)
    graph.add_node("b", _branch_b)
    graph.add_node("c", _branch_c)
    graph.add_node("fanin", _fanin)
    graph.add_edge(START, "a")
    graph.add_edge(START, "b")
    graph.add_edge(START, "c")
    graph.add_edge("a", "fanin")
    graph.add_edge("b", "fanin")
    graph.add_edge("c", "fanin")
    graph.add_edge("fanin", END)
    pipeline = graph.compile()

    initial = ADVState(trace_id="errors-reducer-test", brochure_crd="123")
    result = await pipeline.ainvoke(initial)
    state = ADVState.model_validate(result)

    assert sorted(state.errors) == [
        "branch_a failed",
        "branch_b failed",
        "branch_c failed",
    ]


def test_pipeline_run_endpoint_validates_crd() -> None:
    client = TestClient(app)
    r = client.post("/pipeline/run", json={"crd": "abc"})
    assert r.status_code == 422  # pydantic pattern rejects non-numeric


@pytest.mark.parametrize("vid", ["abc", "12.3"])
def test_pipeline_run_endpoint_validates_vid(vid: str) -> None:
    client = TestClient(app)
    r = client.post("/pipeline/run", json={"crd": "108000", "brochure_version_id": vid})
    assert r.status_code == 422
