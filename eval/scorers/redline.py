"""Redline scorer — structural validator (week-2 baseline).

Per the brief, the redline writer is graded by LLM-as-judge with a second
judge model cross-checking the first to catch judge drift. That lands in
week 4. For now (week 2), the scorer enforces structural quality:

- Pydantic schema validates (the runner already does this implicitly).
- Scorecard is populated with all four categories at non-zero scores.
- At least 4 findings present (the prompt asks for 4-12).
- Each finding has an ``id`` of the form ``F-NNN`` and a non-empty
  ``summary`` and ``detail``.
- Critical/high severity findings cite an ``item_reference`` (we shouldn't
  flag a high-severity issue and not point at the Item it lives in).
- Severity distribution isn't pathological (everything-critical or
  everything-info is a smell — score down).

Pass threshold 0.8 — structural quality is non-negotiable; we want
regressions caught, not "best-effort" tolerated.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from eval.schemas import GoldenItem, ScoreResult

PASS_THRESHOLD = 0.8

_REQUIRED_CATEGORIES = {
    "compliance",
    "transparency",
    "conflicts_handling",
    "fee_competitiveness",
}
_FINDING_ID_RE = re.compile(r"^F-\d{3,}$")
_HIGH_SEVERITY = {"critical", "high"}


def _check_scorecard(actual: dict, issues: list[str]) -> float:
    sc = actual.get("scorecard") or {}
    cats = sc.get("categories") or []
    cat_names = {c.get("name") for c in cats if isinstance(c, dict)}
    missing = _REQUIRED_CATEGORIES - cat_names
    if missing:
        issues.append(f"scorecard missing categories: {sorted(missing)}")
        return 0.0
    if not isinstance(sc.get("overall_score"), int):
        issues.append("scorecard.overall_score is not an integer")
        return 0.0
    if not (sc.get("headline") or "").strip():
        issues.append("scorecard.headline is empty")
        return 0.5
    return 1.0


def _check_findings(actual: dict, issues: list[str]) -> float:
    findings = actual.get("findings") or []
    if len(findings) < 4:
        issues.append(f"only {len(findings)} findings (prompt asks for 4-12)")
    if len(findings) > 12:
        issues.append(f"{len(findings)} findings exceeds the 12 cap")

    # ID format + uniqueness.
    ids = [f.get("id") for f in findings]
    bad_ids = [i for i in ids if not (isinstance(i, str) and _FINDING_ID_RE.match(i))]
    if bad_ids:
        issues.append(f"finding ids not F-NNN: {bad_ids[:5]}")
    if len(set(ids)) != len(ids):
        issues.append("finding ids are not unique")

    # Required prose fields.
    empty = [
        f.get("id")
        for f in findings
        if not (f.get("summary") or "").strip() or not (f.get("detail") or "").strip()
    ]
    if empty:
        issues.append(f"findings with empty summary/detail: {empty[:5]}")

    # High-severity findings should anchor to an Item.
    unanchored = [
        f.get("id")
        for f in findings
        if (f.get("severity") in _HIGH_SEVERITY) and f.get("item_reference") is None
    ]
    if unanchored:
        issues.append(f"high/critical findings without item_reference: {unanchored[:5]}")

    if not findings:
        return 0.0
    # Composite: fraction of findings that pass per-finding checks.
    bad = set(bad_ids) | set(empty) | set(unanchored)
    good_count = sum(1 for f in findings if f.get("id") not in bad)
    return good_count / len(findings)


def _check_severity_distribution(actual: dict, issues: list[str]) -> float:
    findings = actual.get("findings") or []
    if not findings:
        return 0.0
    counts = Counter(f.get("severity") for f in findings)
    total = sum(counts.values())
    # If one bucket holds >85% of findings, it's pathological.
    max_share = max(counts.values()) / total
    if max_share > 0.85 and total >= 4:
        issues.append(f"severity distribution skewed: {dict(counts)}")
        return 0.5
    return 1.0


def _check_metadata(item: GoldenItem, actual: dict, issues: list[str]) -> float:
    # Brochure metadata round-tripped through the report.
    expected_crd = item.brochure_crd
    if actual.get("brochure_crd") != expected_crd:
        issues.append(f"brochure_crd mismatch: {actual.get('brochure_crd')!r} != {expected_crd!r}")
        return 0.0
    return 1.0


def score_redline(item: GoldenItem, actual: dict[str, Any]) -> ScoreResult:
    issues: list[str] = []
    scorecard_score = _check_scorecard(actual, issues)
    findings_score = _check_findings(actual, issues)
    severity_score = _check_severity_distribution(actual, issues)
    metadata_score = _check_metadata(item, actual, issues)

    # Equal-weighted composite — all four dimensions matter.
    composite = (scorecard_score + findings_score + severity_score + metadata_score) / 4

    return ScoreResult(
        item_id=item.id,
        section_type=item.section_type,
        score=composite,
        passed=composite >= PASS_THRESHOLD,
        detail={
            "scorecard_score": round(scorecard_score, 4),
            "findings_score": round(findings_score, 4),
            "severity_score": round(severity_score, 4),
            "metadata_score": round(metadata_score, 4),
            "issues": issues,
        },
    )
