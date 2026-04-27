"""IARD bulk Form ADV Part 1 CSV loader.

The SEC publishes quarterly "IA" data files at ``adviserinfo.sec.gov/adv``
(CSVs zipped together with field dictionaries). The full Part 1 CSV carries
~900 columns and drifts between quarterly releases — most are irrelevant to
ADV-Lens. We pull only what the peer-comparison retriever and audit trail need
(AUM bands, state, client/employee counts, disciplinary flag, current
brochure version IDs) and tolerate unknown / renamed columns.

Operational note: we do NOT auto-download the bulk CSV inside the app. It's
~50-100 MB zipped, refreshes quarterly, and mirroring it into CI is wasteful.
The expected flow is an ops script (``python -m adv_lens.ingestion load-iard
--csv path/to/ADV_Base_A_<YYYYMM>.csv``) that runs once per quarter and writes
to Qdrant.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from adv_lens.ingestion.models import AdvPart1Row

logger = logging.getLogger(__name__)

# Column aliases — SEC's Part 1 schema (`ADV_Base_A_<YYYYMM>.csv`). Ordered
# by preference; first non-empty value wins. Update here, not in callers,
# when the schema drifts between quarterly releases.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "crd": ("1E1", "FirmCrdNb", "firm_crd_number"),
    "firm_name": ("1A", "BusinessName", "primary_business_name"),
    "filing_date": ("DateSubmitted", "Latest Filing Date", "latest_filing_date"),
    "main_office_state": ("1F1-State", "Main Office State", "main_office_state"),
    "regulated_by": ("2A", "Registration Type", "registration_type"),
    "aum_discretionary_usd": ("5F2a", "Discretionary AUM", "regulatory_assets_discretionary"),
    "aum_nondiscretionary_usd": (
        "5F2b",
        "Non-Discretionary AUM",
        "regulatory_assets_nondiscretionary",
    ),
    "total_clients": ("5C1", "Number of Clients", "total_clients"),
    "total_employees": ("5A", "Total Employees", "total_employees"),
    "has_disciplinary_history": ("11", "Any Disciplinary Info", "any_disciplinary_info"),
}


class IARDBulkLoader:
    """Iterate a bulk Form ADV Part 1 CSV, yielding validated rows.

    Doesn't load into memory — streams line by line so the 1M+ row CSVs scale.
    """

    def __init__(self, csv_path: Path, encoding: str = "utf-8") -> None:
        self.csv_path = Path(csv_path)
        if not self.csv_path.exists():
            raise FileNotFoundError(self.csv_path)
        self.encoding = encoding

    def iter_rows(self) -> Iterator[AdvPart1Row]:
        """Yield one AdvPart1Row per CSV row. Skips rows without a CRD."""
        with self.csv_path.open("r", encoding=self.encoding, newline="") as f:
            reader = csv.DictReader(f)
            for i, raw in enumerate(reader, start=2):  # 2 = first data row (1 is header)
                try:
                    row = _row_from_csv(raw)
                except ValueError as e:
                    logger.debug("iard row %d skipped: %s", i, e)
                    continue
                if row is not None:
                    yield row

    def count(self) -> int:
        return sum(1 for _ in self.iter_rows())


def _row_from_csv(raw: dict[str, Any]) -> AdvPart1Row | None:
    """Build an AdvPart1Row from a heterogeneous CSV dict.

    Returns ``None`` if the row lacks a CRD (blank header rows etc).
    """
    crd = _pick(raw, COLUMN_ALIASES["crd"])
    if not crd or not str(crd).strip().isdigit():
        return None
    firm_name = _pick(raw, COLUMN_ALIASES["firm_name"]) or "(unknown)"

    return AdvPart1Row(
        crd=str(crd).strip(),
        firm_name=str(firm_name).strip(),
        filing_date=_parse_date(_pick(raw, COLUMN_ALIASES["filing_date"])),
        main_office_state=_clean(_pick(raw, COLUMN_ALIASES["main_office_state"])),
        regulated_by=_parse_regulated_by(_pick(raw, COLUMN_ALIASES["regulated_by"])),
        aum_discretionary_usd=_parse_usd(_pick(raw, COLUMN_ALIASES["aum_discretionary_usd"])),
        aum_nondiscretionary_usd=_parse_usd(_pick(raw, COLUMN_ALIASES["aum_nondiscretionary_usd"])),
        total_clients=_parse_int(_pick(raw, COLUMN_ALIASES["total_clients"])),
        total_employees=_parse_int(_pick(raw, COLUMN_ALIASES["total_employees"])),
        has_disciplinary_history=_parse_yesno(
            _pick(raw, COLUMN_ALIASES["has_disciplinary_history"])
        ),
    )


def _pick(raw: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    for a in aliases:
        if a in raw and raw[a] not in ("", None):
            return raw[a]
    return None


def _clean(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _parse_int(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_usd(v: Any) -> int | None:
    i = _parse_int(v)
    if i is None or i < 0:
        return None
    return i


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_yesno(v: Any) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    return None


def _parse_regulated_by(v: Any) -> Literal["SEC", "State", "Both", "Unknown"]:
    if not v:
        return "Unknown"
    s = str(v).strip().lower()
    if "sec" in s and "state" in s:
        return "Both"
    if "sec" in s:
        return "SEC"
    if "state" in s:
        return "State"
    return "Unknown"
