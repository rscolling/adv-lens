"""Public data ingestion for ADV-Lens.

Two sources, both free and public:

- `iapd`  — per-firm Form ADV Part 2A brochure PDFs from SEC IAPD.
- `iard`  — bulk Form ADV Part 1 CSVs (AUM bands, registrations, disciplinary flags).

Keeping ingestion out of `app/` deliberately: it's a pre-pipeline concern that
runs in CI and ad-hoc scripts, not behind the FastAPI request path.
"""

from adv_lens.ingestion.iapd import IAPDClient
from adv_lens.ingestion.iard import IARDBulkLoader
from adv_lens.ingestion.models import (
    AdvPart1Row,
    BrochureFetchResult,
    BrochureRef,
    FirmSummary,
)

__all__ = [
    "AdvPart1Row",
    "BrochureFetchResult",
    "BrochureRef",
    "FirmSummary",
    "IAPDClient",
    "IARDBulkLoader",
]
