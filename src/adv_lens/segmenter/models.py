"""Pydantic models for Part 2A segmentation output."""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field, field_validator

# Canonical Item titles from SEC Form ADV Part 2A General Instructions.
# Every compliant brochure covers all 18, in order, though firms with
# nothing to say in a given Item may write "Not applicable."
ITEM_TITLES: dict[int, str] = {
    1: "Cover Page",
    2: "Material Changes",
    3: "Table of Contents",
    4: "Advisory Business",
    5: "Fees and Compensation",
    6: "Performance-Based Fees and Side-By-Side Management",
    7: "Types of Clients",
    8: "Methods of Analysis, Investment Strategies and Risk of Loss",
    9: "Disciplinary Information",
    10: "Other Financial Industry Activities and Affiliations",
    11: "Code of Ethics, Participation or Interest in Client Transactions and Personal Trading",
    12: "Brokerage Practices",
    13: "Review of Accounts",
    14: "Client Referrals and Other Compensation",
    15: "Custody",
    16: "Investment Discretion",
    17: "Voting Client Securities",
    18: "Financial Information",
}


class ItemNumber(IntEnum):
    """Part 2A Item numbers as mandated by SEC General Instructions."""

    COVER_PAGE = 1
    MATERIAL_CHANGES = 2
    TABLE_OF_CONTENTS = 3
    ADVISORY_BUSINESS = 4
    FEES_AND_COMPENSATION = 5
    PERFORMANCE_FEES = 6
    TYPES_OF_CLIENTS = 7
    METHODS_OF_ANALYSIS = 8
    DISCIPLINARY_INFORMATION = 9
    OTHER_ACTIVITIES = 10
    CODE_OF_ETHICS = 11
    BROKERAGE_PRACTICES = 12
    REVIEW_OF_ACCOUNTS = 13
    CLIENT_REFERRALS = 14
    CUSTODY = 15
    INVESTMENT_DISCRETION = 16
    VOTING_CLIENT_SECURITIES = 17
    FINANCIAL_INFORMATION = 18


class Section(BaseModel):
    """One Item-level section of a Part 2A brochure.

    ``body`` is the concatenated text between this Item's header and the next
    Item's header, with trailing whitespace stripped. No further cleanup —
    extractor nodes downstream decide what noise (headers, footers, page
    numbers) they want to filter.
    """

    item_number: ItemNumber
    title: str
    body: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)

    @field_validator("body")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def length_chars(self) -> int:
        return len(self.body)

    @property
    def is_placeholder(self) -> bool:
        """True if the section reads 'Not applicable' or similar and has no body.

        Part 2A instructions permit this for Items that don't apply
        (e.g. Item 6 for firms with no performance-based fees).
        """
        lowered = self.body.lower()
        return len(self.body) < 60 and any(
            marker in lowered
            for marker in ("not applicable", "n/a", "does not apply", "no response")
        )


class SegmentedBrochure(BaseModel):
    """Full Item-level segmentation of a single Part 2A brochure."""

    source: str  # pdf path or URL — provenance, not a strict URL
    total_chars: int = Field(ge=0)
    sections: list[Section]
    # Items that were expected (1-18) but not detected by the segmenter.
    missing_items: list[ItemNumber] = Field(default_factory=list)
    # Free-form warnings from the segmenter (out-of-order headers etc).
    warnings: list[str] = Field(default_factory=list)
    backend: str = "heuristic"

    @property
    def items_found(self) -> list[ItemNumber]:
        return [s.item_number for s in self.sections]

    def section(self, item: ItemNumber) -> Section | None:
        for s in self.sections:
            if s.item_number == item:
                return s
        return None
