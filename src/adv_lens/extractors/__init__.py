"""Per-Item structured extractors.

Each extractor consumes one ``Section`` (the body text of a Form ADV
Part 2A Item) and returns a typed Pydantic object that captures the
comparable fields the peer-comparison and redline writer downstream need.

Week 2 lands fee extraction (Item 5) first; disciplinary (Item 9) and
conflicts (Items 10/11/12 collectively) follow.
"""

from adv_lens.extractors.conflicts import ConflictsExtractor
from adv_lens.extractors.disciplinary import DisciplinaryExtractor
from adv_lens.extractors.fee import FeeExtractor
from adv_lens.extractors.redline import RedlineWriter
from adv_lens.extractors.schemas import (
    AffiliationsItem10,
    BillingFrequency,
    BillingTiming,
    BrokeragePracticesItem12,
    CodeOfEthicsItem11,
    ConflictsExtraction,
    DisciplinaryEvent,
    DisciplinaryExtraction,
    EventResolution,
    EventType,
    Extractions,
    FeeExtraction,
    FeeSchedule,
    FeeTier,
    Finding,
    FindingCategory,
    FindingSeverity,
    InvolvedPartyType,
    PeerComparisonNote,
    PricingModel,
    RedlineReport,
    Scorecard,
    ScoreCategory,
    merge_extractions,
)

__all__ = [
    "AffiliationsItem10",
    "BillingFrequency",
    "BillingTiming",
    "BrokeragePracticesItem12",
    "CodeOfEthicsItem11",
    "ConflictsExtraction",
    "ConflictsExtractor",
    "DisciplinaryEvent",
    "DisciplinaryExtraction",
    "DisciplinaryExtractor",
    "EventResolution",
    "EventType",
    "Extractions",
    "FeeExtraction",
    "FeeExtractor",
    "FeeSchedule",
    "FeeTier",
    "Finding",
    "FindingCategory",
    "FindingSeverity",
    "InvolvedPartyType",
    "PeerComparisonNote",
    "PricingModel",
    "RedlineReport",
    "RedlineWriter",
    "ScoreCategory",
    "Scorecard",
    "merge_extractions",
]
