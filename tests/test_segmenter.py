"""HeuristicSegmenter tests — offline, synthetic text only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from adv_lens.segmenter import HeuristicSegmenter, ItemNumber, SegmenterError
from adv_lens.segmenter.heuristic import _pick_real_headers
from adv_lens.segmenter.models import ITEM_TITLES, Section
from eval.schemas import GoldenItem
from eval.scorers.segmenter import score_segmenter


def _full_brochure(items: list[int]) -> str:
    """Build a minimal brochure with headers + 1-line bodies for the given items."""
    lines = ["SYNTHETIC ADVISORY LLC", "Form ADV Part 2A Brochure", ""]
    for n in items:
        lines.append(f"Item {n}. {ITEM_TITLES[n]}")
        lines.append(f"Body text for item {n}.")
        lines.append("")
    return "\n".join(lines)


def test_segmenter_detects_all_eighteen_items() -> None:
    text = _full_brochure(list(range(1, 19)))
    result = HeuristicSegmenter().segment_text(text)

    assert [int(i) for i in result.items_found] == list(range(1, 19))
    assert result.missing_items == []
    assert result.warnings == []
    for section in result.sections:
        assert section.body.startswith(f"Body text for item {int(section.item_number)}")


def test_segmenter_reports_missing_items() -> None:
    text = _full_brochure([1, 2, 3, 5, 6, 7, 8])  # Item 4 deliberately missing
    result = HeuristicSegmenter().segment_text(text)

    assert ItemNumber.ADVISORY_BUSINESS in result.missing_items
    assert ItemNumber.FEES_AND_COMPENSATION not in result.missing_items


def test_segmenter_deduplicates_table_of_contents() -> None:
    # TOC at the top repeats every Item title; real sections follow. Heuristic
    # must pick the post-TOC occurrence for Items after the ~40% cutoff.
    toc_noise = "\n".join(f"Item {n} {ITEM_TITLES[n]}" for n in range(1, 19))
    body = _full_brochure(list(range(1, 19)))
    text = f"{toc_noise}\n\n{body}"

    result = HeuristicSegmenter().segment_text(text)
    # Every section's body should be the "Body text for item N." line, not
    # the TOC line. That's the dedupe working.
    for section in result.sections:
        if int(section.item_number) >= 4:  # Items 4+ live past the 40% cutoff
            assert "Body text for item" in section.body


def test_segmenter_accepts_formatting_variants() -> None:
    # Unicode en/em dashes are deliberate - real brochures use them as
    # header separators; we want the regex to accept these variants.
    text = (
        "Item 1 — Cover Page\n"
        "Body 1.\n"
        "ITEM 2: Material Changes\n"
        "Body 2.\n"
        "Item 3. Table of Contents\n"
        "Body 3.\n"
        "  item 4 – Advisory Business\n"  # noqa: RUF001
        "Body 4.\n"
    )
    result = HeuristicSegmenter().segment_text(text)
    assert [int(i) for i in result.items_found] == [1, 2, 3, 4]


def test_segmenter_rejects_empty_text() -> None:
    with pytest.raises(SegmenterError):
        HeuristicSegmenter().segment_text("   \n   ")


def test_segmenter_marks_not_applicable_as_placeholder() -> None:
    text = "Item 6. Performance-Based Fees\nNot applicable.\nItem 7. Types of Clients\nBody 7.\n"
    result = HeuristicSegmenter().segment_text(text)
    sec6 = result.section(ItemNumber.PERFORMANCE_FEES)
    assert sec6 is not None and sec6.is_placeholder
    sec7 = result.section(ItemNumber.TYPES_OF_CLIENTS)
    assert sec7 is not None and not sec7.is_placeholder


def test_segmenter_does_not_match_item_numbers_above_eighteen() -> None:
    text = "Item 19. Appendix A\nBody 19.\nItem 20 – Final\nBody 20.\n"  # noqa: RUF001
    result = HeuristicSegmenter().segment_text(text)
    assert result.items_found == []


def test_pick_real_headers_prefers_post_toc_hit() -> None:
    total = 1000
    # Item 5 appears at char 100 (in TOC, before 400 cutoff) and 700 (real header).
    hits = {ItemNumber.FEES_AND_COMPENSATION: [(100, 108, "Fees"), (700, 708, "Fees")]}
    picked = _pick_real_headers(hits, total_chars=total)
    assert picked[ItemNumber.FEES_AND_COMPENSATION][0] == 700


def test_pick_real_headers_falls_back_to_only_hit_when_before_cutoff() -> None:
    total = 1000
    hits = {ItemNumber.COVER_PAGE: [(50, 58, "Cover")]}
    picked = _pick_real_headers(hits, total_chars=total)
    assert picked[ItemNumber.COVER_PAGE][0] == 50


# ── Scorer ─────────────────────────────────────────────────────────────
def _golden(expected: list[int]) -> GoldenItem:
    return GoldenItem(
        id="t",
        brochure_crd="999",
        section_id="s",
        section_type="segmenter",
        inputs={"text": ""},
        expected={"items_found": expected},
        labeled_by="test",
        labeled_at=datetime.now(UTC),
    )


def test_scorer_perfect_match_passes() -> None:
    g = _golden(list(range(1, 19)))
    r = score_segmenter(g, {"items_found": list(range(1, 19))})
    assert r.score == 1.0
    assert r.passed is True
    assert r.detail["missing"] == []
    assert r.detail["spurious"] == []


def test_scorer_partial_match_below_threshold_fails() -> None:
    g = _golden(list(range(1, 19)))
    r = score_segmenter(g, {"items_found": [1, 2, 3, 4]})  # recall 4/18
    assert r.score < 0.9
    assert r.passed is False
    assert set(r.detail["missing"]) == set(range(5, 19))


def test_scorer_penalizes_spurious_items() -> None:
    g = _golden([1, 2, 3])
    r = score_segmenter(g, {"items_found": [1, 2, 3, 19, 20]})  # 2 spurious
    assert r.detail["spurious"] == [19, 20]
    assert r.score < 1.0


# ── End-to-end via the actual Section class (pydantic round-trip) ──────
def test_section_length_and_order() -> None:
    text = _full_brochure([1, 2, 3])
    result = HeuristicSegmenter().segment_text(text, source="unit-test")

    assert result.source == "unit-test"
    assert result.backend == "heuristic"
    assert result.total_chars == len(text)
    assert all(isinstance(s, Section) for s in result.sections)
    # char_start ascending
    starts = [s.char_start for s in result.sections]
    assert starts == sorted(starts)
