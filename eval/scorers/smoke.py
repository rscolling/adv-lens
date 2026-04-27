from eval.schemas import GoldenItem, ScoreResult


def score_smoke(item: GoldenItem, actual: dict) -> ScoreResult:
    """Trivial exact-match scorer so CI has something green to run day 1.

    Real section-type scorers (fee F1, disciplinary exact-match, conflicts set-overlap,
    redline LLM-as-judge + cross-judge) land starting week 2.
    """
    passed = actual == item.expected
    return ScoreResult(
        item_id=item.id,
        section_type=item.section_type,
        score=1.0 if passed else 0.0,
        passed=passed,
        detail={"actual": actual, "expected": item.expected},
    )
