"""Tests for the per-brochure HTML/PDF redline renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from adv_lens.extractors.schemas import (
    Finding,
    PeerComparisonNote,
    RedlineReport,
    Scorecard,
    ScoreCategory,
)
from adv_lens.redline.render import (
    _score_colour,
    _severity_colour,
    render_redline_html,
)


def _make_report(
    *,
    overall: int = 78,
    findings: list[Finding] | None = None,
    notes: str | None = None,
) -> RedlineReport:
    return RedlineReport(
        brochure_crd="110181",
        brochure_version_id="1037550",
        scorecard=Scorecard(
            overall_score=overall,
            headline="Test headline.",
            categories=[
                ScoreCategory(name="compliance", score=80, rationale="ok"),
                ScoreCategory(name="transparency", score=70, rationale="thin"),
                ScoreCategory(name="conflicts_handling", score=75, rationale="standard"),
                ScoreCategory(name="fee_competitiveness", score=85, rationale="competitive"),
            ],
        ),
        findings=findings or [],
        notes=notes,
    )


def test_render_html_includes_crd_and_score() -> None:
    html = render_redline_html(_make_report(overall=72))
    assert "CRD 110181" in html
    assert ">72<" in html  # score in the gauge
    assert "Test headline." in html


def test_render_html_lists_all_categories() -> None:
    html = render_redline_html(_make_report())
    for cat_label in ("Compliance", "Transparency", "Conflicts Handling", "Fee Competitiveness"):
        assert cat_label in html


def test_render_html_renders_findings_with_severity() -> None:
    findings = [
        Finding(
            id="F-001",
            category="fee_structure",
            severity="high",
            item_reference=5,
            summary="Missing fee detail",
            detail="No tier breakpoints disclosed.",
            recommendation="Re-extract Item 5.",
            sec_expectation_ref="Item 5.A",
        ),
        Finding(
            id="F-002",
            category="compliance_program",
            severity="info",
            item_reference=9,
            summary="No disciplinary history",
            detail="Item 9 reflects a clean disclosure.",
            recommendation="No action required.",
        ),
    ]
    html = render_redline_html(_make_report(findings=findings))
    assert "F-001" in html
    assert "F-002" in html
    assert "high" in html.lower()
    assert "info" in html.lower()
    assert "Re-extract Item 5." in html
    assert "Findings (2)" in html


def test_render_html_includes_notes_callout_when_present() -> None:
    html = render_redline_html(_make_report(notes="Caveat: peer corpus offline."))
    assert "Caveat: peer corpus offline." in html


def test_render_html_omits_notes_when_absent() -> None:
    html = render_redline_html(_make_report())
    assert "notes-from-the-redline-writer" not in html.lower()


def test_render_html_includes_peer_comparisons_when_present() -> None:
    report = _make_report()
    report = report.model_copy(
        update={
            "peer_comparisons": [
                PeerComparisonNote(
                    item_number=5,
                    peer_count=4,
                    median_peer_position="Top tier rate at the AUM-band median.",
                ),
            ]
        }
    )
    html = render_redline_html(report)
    assert "Peer comparisons" in html
    assert "Top tier rate at the AUM-band median." in html
    assert "Item 5" in html


def test_render_html_includes_meta_provenance_when_provided() -> None:
    meta = {
        "trace_id": "trace-abc",
        "report_hash": "1234567890abcdef" * 4,
        "brochure_sha256": "deadbeef" * 8,
    }
    html = render_redline_html(_make_report(), meta=meta)
    assert "trace-abc" in html
    assert "1234567890abcdef"[:16] in html  # truncated to first 16 chars
    assert "deadbeef" * 2 in html  # also truncated to 16 chars


def test_render_html_is_self_contained() -> None:
    """No external CSS or JS references — safe to email or attach."""
    html = render_redline_html(_make_report())
    assert "<link" not in html
    assert "<script" not in html


@pytest.mark.parametrize(
    "score, expected_band",
    [
        (95, "#198754"),  # green
        (82, "#52a35d"),
        (75, "#a07f1f"),  # amber
        (62, "#c47f1f"),
        (52, "#cc6e2a"),  # orange
        (40, "#b32e2e"),  # red
    ],
)
def test_score_colour_bands(score: int, expected_band: str) -> None:
    assert _score_colour(score) == expected_band


def test_severity_colour_known_levels() -> None:
    assert _severity_colour("critical") != _severity_colour("info")
    assert _severity_colour("HIGH") == _severity_colour("high")  # case-insensitive


def test_severity_colour_unknown_falls_back() -> None:
    assert _severity_colour("not-a-level") == _severity_colour("info")


# ── CLI smoke test ────────────────────────────────────────────────────
def test_cli_renders_wrapped_sample(tmp_path: Path) -> None:
    """CLI accepts the trimmed wrapper {meta, redline} used in
    docs/examples/sample-report.json."""
    import json

    from adv_lens.redline.cli import main

    src = tmp_path / "wrapped.json"
    report = _make_report(findings=[
        Finding(id="F-001", category="compliance_program", severity="info", item_reference=9,
                summary="ok", detail="d", recommendation="r"),
    ])
    src.write_text(
        json.dumps({
            "meta": {"trace_id": "t-1"},
            "redline": report.model_dump(mode="json"),
        }),
        encoding="utf-8",
    )
    out = tmp_path / "wrapped.html"
    rc = main([str(src), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "CRD 110181" in body
    assert "t-1" in body  # meta provenance threaded through


def test_cli_renders_bare_report(tmp_path: Path) -> None:
    """CLI also accepts a bare RedlineReport JSON without the meta wrapper."""
    import json

    from adv_lens.redline.cli import main

    src = tmp_path / "bare.json"
    src.write_text(
        json.dumps(_make_report().model_dump(mode="json")),
        encoding="utf-8",
    )
    out = tmp_path / "bare.html"
    rc = main([str(src), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert "CRD 110181" in out.read_text(encoding="utf-8")
