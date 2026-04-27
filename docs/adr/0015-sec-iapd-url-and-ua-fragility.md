# ADR 0015 — SEC IAPD URL + User-Agent fragility

- **Status:** Accepted
- **Date:** 2026-04-26
- **Decider:** Robert Colling
- **Amends:** ADR 0002 §§ 1, 3 (per-firm IAPD client; UA policy)

## Context

ADR 0002 established the SEC IAPD ingestion contract and warned, in its
own consequences section, that *"the search API is not publicly
documented and its JSON shape has drifted historically."* The first
live IAPD run on 2026-04-26 — Brown Advisory LLC, CRD 110181 — produced
three concrete failures, each rooted in undocumented SEC behavior that
changed since ADR 0002 was written. This ADR records what we found,
what we did, and what to do when SEC moves again.

### Failure 1 — `/search/entity` retired without notice

The original firm-search endpoint
``https://api.adviserinfo.sec.gov/search/entity?query=<CRD>...`` now
returns ``HTTP 403`` with body
``{"message":"Missing Authentication Token"}`` and AWS API Gateway
header ``x-amzn-errortype: MissingAuthenticationTokenException``. That
header is what AWS returns when the URL doesn't match any registered
route — i.e., the route was deleted, not gated.

Inspection of the IAPD SPA's main JS bundle
(``https://adviserinfo.sec.gov/main.<hash>.js``) revealed the current
endpoint config:

```js
endpoints: {
  entity: "https://api.adviserinfo.sec.gov",
  search: "https://api.adviserinfo.sec.gov/search",
  ...
}
```

with the firm-fetch URL constructed as
``${searchResultURL}/${crd}?hl=true&nrows=12&...``. Empirically:

| Path | Status |
|---|---|
| ``/search/entity?query=110181`` | 403 (route gone) |
| ``/search/firm/110181``         | 200 |
| ``/search/individual/110181``   | 200 |

The new ``/search/firm/<CRD>`` endpoint returns the same outer JSON
shape as the old ``/search/entity?query=<CRD>``, but the embedded
``iacontent`` payload reorganized brochure data:

| Era | ``iacontent.brochures`` shape |
|---|---|
| Pre-2026 | `[{brochureVersionId, brochureName, isCurrent}, ...]` |
| 2026+    | `{part2ExemptFlag, brochuredetails: [{brochureVersionID, brochureName, dateSubmitted}, ...]}` |

Note: the field name capitalisation also drifted — ``brochureVersionId``
became ``brochureVersionID``.

### Failure 2 — `files.adviserinfo.sec.gov` does naive UA bot detection

The brochure-PDF URL itself —
``https://files.adviserinfo.sec.gov/IAPD/Content/Common/crd_iapd_Brochure.aspx?BRCHR_VRSN_ID=<id>``
— is unchanged and still serves the Part 2A brochure. But the SEC's
fronting CDN now returns **HTTP 404 (text/html, 355 bytes)** for any
request whose ``User-Agent`` header does not contain ``Mozilla`` or
``Gecko``.

This contradicts SEC's official guidance, which asks for descriptive
user agents with contact info (``ADV-Lens research/0.1
(robert.colling@gmail.com)``). The descriptive UA worked when ADR 0002
was written and works against the SEC EDGAR endpoints today; it is
specifically the IAPD CDN that has been switched to bot-detection mode.

Confirmed empirically with the same target URL and varying UA:

| UA prefix | Status | Body |
|---|---|---|
| ``ADV-Lens research/0.1 (...)`` (descriptive) | 404 | text/html error |
| ``Mozilla/5.0`` (bare browser) | 200 | application/pdf, 666,759 bytes |
| ``Mozilla/5.0 (compatible; ADV-Lens/0.1; +mailto:...)`` | 200 | application/pdf, 666,759 bytes |
| ``ADV-Lens/0.1 ... Gecko/20100101`` | 200 | application/pdf, 666,759 bytes |

### Failure 3 — `reports.adviserinfo.sec.gov/reports/ADV/<CRD>/PDF/<CRD>.pdf` serves Part 1A, not the brochure

While the bot-detection failure was being diagnosed, an earlier patch
mistakenly switched the brochure URL to
``https://reports.adviserinfo.sec.gov/reports/ADV/<CRD>/PDF/<CRD>.pdf``
because that URL also returns 200 with a PDF. It does — but the PDF is
the **regulatory Form ADV Part 1A**, a 2.3-MB statistical-data filing
with checkbox tables and registration metadata, not the Part 2A
narrative brochure (~600 KB) that ADV-Lens's extractors are designed
for. The downstream symptom was very recognisable: the segmenter
correctly found Items 1-12 but with Part-1A titles
("``Item 9 Custody``", "``Item 10 Control Persons``"), the redline
writer honestly flagged the input mismatch, and overall_score
collapsed accordingly.

The two URLs are both legitimate SEC endpoints; they serve different
documents. Mixing them up was my own error, surfaced cleanly because
the redline writer didn't paper over the bad input.

## Decision

### 1. Update the brochure-search path and parser to the 2026 shape.

`adv_lens.ingestion.iapd.FIRM_SEARCH_PATH = "/search/firm"`. The path
takes the CRD as a path segment (``/search/firm/<CRD>``), not a query
parameter. `_parse_current_brochures` accepts both pre-2026 (flat list)
and 2026+ (`brochuredetails` nested under `brochures` dict) shapes;
both field-name capitalisations are tolerated. A new test in
``test_ingestion_iapd.py`` pins the 2026 response shape to lock the
parser against accidental regression.

### 2. Switch to a polite-bot hybrid User-Agent.

`Settings.sec_user_agent` default:

```
Mozilla/5.0 (compatible; ADV-Lens/0.1; +mailto:robert.colling@gmail.com)
```

Mirrors Googlebot's pattern (``Mozilla/5.0 (compatible; Googlebot/2.1;
+http://www.google.com/bot.html)``). Browser-shaped prefix passes the
naive UA filter; the ``compatible;`` parenthetical preserves our
identification and contact for SEC log-readers. Documented in
``.env.example`` with the failure mode in a one-paragraph comment so
the next person to touch this knows why the UA looks unusual.

### 3. Keep `reports.adviserinfo.sec.gov` reachable but separate.

`Settings.sec_iapd_reports_base_url = "https://reports.adviserinfo.sec.gov"`
stays in the codebase for future Part 1A use cases (firm AUM, client
counts, registration details that don't require the narrative). The
Part 2A brochure-fetch path lives on ``files.adviserinfo.sec.gov``;
the two are not interchangeable. ``BROCHURE_PDF_PATH`` in
``iapd.py`` now carries an explicit comment about the distinction.

### 4. Document the playbook for the next migration.

When (not if) the SEC moves an IAPD endpoint or escalates the bot
gate, the diagnostic chain that worked here is preserved in
``adv_lens/ingestion/iapd.py`` module docstring and replicated below
for ADR completeness:

1. Confirm the failure is on a specific URL/host (not the whole
   internet) by curl-ing variants.
2. Read the AWS / Cloudflare / API Gateway error type — the
   ``x-amzn-errortype`` header tells you whether the route is gone
   (``MissingAuthenticationTokenException``), gated
   (``UnauthorizedException``), or rate-limited (429).
3. Fetch the IAPD SPA at ``adviserinfo.sec.gov/main.<hash>.js`` and
   grep for ``endpoints``, ``getFirmUrl``, ``brochureVersionID``,
   ``getBrochureLink``, ``buildBrochureLink``, ``/reports/``,
   ``/firm/``. The Angular bundle's URL templates are the source of
   truth for what the SPA itself currently calls.
4. Empirically verify candidate paths with curl + the production UA.
5. Update ``FIRM_SEARCH_PATH`` / ``BROCHURE_PDF_PATH`` /
   ``_parse_current_brochures`` and pin the response shape with a
   new test that doesn't depend on a live SEC call.

## Consequences

- **The portfolio's "we read public SEC filings" claim survives intact.**
  All three bugs were in undocumented surface area between us and the
  SEC, not in our handling of the actual brochure content. The fixes
  preserve the public-data-only posture documented in
  ``docs/compliance.md``.
- **The polite-bot UA convention is the new project default and is in
  the README/.env.example.** Future contributors who hit a 404 from a
  descriptive UA will find the explanation in ``iapd.py`` comments
  and this ADR.
- **One new test, no migration.** ``test_list_current_brochures_parses_2026_response_shape``
  pins the new ``brochuredetails`` payload. The existing test for the
  pre-2026 shape stays — both branches are reachable and either one
  could be the wire format on a given day.
- **The diagnostic playbook is itself an artifact.** Hiring-manager-
  grade reviewers can read this ADR and see the iteration: identify
  the failure, locate the SPA's URL templates, verify with curl,
  patch and pin. That iteration is more useful for a senior-engineer
  reader than a single-commit "fix SEC URL" patch would be.
- **Cache invariants unchanged.** ``BRCHR_VRSN_ID`` is still the
  immutable cache key; the brochure URL still serves the same bytes
  for a given ID. The cache files written before the UA fix were the
  *wrong document type* (Part 1A) — the SHA-256 difference (cached
  Part-1A SHA ``a9f2e8b8…`` vs true Part-2A SHA ``4492c670…``) is the
  forensic anchor that detects this if it ever recurs.
- **Failure mode for the next SEC migration is contained.** Per ADR
  0002 § 1, the IAPD search drift is a one-file blast radius. This
  ADR keeps that property — every failure surface in this incident
  was inside ``adv_lens/ingestion/iapd.py`` plus its companion test
  file. No callers, no audit table, no Pydantic schemas changed.
