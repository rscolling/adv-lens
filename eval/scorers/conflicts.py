"""Conflicts scorer — boolean field accuracy + affiliations set overlap.

The conflicts schema is dominated by ``bool | None`` fields. We score:

1. **Boolean fields** — for each field where expected is non-None, count
   it as correct iff actual matches (None vs True/False both fail).
   Composite = correct / total_scored.
2. **Affiliations list** — set-F1 over short tags.

Final score = 0.7 * boolean_accuracy + 0.3 * affiliation_f1. Boolean
accuracy carries the bulk of the weight because it's what peer
comparison filters on. Pass threshold 0.75 — Items 10/11/12 disclosure
prose has high variance, judging too tightly would over-fit prompts.
"""

from __future__ import annotations

from typing import Any

from eval.schemas import GoldenItem, ScoreResult

PASS_THRESHOLD = 0.75
BOOL_WEIGHT = 0.7

# Boolean fields, addressed as (sub_model_key, field_name).
_BOOL_FIELDS: tuple[tuple[str, str], ...] = (
    ("item_10_affiliations", "affiliated_broker_dealer"),
    ("item_10_affiliations", "affiliated_investment_adviser"),
    ("item_10_affiliations", "affiliated_investment_company"),
    ("item_10_affiliations", "affiliated_insurance"),
    ("item_10_affiliations", "affiliated_bank"),
    ("item_10_affiliations", "uses_other_investment_advisers"),
    ("item_11_code_of_ethics", "has_code_of_ethics"),
    ("item_11_code_of_ethics", "recommends_securities_with_material_interest"),
    ("item_11_code_of_ethics", "personal_trading_in_recommended_securities"),
    ("item_11_code_of_ethics", "requires_personal_trade_preclearance"),
    ("item_11_code_of_ethics", "requires_personal_trade_reporting"),
    ("item_12_brokerage", "accepts_soft_dollars"),
    ("item_12_brokerage", "soft_dollar_within_28e_safe_harbor"),
    ("item_12_brokerage", "accepts_directed_brokerage"),
    ("item_12_brokerage", "requires_directed_brokerage"),
    ("item_12_brokerage", "brokerage_for_referrals"),
    ("item_12_brokerage", "aggregates_orders"),
)


def _get(d: dict, sub: str, field: str) -> Any:
    return (d.get(sub) or {}).get(field)


def _affiliations(d: dict) -> set[str]:
    raw = (d.get("item_10_affiliations") or {}).get("affiliations") or []
    # Normalise to lowercase trimmed for set comparison.
    return {str(a).strip().lower() for a in raw if str(a).strip()}


def score_conflicts(item: GoldenItem, actual: dict) -> ScoreResult:
    expected = item.expected

    # ── Boolean accuracy ────────────────────────────────────────────────
    scored = 0
    correct = 0
    field_misses: list[str] = []
    for sub, field in _BOOL_FIELDS:
        exp_v = _get(expected, sub, field)
        if exp_v is None:
            continue  # fixture abstains — don't grade this field
        scored += 1
        act_v = _get(actual, sub, field)
        if act_v == exp_v:
            correct += 1
        else:
            field_misses.append(f"{sub}.{field}: expected={exp_v!r} actual={act_v!r}")
    bool_accuracy = correct / scored if scored else 1.0

    # ── Affiliations set F1 ─────────────────────────────────────────────
    exp_aff = _affiliations(expected)
    act_aff = _affiliations(actual)
    tp = len(exp_aff & act_aff)
    fp = len(act_aff - exp_aff)
    fn = len(exp_aff - act_aff)
    if not exp_aff and not act_aff:
        aff_f1 = 1.0
    elif (tp + fp) == 0 or (tp + fn) == 0:
        aff_f1 = 0.0
    else:
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        aff_f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    composite = BOOL_WEIGHT * bool_accuracy + (1 - BOOL_WEIGHT) * aff_f1

    return ScoreResult(
        item_id=item.id,
        section_type=item.section_type,
        score=composite,
        passed=composite >= PASS_THRESHOLD,
        detail={
            "bool_accuracy": round(bool_accuracy, 4),
            "bool_correct": correct,
            "bool_scored": scored,
            "affiliation_f1": round(aff_f1, 4),
            "affiliation_missing": sorted(exp_aff - act_aff),
            "affiliation_spurious": sorted(act_aff - exp_aff),
            "field_misses": field_misses,
        },
    )
