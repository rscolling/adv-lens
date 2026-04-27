# ADR 0014 — Segmenter limits on multi-program brochures + LLM-fallback plan

- **Status:** Accepted (limit acknowledged; LLM fallback planned for Week 4)
- **Date:** 2026-04-26
- **Decider:** Robert Colling
- **Amends:** ADR 0003 (segmenter strategy) — adds a known-limitations
  section and commits to a follow-up backend, not a replacement.

## Context

ADR 0003 chose a regex-based `HeuristicSegmenter` keyed on line-anchored
``Item N`` headers, with a 40%-of-document TOC cutoff to dedupe Table-of-
Contents matches against narrative section headers. It works cleanly on
the textbook brochure structure that the SEC's General Instructions
imply.

The first live IAPD run on 2026-04-26 — Brown Advisory LLC (CRD 110181,
BRCHR_VRSN_ID 1037550) — surfaced a real-world structural variant the
heuristic does not handle. The full diagnostic is preserved as a sample
under ``docs/examples/sample-report.json``; the short version follows.

### What the regex sees in Brown Advisory's Part 2A brochure

Line-anchored ``Item N`` matches across the 211,612-char text:

| Item | Header form found | Where |
|---|---|---|
| 2  | ``ITEM 2 MATERIAL CHANGES``       | narrative — clean |
| 3  | ``ITEM 3 TABLE OF CONTENTS``      | narrative — clean |
| 4  | ``ITEM 4 ADVISORY BUSINESS``      | narrative — clean |
| 5  | (none after the TOC line)         | TOC fragment only |
| 9  | ``ITEM 9 DISCIPLINARY INFORMATION`` | narrative — clean |
| 10 | (none after the TOC line)         | TOC fragment only |
| 11 | (none after the TOC line)         | TOC fragment only |
| 12 | (none after the TOC line)         | TOC fragment only |
| 15 | ``ITEM 15 CUSTODY``               | narrative — clean |
| 16 | ``ITEM 16 INVESTMENT DISCRETION`` | narrative — clean |

Items 5/10/11/12 narrative content **exists in the brochure**, but it is
bundled into multi-program subsections that don't carry standalone
``Item N`` headers. Examples observed:

- ``Item 5 — Fees and Compensation`` appears as a body cross-reference
  (``As described in Item 5 — Fees and Compensation below``) — the
  regex matches, but the surrounding context is body text, not a
  section header.
- The actual fee narrative is split across multiple program-specific
  sections like *"Brown Advisory Investment Program — Fees"* and
  *"Brown Advisory Securities Wrap Program — Fees"*, neither of which
  begins with the ``Item 5`` literal.

### Resulting segmenter output for Brown Advisory

| Item | Body chars | Quality |
|---|---|---|
| 5  |     144 | TOC dotted-leader fragment |
| 9  |  65,613 | Bloated — extends through Items 10–14 narratives because the next ALL-CAPS header is ``ITEM 15`` |
| 10 |       0 | Empty |
| 11 |       0 | Empty |
| 12 |       0 | Empty |

### How the rest of the pipeline behaves under this input

The Fee, Disciplinary, and Conflicts extractors all receive degraded
inputs but **do not fabricate**: they emit ``extraction_warnings`` like
*"section appears to be a TOC fragment"* and *"section empty; no
extraction performed"*. The redline writer (Opus 4.7) honours those
warnings and refuses to assert exam-ready conclusions:

- Headline: *"Partial scorecard for CRD 110181: Item 9 shows no
  disciplinary disclosures, but Items 5, 10, 11, and 12 could not be
  extracted and require re-ingestion before reliance"*
- Findings F-001 / F-002 explicitly flag the un-extracted Items as
  high-severity gaps with *"Re-ingest"* recommendations.
- Overall score: 50 — the system reflects evidentiary uncertainty as a
  middling score, not as a falsely-confident high or low.

This is the right defensive behavior under uncertain input, but it is
not a substitute for actually parsing the brochure correctly.

## Decision

### 1. Acknowledge the limit explicitly. Do not paper over it.

The current `HeuristicSegmenter` is the right primary backend for the
70-80% of brochures that follow the canonical structure. It is the
wrong backend for multi-program brochures that bundle Items 5/10/11/12
into program-specific subsections without per-Item headers.

Code comments in ``adv_lens/segmenter/heuristic.py`` and the user
manual § 9.1 reference this ADR. The Brown Advisory sample at
``docs/examples/sample-report.json`` is the canonical real-world
demonstration.

### 2. Plan: LLM-backed fallback as a per-Item rescue, not a primary backend.

When the regex segmenter produces a body shorter than 2,000 characters
(or 0 chars) for any of Items 5 / 9 / 10 / 11 / 12 — the five Items
the extractors actually consume — the LangGraph segmenter node will
invoke a Haiku 4.5 prompt that takes the *full* brochure text plus the
list of canonical Item titles and returns character-offset spans for
each missing Item. The regex result for Items it correctly found stays
authoritative.

Why these design choices:

- **Haiku 4.5, not Sonnet/Opus.** The task is span identification on
  English-language disclosure text — no domain reasoning required.
  Cost: ~$0.001 per fallback invocation; only fires on the ~15-25%
  of brochures that need it.
- **Per-Item rescue, not full-brochure re-segmentation.** Preserves the
  deterministic regex result for the 70-80% of brochures that work and
  the Items that do match in the difficult cases. Avoids the failure
  mode where the LLM mis-locates a section the regex got right.
- **Triggered on <2,000 chars, not on regex success/failure.** The
  brochure-sized 0-char and 144-char Brown cases both trip a single
  threshold cleanly. 2,000 chars is below the size of every real Item
  5 / 10 / 11 / 12 narrative in our 19-fixture golden set, so true
  positives won't fire the fallback.
- **Output is character spans, not the body text itself.** Lets the
  regex segmenter's `Section` shape (``char_start``, ``char_end``,
  ``body``) stay the contract. The fallback is invisible downstream.

### 3. Explicitly out of scope here.

- **Replacing the regex segmenter wholesale with an LLM.** The regex is
  cheaper, deterministic, and CCO-defensible ("we use a regex on
  SEC-mandated section headers; here's the file"). Replacing it would
  trade defensibility for a marginal hit-rate gain on the long tail.
- **PDF rendering / layout-aware extraction.** pypdf gives us serial
  text; multi-column layouts and embedded tables come back somewhat
  reordered. Real fix is a layout-aware extractor (e.g., LlamaParse,
  unstructured.io) — deferred to a separate ADR if needed. The LLM
  fallback above hides most of this for the 5 Items the extractors
  consume.
- **Per-program sub-Item extraction.** Brown Advisory's brochure has
  *multiple* Item 5 narratives (one per program). The fallback will
  return spans for the canonical Items; per-program decomposition is a
  Week-5+ feature that depends on having a programs taxonomy first.

## Consequences

- **The portfolio claim becomes correct, not aspirational.** Today's
  claim — *"we handle non-standard brochures by surfacing the gap, not
  hiding it"* — is true and demonstrable on the Brown sample. After
  the LLM fallback lands, the claim upgrades to *"we extract Items 5,
  9, 10, 11, 12 from canonical and multi-program brochures alike;
  outliers degrade to a flagged partial scorecard."*
- **One new dependency on LLM availability for the segmenter node.**
  Today the segmenter is offline-deterministic; the fallback path
  introduces a network dependency for the difficult subset. Mitigated
  by: (a) only the difficult subset uses it, (b) the regex result is
  used directly when the fallback fails, with a logged warning, (c)
  cost is capped at one Haiku call per pipeline invocation.
- **A new failure mode to watch in the eval harness.** Once the
  fallback lands, the segmenter scorer (``score_segmenter``) gains a
  per-Item span-accuracy metric, scored against hand-labeled spans
  in a new fixture set drawn from real multi-program brochures.
- **Audit-trail unchanged.** The fallback is one additional row in
  ``llm_calls`` per triggered run, with ``node="segmenter_llm_fallback"``.
  Operators can join this against ``pipeline_runs.result`` to see which
  Items came from the regex vs the LLM.
- **Reversibility.** If the LLM fallback proves unreliable, removing
  it is a one-line revert in the segmenter node. The regex backend
  stays the primary; nothing downstream changes.
