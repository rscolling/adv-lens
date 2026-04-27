"""Disciplinary scorer — headline boolean + per-event field overlap.

Two halves:

1. **Headline.** Exact match on ``has_disciplinary_history``. Carries 0.5
   of the score by default — getting the "no events" / "yes events" call
   right is the single most important signal.

2. **Events.** Each event is flattened into a key tuple (event_type,
   year, party_type, monetary, suspension, resolution); we compute set-F1
   over those. Carries the other 0.5.

Composite = ``0.5 * headline + 0.5 * event_f1``. Pass threshold 0.75 —
events have more variance than fee fields, and a shop with no events is
the dominant case.
"""

from __future__ import annotations

from typing import Any

from eval.schemas import GoldenItem, ScoreResult

PASS_THRESHOLD = 0.75
HEADLINE_WEIGHT = 0.5


def _event_key(ev: dict[str, Any]) -> tuple:
    # event_year may come back as int (schema-typed) or as a string when
    # the scorer falls back to slicing event_date. Cast to int both ways
    # so the set-membership check doesn't split on type.
    year_raw = ev.get("event_year")
    if year_raw is None and ev.get("event_date"):
        year_raw = str(ev.get("event_date"))[:4]
    year = int(year_raw) if year_raw is not None else None
    return (
        ev.get("event_type"),
        year,
        ev.get("involved_party_type"),
        ev.get("sanction_monetary_usd"),
        ev.get("sanction_suspension_days"),
        ev.get("resolution"),
    )


def _event_keys(events: list[dict] | None) -> set[tuple]:
    return {_event_key(e) for e in (events or [])}


def score_disciplinary(item: GoldenItem, actual: dict) -> ScoreResult:
    expected_headline = bool(item.expected.get("has_disciplinary_history"))
    actual_headline = bool(actual.get("has_disciplinary_history"))
    headline_correct = expected_headline == actual_headline

    expected_events = _event_keys(item.expected.get("events"))
    actual_events = _event_keys(actual.get("events"))

    tp = len(expected_events & actual_events)
    fp = len(actual_events - expected_events)
    fn = len(expected_events - actual_events)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    if not expected_events and not actual_events:
        # Both sides correctly report zero events — full event score.
        event_f1 = 1.0
    elif (precision + recall) == 0:
        event_f1 = 0.0
    else:
        event_f1 = 2 * precision * recall / (precision + recall)

    headline_score = 1.0 if headline_correct else 0.0
    composite = HEADLINE_WEIGHT * headline_score + (1 - HEADLINE_WEIGHT) * event_f1

    return ScoreResult(
        item_id=item.id,
        section_type=item.section_type,
        score=composite,
        passed=composite >= PASS_THRESHOLD,
        detail={
            "headline_correct": headline_correct,
            "expected_headline": expected_headline,
            "actual_headline": actual_headline,
            "event_precision": round(precision, 4),
            "event_recall": round(recall, 4),
            "event_f1": round(event_f1, 4),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "missing": sorted(str(k) for k in (expected_events - actual_events)),
            "spurious": sorted(str(k) for k in (actual_events - expected_events)),
        },
    )
