"""Segmenter scorer — set-level F1 over detected Item numbers.

The heuristic segmenter is about header detection, not content extraction.
We grade it on which Item numbers it correctly identifies (precision,
recall, F1) and pass the item if F1 >= 0.9. Per-Item content-quality is
graded by the downstream extractor scorers starting Week 2.
"""

from __future__ import annotations

from eval.schemas import GoldenItem, ScoreResult

PASS_THRESHOLD = 0.9


def score_segmenter(item: GoldenItem, actual: dict) -> ScoreResult:
    expected_items: set[int] = {int(n) for n in item.expected.get("items_found", [])}
    actual_items: set[int] = {int(n) for n in actual.get("items_found", [])}

    tp = len(expected_items & actual_items)
    fp = len(actual_items - expected_items)
    fn = len(expected_items - actual_items)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return ScoreResult(
        item_id=item.id,
        section_type=item.section_type,
        score=f1,
        passed=f1 >= PASS_THRESHOLD,
        detail={
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "expected": sorted(expected_items),
            "actual": sorted(actual_items),
            "missing": sorted(expected_items - actual_items),
            "spurious": sorted(actual_items - expected_items),
        },
    )
