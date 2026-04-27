# ADR 0003 — Segmenter strategy

- **Status:** Accepted
- **Date:** 2026-04-24
- **Decider:** Robert Colling
- **Amends:** `PROJECT_BRIEF.md` (which called for `alphanome-ai/sec-parser` as the primary segmenter)

## Context

The project brief proposed `alphanome-ai/sec-parser` as the primary section
segmenter with LlamaParse as fallback. On closer read, `sec-parser` targets
EDGAR HTML filings (10-K / 10-Q / 8-K), not the PDF-native Form ADV
Part 2A brochures ADV-Lens actually ingests. Feeding an ADV PDF through
`sec-parser` would first require PDF-to-HTML conversion and then rely on
heuristics tuned for a different filing family.

Meanwhile, Part 2A has a structural advantage EDGAR filings don't: the SEC
General Instructions mandate 18 Item sections, in order, every time. Every
compliant brochure introduces each section with ``Item N`` on its own line
(formatting varies — ``ITEM 5``, ``Item 5:``, ``Item 5.``, ``Item 5 –``).
That's a regex problem, not a DOM-traversal problem.

## Decision

**Primary backend: `HeuristicSegmenter` — regex on Item 1–18 headers.**

- Deterministic, offline, dependency-light (pypdf + stdlib re).
- Handles the common formatting variants via one regex.
- Dedupes against the Table of Contents by picking the *post-TOC*
  occurrence of each Item header (TOC lives in the first ~40% of the
  document; real Item 4+ headers follow).
- Reports missing items and out-of-order headers in ``warnings`` so a
  downstream gate can route to the fallback.

**Secondary backend: `LlamaParseSegmenter` — placeholder until Week 2.**

Invoked when:
- `HeuristicSegmenter` reports >2 missing Items, or
- `extract_text_from_pdf` raises (scanned / image-only PDF).

Wired via the `Segmenter` protocol; the LangGraph segmenter node will
choose the backend per ADVState, not via a hardcoded import.

**Not used: `alphanome-ai/sec-parser`.**

Kept in `pyproject.toml` for now — Week 6 (ADV-Diff bolt-on) may use it
for true EDGAR feeds where the HTML path is native. For Part 2A it
would be worse than the heuristic on its best day.

## Consequences

- **One-file ownership of Item detection.** Drift in SEC formatting
  conventions lands in `ITEM_HEADER_RE` or `_pick_real_headers`, not in
  callers.
- **Golden-set scorer is independent of backend.** `score_segmenter`
  grades the output structure (items found), so a future LlamaParse
  backend passes the same bar as the heuristic.
- **Scanned PDFs are a visible, named failure mode.** `extract_text_from_pdf`
  raises `SegmenterError` with a routing hint; we don't silently segment
  an empty string.
- **The brief now diverges from the code.** This ADR is the audit trail
  for that divergence; `PROJECT_BRIEF.md` intentionally stays as the
  project-inception artifact and is not retro-edited.
- **Reversibility.** If a future brochure corpus defeats the regex
  approach, swapping to LlamaParse is a one-line change in the LangGraph
  node — no data migration, the `SegmentedBrochure` contract is the same.
