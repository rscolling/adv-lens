"""IAPD client — async, rate-limited, on-disk-cached.

SEC's soft ceiling on adviserinfo.sec.gov is 10 req/s with a descriptive
User-Agent. We default to 5 req/s and back off on 429/5xx. Every brochure we
fetch is written to ``<data_dir>/brochures/<crd>/<brochure_version_id>.pdf``
and re-reads come from disk — SEC brochures are versioned, so a given
``BRCHR_VRSN_ID`` is immutable.

Two endpoints are in play:

* ``files.adviserinfo.sec.gov/IAPD/Content/Common/crd_iapd_Brochure.aspx?BRCHR_VRSN_ID=<id>``
  serves the Part 2A brochure PDF directly. The CDN does naive UA bot
  detection: descriptive User-Agent strings without ``Mozilla`` / ``Gecko``
  get a 404 even though SEC's official guidance asks for descriptive UAs.
  We use a polite-bot hybrid (``Mozilla/5.0 (compatible; ADV-Lens/...)``)
  that mirrors Googlebot's pattern — browser-shaped prefix to pass the
  filter, identification + contact preserved for SEC log-readers.
* ``api.adviserinfo.sec.gov/search/firm/<crd>`` returns the per-firm JSON
  (name, AUM, brochure list). Used to resolve CRD → brochure version IDs.
  Undocumented; we isolate it in one method and note fragility in ADR 0002.
  Note: the legacy ``/search/entity`` endpoint was retired by the SEC in
  early 2026 and now returns 403 ``MissingAuthenticationTokenException``.

A separate URL — ``reports.adviserinfo.sec.gov/reports/ADV/<crd>/PDF/<crd>.pdf``
— serves the regulatory Form ADV **Part 1A** (firm registration, AUM,
client counts), NOT the Part 2A narrative brochure. Reachable via
``settings.sec_iapd_reports_base_url`` for future use; the core pipeline
does not call it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from adv_lens.app.settings import Settings
from adv_lens.app.settings import settings as default_settings
from adv_lens.ingestion.models import BrochureFetchResult, BrochureRef

logger = logging.getLogger(__name__)

BROCHURE_PDF_PATH = "/IAPD/Content/Common/crd_iapd_Brochure.aspx"
FIRM_SEARCH_PATH = "/search/firm"
# /reports/ADV/<CRD>/PDF/<CRD>.pdf serves the regulatory Form ADV Part 1A —
# kept reachable via settings.sec_iapd_reports_base_url for future use
# (firm AUM, client counts, etc.) but not the Part 2A brochure path.


class _TokenBucket:
    """Minimal async token bucket for rate limiting.

    We don't pull in aiolimiter/asynclimiter just for this — SEC's needs are
    too simple and the extra dep noise isn't worth it.
    """

    def __init__(self, rate_per_sec: float) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate = rate_per_sec
        self._capacity = max(rate_per_sec, 1.0)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens < 1.0:
                wait_s = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait_s)
                self._tokens = 0.0
                self._last = time.monotonic()
            else:
                self._tokens -= 1.0


class IAPDClient:
    """Async client for SEC IAPD.

    Usage::

        async with IAPDClient() as client:
            result = await client.fetch_brochure(
                BrochureRef(crd="108000", brochure_version_id="123456")
            )
    """

    def __init__(
        self,
        settings: Settings = default_settings,
        transport: httpx.AsyncBaseTransport | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._data_dir = data_dir or settings.data_dir
        self._limiter = _TokenBucket(settings.sec_rate_limit_rps)
        self._client = httpx.AsyncClient(
            headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=settings.sec_request_timeout_s,
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> IAPDClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── URL builders ───────────────────────────────────────────────────
    def brochure_pdf_url(self, ref: BrochureRef) -> str:
        # Part 2A brochures are still keyed on BRCHR_VRSN_ID and served
        # from files.adviserinfo.sec.gov. Each version is immutable (see
        # ADR 0002), so the cache key on BRCHR_VRSN_ID is correct.
        return (
            f"{self._settings.sec_iapd_files_base_url}{BROCHURE_PDF_PATH}"
            f"?BRCHR_VRSN_ID={ref.brochure_version_id}"
        )

    def firm_search_url(self, crd: str) -> str:
        # The current endpoint takes the CRD as a path segment under
        # /search/firm; query-string parameters tune sort and result count.
        return (
            f"{self._settings.sec_iapd_api_base_url}{FIRM_SEARCH_PATH}/{crd}"
            f"?hl=true&nrows=1&start=0&r=25&sort=score+desc&investorTools=true"
        )

    def cache_path(self, ref: BrochureRef) -> Path:
        return self._data_dir / "brochures" / ref.crd / f"{ref.brochure_version_id}.pdf"

    # ── Core: fetch brochure PDF ───────────────────────────────────────
    async def fetch_brochure(self, ref: BrochureRef, *, force: bool = False) -> BrochureFetchResult:
        """Fetch one Part 2A brochure PDF, using the on-disk cache by default.

        Brochures are immutable per ``BRCHR_VRSN_ID``, so cache invalidation
        is never needed — a new filing gets a new ID.
        """
        path = self.cache_path(ref)
        if path.exists() and not force:
            data = path.read_bytes()
            return BrochureFetchResult(
                ref=ref,
                pdf_path=path,
                bytes_downloaded=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                fetched_at=datetime.now(UTC),
                from_cache=True,
                http_status=200,
            )

        url = self.brochure_pdf_url(ref)
        response = await self._request_with_retries("GET", url)
        data = response.content
        if not data.startswith(b"%PDF"):
            raise ValueError(
                f"IAPD response for BRCHR_VRSN_ID={ref.brochure_version_id} "
                f"is not a PDF (content-type={response.headers.get('content-type')!r})"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        return BrochureFetchResult(
            ref=ref,
            pdf_path=path,
            bytes_downloaded=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            fetched_at=datetime.now(UTC),
            from_cache=False,
            http_status=response.status_code,
        )

    # ── Firm search (undocumented; isolate fragility here) ─────────────
    async def fetch_firm_payload(self, crd: str) -> dict:
        """Return the raw JSON from IAPD search for one CRD.

        Kept as raw JSON on purpose: the IAPD search schema is undocumented
        and drifts. `list_current_brochures` does the minimal parsing we need;
        anything else deserves an explicit decision + ADR entry.
        """
        if not crd.strip().isdigit():
            raise ValueError(f"crd must be numeric, got {crd!r}")
        response = await self._request_with_retries("GET", self.firm_search_url(crd))
        return response.json()

    async def list_current_brochures(self, crd: str) -> list[BrochureRef]:
        payload = await self.fetch_firm_payload(crd)
        return _parse_current_brochures(crd, payload)

    # ── HTTP with retries + rate limit ─────────────────────────────────
    async def _request_with_retries(self, method: str, url: str) -> httpx.Response:
        attempt = 0
        backoff = 1.0
        while True:
            await self._limiter.acquire()
            response = await self._client.request(method, url)
            if response.status_code < 400:
                return response
            if (
                response.status_code in (429, 500, 502, 503, 504)
                and attempt < self._settings.sec_max_retries
            ):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                sleep_s = retry_after if retry_after is not None else backoff
                logger.warning(
                    "SEC %s %s -> %d, retry in %.1fs (attempt %d/%d)",
                    method,
                    url,
                    response.status_code,
                    sleep_s,
                    attempt + 1,
                    self._settings.sec_max_retries,
                )
                await asyncio.sleep(sleep_s)
                attempt += 1
                backoff *= 2
                continue
            response.raise_for_status()
            return response  # unreachable


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_current_brochures(crd: str, payload: dict) -> list[BrochureRef]:
    """Pull current brochure version IDs out of an IAPD search payload.

    The payload shape is roughly:
    ``{"hits": {"hits": [{"_source": {"iacontent": "<JSON-encoded firm doc>"}}]}}``
    where ``iacontent`` embeds a serialized JSON string. The relevant subtree
    has been through two shapes; we handle both:

    * **Current (2026-04+):** ``iacontent.brochures`` is a *dict* with a
      ``brochuredetails`` array, each entry carrying ``brochureVersionID``
      (capital ID) and ``brochureName`` / ``dateSubmitted``.
    * **Legacy:** ``iacontent.brochures`` was a flat array with
      ``brochureVersionId`` keys.

    Tests can pass either the embedded string form or a pre-parsed dict.
    """
    hits = payload.get("hits", {}).get("hits", [])
    if not hits:
        return []
    source = hits[0].get("_source", {})
    iacontent = source.get("iacontent")
    doc: dict
    if isinstance(iacontent, str):
        import json

        doc = json.loads(iacontent)
    elif isinstance(iacontent, dict):
        doc = iacontent
    else:
        doc = source

    brochures_raw = doc.get("brochures") or doc.get("Brochures") or []
    # Current shape: dict with brochuredetails. Legacy shape: list of dicts.
    if isinstance(brochures_raw, dict):
        brochure_entries = (
            brochures_raw.get("brochuredetails") or brochures_raw.get("brochureDetails") or []
        )
    else:
        brochure_entries = brochures_raw

    refs: list[BrochureRef] = []
    for b in brochure_entries:
        if not isinstance(b, dict):
            continue
        vid_raw = b.get("brochureVersionID") or b.get("brochureVersionId") or b.get("BRCHR_VRSN_ID")
        vid = str(vid_raw).strip() if vid_raw is not None else ""
        if not vid.isdigit():
            continue
        refs.append(
            BrochureRef(
                crd=crd,
                brochure_version_id=vid,
                brochure_name=b.get("brochureName") or b.get("name"),
                is_current=bool(b.get("isCurrent", True)),
            )
        )
    return refs
