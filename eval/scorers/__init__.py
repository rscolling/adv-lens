from collections.abc import Callable

from eval.schemas import GoldenItem, ScoreResult
from eval.scorers.conflicts import score_conflicts
from eval.scorers.disciplinary import score_disciplinary
from eval.scorers.fee import score_fee
from eval.scorers.redline import score_redline
from eval.scorers.segmenter import score_segmenter
from eval.scorers.smoke import score_smoke

Scorer = Callable[[GoldenItem, dict], ScoreResult]

REGISTRY: dict[str, Scorer] = {
    "smoke": score_smoke,
    "segmenter": score_segmenter,
    "fee": score_fee,
    "disciplinary": score_disciplinary,
    "conflicts": score_conflicts,
    "redline": score_redline,
}


def get_scorer(section_type: str) -> Scorer:
    if section_type not in REGISTRY:
        raise KeyError(
            f"No scorer registered for section_type={section_type!r}. Known: {sorted(REGISTRY)}"
        )
    return REGISTRY[section_type]
