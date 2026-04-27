"""Pydantic schemas for per-Item extractions.

Schema design notes (see ADR 0005):

- **Comparable fields are categorical or numeric.** Free-text fields are
  hard to score with exact-match F1, so we keep narrative content out of
  the structured schema and into ``notes``-style attached fields that the
  scorer ignores by default.
- **Numbers are integers in their base unit** (USD, basis points). No
  floats — easier to round-trip through JSON and match exactly in tests.
- **Optional fields are pervasive.** Brochures vary; the extractor must be
  free to leave a field ``None`` rather than hallucinating a value.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ── Fee Extractor ─────────────────────────────────────────────────────
PricingModel = Literal[
    "aum_tiered",  # tiered % of assets under management (most common)
    "aum_flat",  # single % of AUM regardless of size
    "hourly",
    "fixed",  # flat dollar amount per period
    "performance",  # performance-based fee (Item 6 territory but disclosed in Item 5)
    "retainer",
    "wrap",  # wrap fee program
    "subscription",
    "other",
]

BillingFrequency = Literal["monthly", "quarterly", "annually", "varies", "unknown"]
BillingTiming = Literal["advance", "arrears", "varies", "unknown"]


class FeeTier(BaseModel):
    """One break-point in an AUM-tiered fee schedule."""

    min_assets_usd: int | None = Field(default=None, ge=0)
    max_assets_usd: int | None = Field(default=None, ge=0)  # None = open-ended
    rate_basis_points: int | None = Field(default=None, ge=0)  # 100 bps = 1.00%
    flat_fee_usd: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _at_least_one_amount(self) -> FeeTier:
        if self.rate_basis_points is None and self.flat_fee_usd is None:
            raise ValueError("FeeTier must specify rate_basis_points or flat_fee_usd")
        return self


class FeeSchedule(BaseModel):
    """One pricing program. A firm may offer several (e.g., wrap + advisory)."""

    pricing_model: PricingModel
    program_name: str | None = None
    tiers: list[FeeTier] = Field(default_factory=list)
    hourly_rate_low_usd: int | None = Field(default=None, ge=0)
    hourly_rate_high_usd: int | None = Field(default=None, ge=0)
    minimum_annual_fee_usd: int | None = Field(default=None, ge=0)
    minimum_account_size_usd: int | None = Field(default=None, ge=0)
    billing_frequency: BillingFrequency = "unknown"
    billing_timing: BillingTiming = "unknown"
    fees_negotiable: bool | None = None


class FeeExtraction(BaseModel):
    """Structured representation of Item 5 — Fees and Compensation."""

    schedules: list[FeeSchedule] = Field(default_factory=list)
    accepts_performance_fees: bool | None = None
    other_compensation_disclosed: list[str] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)


# ── Disciplinary Extractor ─────────────────────────────────────────────
EventType = Literal[
    "criminal",
    "sec_administrative",
    "state_administrative",
    "sro",  # FINRA, NFA, exchange
    "civil_judicial",
    "other_regulatory",
    "other",
]

EventResolution = Literal[
    "settled",
    "consent_order",
    "found_in_violation",
    "convicted",
    "dismissed",
    "vacated",
    "pending",
    "unknown",
]

InvolvedPartyType = Literal["firm", "principal", "supervised_person", "affiliate", "other"]


class DisciplinaryEvent(BaseModel):
    """One disclosed disciplinary event from Item 9.

    SEC General Instructions require disclosure of legal/disciplinary events
    that are "material to a client's evaluation" of the firm or its
    management. Captured per-event so peer comparison can group by type
    (e.g., "how many peers in this AUM band have an SRO sanction in the
    last 5 years?").
    """

    event_type: EventType
    event_date: date | None = None  # YYYY-MM-DD when disclosed
    event_year: int | None = Field(default=None, ge=1900, le=2100)  # fallback
    authority: str | None = None  # "SEC", "FINRA", "State of NY", "U.S. District Court ..."
    involved_party_type: InvolvedPartyType
    involved_party_name: str | None = None
    allegation: str  # short description, may be paraphrased
    resolution: EventResolution
    sanction_monetary_usd: int | None = Field(default=None, ge=0)
    sanction_suspension_days: int | None = Field(default=None, ge=0)
    sanction_other: list[str] = Field(
        default_factory=list
    )  # ["censure", "cease and desist", "bar"]
    is_material: bool | None = None  # firm's own materiality assessment if stated


class DisciplinaryExtraction(BaseModel):
    """Structured representation of Item 9 — Disciplinary Information.

    The headline ``has_disciplinary_history`` carries the dominant signal —
    most RIAs have nothing to disclose ("Not applicable" or similar). When
    events exist, each one is captured separately for downstream analysis.
    """

    has_disciplinary_history: bool
    events: list[DisciplinaryEvent] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _events_imply_history(self) -> DisciplinaryExtraction:
        if self.events and not self.has_disciplinary_history:
            raise ValueError("DisciplinaryExtraction has events but has_disciplinary_history=False")
        return self


# ── Conflicts Extractor (Items 10/11/12) ──────────────────────────────
class AffiliationsItem10(BaseModel):
    """Item 10 — Other Financial Industry Activities and Affiliations.

    Captures the headline "is this firm captive / multi-affiliated /
    independent?" question. Per-affiliation conflict text is intentionally
    out of scope — it lives in the brochure and the redline writer can
    quote it. We're after the boolean shape for peer comparison.
    """

    affiliated_broker_dealer: bool | None = None
    affiliated_investment_adviser: bool | None = None
    affiliated_investment_company: bool | None = None
    affiliated_insurance: bool | None = None
    affiliated_bank: bool | None = None
    uses_other_investment_advisers: bool | None = None
    # Free-text affiliated-entity tags ("RIA", "broker-dealer", "RIA holding company",
    # "registered fund complex"). Keep short and dedup-friendly.
    affiliations: list[str] = Field(default_factory=list)


class CodeOfEthicsItem11(BaseModel):
    """Item 11 — Code of Ethics, Personal Trading.

    The Code of Ethics rule (Rule 204A-1) requires every RIA to maintain a
    Code, so ``has_code_of_ethics`` is almost always True; we still capture
    it because a brochure that's silent on it is itself a flag.
    """

    has_code_of_ethics: bool | None = None
    recommends_securities_with_material_interest: bool | None = None
    personal_trading_in_recommended_securities: bool | None = None
    requires_personal_trade_preclearance: bool | None = None
    requires_personal_trade_reporting: bool | None = None


class BrokeragePracticesItem12(BaseModel):
    """Item 12 — Brokerage Practices.

    Soft dollars + directed brokerage are the two big disclosed conflicts.
    Section 28(e) of the '34 Act safe-harbours research-only soft dollars;
    a firm that takes soft dollars OUTSIDE 28(e) is a louder conflict.
    """

    accepts_soft_dollars: bool | None = None
    soft_dollar_within_28e_safe_harbor: bool | None = None
    accepts_directed_brokerage: bool | None = None
    requires_directed_brokerage: bool | None = None
    # "Brokerage for client referrals" — the firm directs trades to brokers
    # who refer clients to the firm. Material conflict, often disclosed
    # alongside soft dollars.
    brokerage_for_referrals: bool | None = None
    aggregates_orders: bool | None = None


class ConflictsExtraction(BaseModel):
    """Aggregate of Items 10/11/12.

    One LLM call covers all three Items so the model can cross-reference
    (e.g., an affiliated broker-dealer disclosed in Item 10 should reflect
    in directed-brokerage practice in Item 12). The combined call also
    keeps token spend down vs three separate Sonnet calls.
    """

    item_10_affiliations: AffiliationsItem10 = Field(default_factory=AffiliationsItem10)
    item_11_code_of_ethics: CodeOfEthicsItem11 = Field(default_factory=CodeOfEthicsItem11)
    item_12_brokerage: BrokeragePracticesItem12 = Field(default_factory=BrokeragePracticesItem12)
    extraction_warnings: list[str] = Field(default_factory=list)


# ── Redline Writer (final-stage output) ──────────────────────────────
FindingSeverity = Literal["info", "low", "medium", "high", "critical"]

FindingCategory = Literal[
    "fee_structure",
    "disciplinary",
    "conflict_of_interest",
    "brokerage_practice",
    "personal_trading",
    "compliance_program",
    "disclosure_quality",
    "other",
]


class Finding(BaseModel):
    """One observation in a redline report.

    Findings are the unit a CCO defends on exam: each carries a category, a
    severity, an SEC reference (where applicable), peer-comparison context,
    and a recommended next action. ``id`` is firm-scoped (F-001..F-NNN per
    report) so audit logs can cite a stable handle.
    """

    id: str  # "F-001" pattern; one report's findings are uniquely numbered
    category: FindingCategory
    severity: FindingSeverity
    item_reference: int | None = Field(default=None, ge=1, le=18)
    summary: str  # one-sentence headline
    detail: str  # 2-4 sentence explanation, may quote brochure text
    sec_expectation_ref: str | None = None  # e.g., "Form ADV Part 2A Instructions, Item 5"
    peer_comparison: str | None = None  # plain-language peer context if available
    recommendation: str | None = None  # CCO action: review, document, escalate


class ScoreCategory(BaseModel):
    name: Literal["compliance", "transparency", "conflicts_handling", "fee_competitiveness"]
    score: int = Field(ge=0, le=100)
    rationale: str  # one paragraph


class Scorecard(BaseModel):
    """Headline scoring panel for the report."""

    overall_score: int = Field(ge=0, le=100)
    categories: list[ScoreCategory] = Field(min_length=1)
    headline: str  # 1-sentence executive summary


class PeerComparisonNote(BaseModel):
    """One peer-context observation attached to a report.

    Populated by the redline writer using ``state.peer_context``. When peer
    context is absent (no Qdrant connection, empty corpus), the report
    omits this list and notes the gap in ``Scorecard.headline``.
    """

    item_number: int = Field(ge=1, le=18)
    peer_count: int = Field(ge=0)
    median_peer_position: str | None = None  # plain-language summary
    notable_outliers: list[str] = Field(default_factory=list)


class RedlineReport(BaseModel):
    """Final output the CCO reads.

    Composes the three extractor outputs + peer hits into a defensible
    scorecard. Always carries an audit pointer (``trace_id`` if upstream
    sets it on the report) so a CCO can drill from finding to source LLM
    call. The report is the artifact the HumanReviewGate (week 3) acts on.
    """

    brochure_crd: str
    brochure_version_id: str | None = None
    scorecard: Scorecard
    findings: list[Finding] = Field(default_factory=list)
    peer_comparisons: list[PeerComparisonNote] = Field(default_factory=list)
    extraction_warnings_seen: list[str] = Field(default_factory=list)
    notes: str | None = None


# ── Top-level container on ADVState ────────────────────────────────────
class Extractions(BaseModel):
    """Typed bag of extractor outputs hung off ADVState.

    Each field is None until the corresponding extractor node fires. The
    typed container keeps `state.extractions.fee.schedules[0]` accessible
    without dict-key indirection downstream.

    Parallel-write composition: ``ADVState.extractions`` is annotated with
    ``merge_extractions`` as a LangGraph reducer, so concurrent extractor
    nodes returning ``{"extractions": Extractions(fee=...)}`` and
    ``{"extractions": Extractions(disciplinary=...)}`` compose into one
    populated ``Extractions``. See ADR 0006.
    """

    fee: FeeExtraction | None = None
    disciplinary: DisciplinaryExtraction | None = None
    conflicts: ConflictsExtraction | None = None

    def merge(self, other: Extractions) -> Extractions:
        """Field-wise merge: take ``other``'s value when set, else keep ``self``'s."""
        return Extractions(
            fee=other.fee if other.fee is not None else self.fee,
            disciplinary=other.disciplinary
            if other.disciplinary is not None
            else self.disciplinary,
            conflicts=other.conflicts if other.conflicts is not None else self.conflicts,
        )


def merge_extractions(left: Extractions, right: Extractions) -> Extractions:
    """LangGraph reducer for ``ADVState.extractions``.

    Called automatically when parallel branches both return an
    ``extractions`` partial. Symmetric: ``right`` wins on fields it
    populates, ``left`` carries the rest. Equivalent to
    ``left.merge(right)`` — kept as a free function so the type
    annotation ``Annotated[Extractions, merge_extractions]`` reads
    naturally on ADVState.
    """
    return left.merge(right)
