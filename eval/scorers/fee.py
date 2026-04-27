"""Fee Extractor scorer — field-level F1 over a flattened comparable record.

Extraction fixtures supply the canonical fields the extractor should
recover (pricing model, top-tier rate, minimum account size, billing
frequency / timing, accepts performance fees, key tags). We flatten both
expected and actual into ``(field, value)`` pairs and compute F1.

This rewards getting more right (recall) without letting the extractor
spam fields it's uncertain about (precision). The eval pass threshold is
0.8 — fee schedules are messy and an exact match across every field is a
high bar; we want to catch regressions, not enforce perfection.

Before flattening, we normalise two cosmetic differences that otherwise
explode F1 noise:

1. **Program-name verbosity.** The extractor often labels a schedule
   "Investment Management Services" where the fixture says "Investment
   Management" — same fields, same values, same semantics. We greedily
   align actual schedules to expected ones using token Jaccard on the
   program name (case-folded, stopwords dropped) and rewrite the actual
   name to its expected counterpart for matched pairs.
2. **Tier-breakpoint off-by-one.** Real ADV brochures phrase tier
   boundaries inclusively ("on the first $1,000,000") or exclusively
   ("over $1,000,000"); the extractor sometimes emits ``$1,000,001`` for
   the next tier's lower bound. When a tier's ``rate_basis_points`` and
   ``flat_fee_usd`` exactly match an expected tier within the same
   matched schedule, we snap the actual ``min_assets_usd`` and
   ``max_assets_usd`` to the expected values when within ±1.

Both normalisations are conservative: they only collapse cosmetic
differences. If the extractor invents a schedule, picks a wrong pricing
model, or misses a tier, the scorer still reflects that.
"""

from __future__ import annotations

import re
from typing import Any

from eval.schemas import GoldenItem, ScoreResult

PASS_THRESHOLD = 0.8

_TOP_LEVEL_FIELDS = (
    "accepts_performance_fees",
    "other_compensation_disclosed",  # list[str], compared as set
)
_SCHEDULE_FIELDS = (
    "pricing_model",
    "hourly_rate_low_usd",
    "hourly_rate_high_usd",
    "minimum_annual_fee_usd",
    "minimum_account_size_usd",
    "billing_frequency",
    "billing_timing",
    "fees_negotiable",
)

# Below this token-Jaccard similarity, two program names are considered
# unrelated and won't be aligned. 0.4 admits "Investment Management" ↔
# "Investment Management Services" (Jaccard = 2/3 ≈ 0.67) and rejects
# "Wealth Management" ↔ "Retirement Plan Consulting" (Jaccard = 0).
_NAME_ALIGN_THRESHOLD = 0.4
_NAME_STOPWORDS = frozenset({"the", "and", "of", "for", "to", "a", "an"})
# ±$1 snap window for tier breakpoints. Wider than this and we'd start
# masking real disagreements (a $1,000 tier-boundary error is a real bug).
_TIER_BREAKPOINT_TOLERANCE = 1


def _name_tokens(name: str) -> set[str]:
    """Lowercase, alpha-only tokens minus stopwords, with trailing-s stemming.

    Stemming the trailing 's' folds singular/plural variants so
    "Qualified Client" ↔ "Qualified Clients" and "Fee" ↔ "Fees" don't
    suppress alignment under the Jaccard threshold. Naive but matches the
    domain (program names are descriptive English noun phrases — not the
    edge cases like "process" / "lens" that proper Porter stemming worries
    about). 3+ char minimum keeps "us"/"is" from collapsing.
    """
    if not name:
        return set()
    raw = re.findall(r"[a-z]+", name.lower())
    out: set[str] = set()
    for t in raw:
        if t in _NAME_STOPWORDS or len(t) <= 1:
            continue
        if len(t) >= 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.add(t)
    return out


def _name_similarity(a: str, b: str) -> float:
    """Jaccard similarity over normalised token sets.

    Two empty/null names match perfectly — fixtures that don't bother
    naming the program (e.g. a single-program AUM schedule) shouldn't be
    blocked from alignment. One empty + one populated stays at 0.
    """
    sa, sb = _name_tokens(a), _name_tokens(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _align_schedules(expected: list[dict], actual: list[dict]) -> dict[int, int]:
    """Greedy bipartite alignment ``actual_idx → expected_idx``.

    Highest-similarity pair first; ties broken by index order. Pairs with
    similarity below ``_NAME_ALIGN_THRESHOLD`` are not aligned.
    """
    pairs = [
        (sim, i, j)
        for i, a in enumerate(actual)
        for j, e in enumerate(expected)
        for sim in [_name_similarity(a.get("program_name") or "", e.get("program_name") or "")]
        if sim >= _NAME_ALIGN_THRESHOLD
    ]
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_actual: set[int] = set()
    used_expected: set[int] = set()
    alignment: dict[int, int] = {}
    for _sim, i, j in pairs:
        if i in used_actual or j in used_expected:
            continue
        alignment[i] = j
        used_actual.add(i)
        used_expected.add(j)
    return alignment


def _close_int(a: Any, b: Any, tol: int) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(int(a) - int(b)) <= tol


def _snap_tier_boundaries(
    actual_tiers: list[dict],
    expected_tiers: list[dict],
    tolerance: int = _TIER_BREAKPOINT_TOLERANCE,
) -> list[dict]:
    """Snap actual ``min/max_assets_usd`` to expected when within tolerance.

    Tiers are matched on the (rate_basis_points, flat_fee_usd) pair. Each
    expected tier can absorb at most one actual tier — that prevents
    multiple actual tiers all snapping to the same expected boundary.
    """
    consumed: set[int] = set()
    snapped: list[dict] = []
    for actual_tier in actual_tiers:
        new_tier = dict(actual_tier)
        for j, exp_tier in enumerate(expected_tiers):
            if j in consumed:
                continue
            if (
                exp_tier.get("rate_basis_points") == actual_tier.get("rate_basis_points")
                and exp_tier.get("flat_fee_usd") == actual_tier.get("flat_fee_usd")
                and _close_int(
                    actual_tier.get("min_assets_usd"), exp_tier.get("min_assets_usd"), tolerance
                )
                and _close_int(
                    actual_tier.get("max_assets_usd"), exp_tier.get("max_assets_usd"), tolerance
                )
            ):
                new_tier["min_assets_usd"] = exp_tier.get("min_assets_usd")
                new_tier["max_assets_usd"] = exp_tier.get("max_assets_usd")
                consumed.add(j)
                break
        snapped.append(new_tier)
    return snapped


def _normalise_actual(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied ``actual`` with cosmetic noise snapped to expected.

    Doesn't mutate the caller's ``actual``. Doesn't touch fields that
    aren't subject to a documented normalisation rule.
    """
    expected_schedules = expected.get("schedules") or []
    actual_schedules = actual.get("schedules") or []
    alignment = _align_schedules(expected_schedules, actual_schedules)

    new_actual = dict(actual)
    new_schedules: list[dict] = []
    for i, sched in enumerate(actual_schedules):
        new_sched = dict(sched)
        if i in alignment:
            exp = expected_schedules[alignment[i]]
            new_sched["program_name"] = exp.get("program_name")
            new_sched["tiers"] = _snap_tier_boundaries(
                sched.get("tiers") or [], exp.get("tiers") or []
            )
        new_schedules.append(new_sched)
    new_actual["schedules"] = new_schedules
    return new_actual


def _flatten(extraction: dict[str, Any]) -> set[tuple]:
    pairs: set[tuple] = set()

    for key in _TOP_LEVEL_FIELDS:
        val = extraction.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            for item in val:
                pairs.add((key, item))
        else:
            pairs.add((key, val))

    for sched in extraction.get("schedules", []) or []:
        program = sched.get("program_name") or sched.get("pricing_model") or "?"
        for f in _SCHEDULE_FIELDS:
            v = sched.get(f)
            if v is None:
                continue
            pairs.add(("schedule", program, f, v))
        for tier in sched.get("tiers", []) or []:
            tier_key = (
                "tier",
                program,
                tier.get("min_assets_usd"),
                tier.get("max_assets_usd"),
                tier.get("rate_basis_points"),
                tier.get("flat_fee_usd"),
            )
            pairs.add(tier_key)

    return pairs


def score_fee(item: GoldenItem, actual: dict) -> ScoreResult:
    aligned_actual = _normalise_actual(item.expected, actual)
    expected_pairs = _flatten(item.expected)
    actual_pairs = _flatten(aligned_actual)

    tp = len(expected_pairs & actual_pairs)
    fp = len(actual_pairs - expected_pairs)
    fn = len(expected_pairs - actual_pairs)

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
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "missing": sorted(str(p) for p in (expected_pairs - actual_pairs)),
            "spurious": sorted(str(p) for p in (actual_pairs - expected_pairs)),
        },
    )
