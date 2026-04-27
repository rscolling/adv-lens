from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SectionType = Literal["smoke", "segmenter", "fee", "disciplinary", "conflicts", "redline"]


class GoldenItem(BaseModel):
    """One golden-set fixture. Lives as JSON in eval/fixtures/<section_type>/."""

    id: str
    brochure_crd: str
    section_id: str
    section_type: SectionType
    # Inputs the pipeline consumes for this fixture (e.g., brochure text for
    # the segmenter; extracted section body for downstream extractor fixtures).
    # Kept separate from `expected` so ground truth isn't polluted with inputs.
    inputs: dict = Field(default_factory=dict)
    expected: dict
    source_url: str | None = None
    labeled_by: str
    labeled_at: datetime
    notes: str | None = None


class ScoreResult(BaseModel):
    item_id: str
    section_type: SectionType
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    detail: dict = Field(default_factory=dict)


class EvalReport(BaseModel):
    run_id: str
    started_at: datetime
    completed_at: datetime
    total: int
    passed: int
    skipped: int = 0
    mean_score: float
    by_section: dict[str, dict]
    results: list[ScoreResult]
    skipped_items: list[str] = Field(default_factory=list)
