"""Pydantic models shared between IAPD + IARD loaders.

Structured output everywhere (CLAUDE.md) — even at the ingestion edge. Downstream
LangGraph nodes receive `BrochureFetchResult` + `AdvPart1Row`, not dicts.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class BrochureRef(BaseModel):
    """Pointer to one Form ADV Part 2A brochure on SEC IAPD.

    `brochure_version_id` (BRCHR_VRSN_ID) is the stable, filing-scoped identifier
    used by `files.adviserinfo.sec.gov` to serve the PDF. One CRD can have many
    versions (annual amendments + interim filings) plus >1 current brochure
    (wrap program, institutional share class, etc).
    """

    crd: str
    brochure_version_id: str
    brochure_name: str | None = None
    date_posted: date | None = None
    is_current: bool = True

    @field_validator("crd", "brochure_version_id")
    @classmethod
    def _strip_digits(cls, v: str) -> str:
        v = v.strip()
        if not v or not v.isdigit():
            raise ValueError("must be a non-empty numeric identifier")
        return v


class BrochureFetchResult(BaseModel):
    """Outcome of fetching one brochure PDF. Written to the audit trail."""

    ref: BrochureRef
    pdf_path: Path
    bytes_downloaded: int = Field(ge=0)
    sha256: str
    fetched_at: datetime
    from_cache: bool = False
    http_status: int = 200


class FirmSummary(BaseModel):
    """Minimal firm identity + current brochure list from IAPD search API.

    Not a mirror of the full IAPD JSON — we pull only the fields used
    downstream by the peer-comparison retriever and the audit trail.
    """

    crd: str
    firm_name: str
    main_office_state: str | None = None
    aum_usd: int | None = Field(default=None, ge=0)
    aum_band: str | None = None  # "<$100M" | "$100M-$1B" | "$1B-$10B" | "$10B-$100B" | ">$100B"
    primary_business_type: Literal["RIA", "ERA", "Other"] | None = None
    current_brochures: list[BrochureRef] = Field(default_factory=list)


class AdvPart1Row(BaseModel):
    """Subset of the IARD bulk Form ADV Part 1 CSV relevant to ADV-Lens.

    The SEC's bulk CSV has hundreds of columns and the schema drifts between
    quarterly releases. We parse tolerantly: unknown columns are ignored,
    missing non-key columns deserialize to None. Treat this class as the
    *stable contract* — if SEC renames a column we update the alias here, not
    in the callers.
    """

    crd: str
    firm_name: str
    filing_date: date | None = None
    main_office_state: str | None = None
    regulated_by: Literal["SEC", "State", "Both", "Unknown"] = "Unknown"
    aum_discretionary_usd: int | None = Field(default=None, ge=0)
    aum_nondiscretionary_usd: int | None = Field(default=None, ge=0)
    total_clients: int | None = Field(default=None, ge=0)
    total_employees: int | None = Field(default=None, ge=0)
    has_disciplinary_history: bool | None = None
    current_brochure_version_ids: list[str] = Field(default_factory=list)

    @property
    def aum_total_usd(self) -> int | None:
        d, n = self.aum_discretionary_usd, self.aum_nondiscretionary_usd
        if d is None and n is None:
            return None
        return (d or 0) + (n or 0)

    @property
    def aum_band(self) -> str | None:
        total = self.aum_total_usd
        if total is None:
            return None
        if total < 100_000_000:
            return "<$100M"
        if total < 1_000_000_000:
            return "$100M-$1B"
        if total < 10_000_000_000:
            return "$1B-$10B"
        if total < 100_000_000_000:
            return "$10B-$100B"
        return ">$100B"
