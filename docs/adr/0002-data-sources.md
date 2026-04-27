# ADR 0002 — Data sources and ingestion contract

- **Status:** Accepted
- **Date:** 2026-04-24
- **Decider:** Robert Colling
- **Supersedes / Amends:** —

## Context

ADV-Lens operates on public SEC filings. Two upstream sources feed the
pipeline and have different availability, cost, and stability profiles:

1. **SEC IAPD per-firm pages** (`adviserinfo.sec.gov`, `files.adviserinfo.sec.gov`,
   `api.adviserinfo.sec.gov`). Serves the actual Form ADV Part 2A brochure
   PDFs indexed by `BRCHR_VRSN_ID`, plus a search API for resolving a CRD to
   its current brochure list. The file-server URL is stable and publicly
   documented; the search API is not publicly documented and its JSON shape
   has drifted historically.
2. **IARD bulk Form ADV Part 1 CSVs** (`adviserinfo.sec.gov/adv`). Quarterly
   release, ~100 MB zipped, ~900 columns, ~15,000 SEC-registered RIA rows
   plus ERAs. Stable cadence but column names and codes shift between
   releases.

A third CCO-useful source — Part 2 bulk ZIPs, also at `/adv` — is deferred:
the quarterly ZIP mirrors Part 2 PDFs but does not include brochure-level
metadata we can't already obtain from Part 1 + IAPD.

Day-one decisions: how do we fetch brochures without triggering SEC rate
controls, where do we cache, and what's the failure mode when an
undocumented endpoint drifts.

## Decision

### 1. Per-firm IAPD client (`adv_lens.ingestion.iapd.IAPDClient`) does two things, nothing else.

- `fetch_brochure(ref)` — downloads a PDF by `BRCHR_VRSN_ID` from
  `files.adviserinfo.sec.gov`. Writes to
  `<data_dir>/brochures/<CRD>/<BRCHR_VRSN_ID>.pdf`. Idempotent:
  `BRCHR_VRSN_ID` is immutable per filing, so a cache hit is always correct.
- `list_current_brochures(crd)` — calls the IAPD search API, extracts current
  brochure version IDs. Marked explicitly in the module docstring as the
  fragile path; any change lands in `_parse_current_brochures` and travels no
  further than this file.

### 2. Rate limiting is local and conservative.

SEC's published soft ceiling is 10 req/s with a descriptive User-Agent.
Default `SEC_RATE_LIMIT_RPS=5.0` — we leave headroom for other SEC-traffic
tools a developer might be running on the same machine. Token-bucket
implementation is inlined; no extra library dependency for ~30 LOC.

### 3. All SEC requests carry a contact-bearing `User-Agent`.

`ADV-Lens research/0.1 (robert.colling@gmail.com)` by default. Non-optional
for portfolio defensibility — SEC has temporarily blocked IP ranges using
bare `python-httpx/...` agents.

### 4. Retry policy: 3 attempts, 1 s→2 s→4 s backoff, respect `Retry-After`.

Only on 429/5xx. 4xx other than 429 is a bug — raise immediately. No circuit
breaker at this scale; worst case the CLI run errors and the operator rerun
it.

### 5. IARD Part 1 CSV is *not* auto-downloaded.

The bulk ZIP is large and refreshes quarterly, not continuously. Making CI
download it is waste. Expected flow is a manual one-off:

```bash
# once per quarter
unzip ADV_Base_A_$(YYYYMM).zip -d data/iard/
uv run python -m adv_lens.ingestion.cli load-iard data/iard/ADV_Base_A_*.csv | \
    tee data/iard/parsed.jsonl
```

The loader streams row-by-row with validated Pydantic output. Unknown
columns are ignored; renamed columns are handled via the `COLUMN_ALIASES`
table in `iard.py` (update the aliases, not the callers).

### 6. Structured outputs at the edge, not just between LangGraph nodes.

`BrochureFetchResult`, `AdvPart1Row`, `FirmSummary`, `BrochureRef` are
Pydantic models, matching the CLAUDE.md convention of no bare strings
between layers. The audit trail, peer-retriever indexing, and golden-set
fixtures all consume these types directly.

### 7. CRD and `BRCHR_VRSN_ID` validation is strict.

Both must be non-empty numeric strings. A non-digit CRD reaching the HTTP
layer is always a bug — usually a user typing a firm name into the wrong
CLI argument.

## Consequences

- **IAPD search drift is a one-file blast radius.** When SEC reorganises
  `iacontent` again, only `_parse_current_brochures` changes. Tests use a
  pre-baked dict form so they don't re-depend on the string-encoded quirk.
- **Brochure cache is content-addressed and immutable.** Safe to delete
  wholesale, safe to mount as read-only, safe to ship in a Docker volume.
- **IARD scale is deferred, not refused.** Week 3 will push the full CSV
  into Qdrant for peer filtering; today's loader already validates the
  schema path end-to-end on small synthetic fixtures.
- **The CLI, not the FastAPI endpoint, downloads PDFs.** The HTTP endpoint
  lists brochure metadata only; bulk byte-fetching is an ops concern and
  doesn't belong behind a request/response timeout.
- **On-prem branch (Ollama fallback) needs nothing from ingestion.** All SEC
  data is already on the local disk before any LLM sees it.
