# ADV-Lens — Compliance note

> **Status:** Reviewed for CCO-grade defensibility, 2026-04-26.
> Companion to `docs/architecture.md` (system design) and the user
> manual at `docs/user-manual.md` (operator-facing). Read this when you
> need to defend the system to an examiner, a vendor-management team,
> or your own GC.

## 1. What this tool is

ADV-Lens is an analyst aid for Chief Compliance Officers (CCOs), M&A
diligence teams, and consulting practices reading **public SEC Form ADV
Part 2A** brochures. It produces:

- structured extractions of Items 5 (fees), 9 (disciplinary), 10/11/12
  (conflicts) into typed Pydantic objects,
- peer-comparison context drawn from a curated corpus of other public
  brochures,
- a "redline" scorecard with severity-rated findings citing specific
  Form ADV General Instructions and Advisers Act rules,
- an audit-trail row for every LLM call and every human review decision.

It is built on public SEC filings only. There is no client PII, no
private-fund holding data, no live trading interface, no investment-
recommendation surface.

## 2. What this tool is NOT

- **Not legal advice.** A finding cites a Form ADV instruction or
  Advisers Act rule and recommends a remediation; it is not legal
  counsel. Outside counsel still owns the final determination.
- **Not an auto-publisher.** No report leaves the system without a
  human review decision (§ 5).
- **Not a recommendation engine** for investment products, securities,
  or advisory services. It scores disclosure quality.
- **Not a substitute for the CCO** or for the firm's compliance
  program required by Rule 206(4)-7.
- **Not a financial-projection or performance-prediction tool.** No
  output infers prospective returns from any disclosed information.
- **Not a substitute for an exam-readiness program.** It makes parts
  of exam prep cheaper; it does not replace the firm's own controls
  testing, books-and-records review, or compliance-officer attestations.

## 3. Regulatory posture

### 3.1 Data sources

Entirely public. The SEC IAPD per-firm brochure server (`files.adviserinfo.sec.gov`,
gated behind a polite-bot user agent — see ADR 0015) and the quarterly
IARD Part 1 bulk CSVs at `adviserinfo.sec.gov/adv`. No scraping of
gated content. No client PII. No third-party data brokers. No social
media or news ingestion.

The brochure cache on disk is content-addressed
(`data/brochures/<CRD>/<BRCHR_VRSN_ID>.pdf`) and immutable per filing
version; safe to commit to encrypted storage, safe to mount read-only,
safe to delete wholesale.

### 3.2 Specific rules engaged

| Rule / Citation | How ADV-Lens engages it |
|---|---|
| **Form ADV General Instructions** (esp. Item 5/9/10/11/12 sections) | Used as the scoring rubric. Each finding cites the specific Item the gap relates to. |
| **Advisers Act § 204** + **Rule 204-2** ("books and records") | The `llm_calls`, `human_reviews`, and `pipeline_runs` tables together form the system's record. Retention default is at least 5 years for `llm_calls` and `human_reviews` (the rule's general retention floor). |
| **Advisers Act § 206 (anti-fraud)** + **Rule 206(4)-7** ("Compliance Programs") | ADV-Lens does not itself provide a compliance program. It produces an analyst aid that a firm's compliance program may consume. |
| **Rule 206(4)-1** (Marketing Rule, 2022 amendments) | ADV-Lens outputs are *not themselves* marketing communications. The redline writer's prompt deliberately avoids performance-promise language so that quoted outputs cannot become Marketing Rule problems. |
| **Rule 204A-1** (Code of Ethics) | Findings about an adviser's Code of Ethics disclosure cite this rule. ADV-Lens itself is not a registrant subject to it. |
| **FINRA Regulatory Notice 24-09** (GenAI supervisory expectations) | Applied as follows: model IDs, intended temperature, prompts, outputs, and reviewer decisions are logged per call (`llm_calls`); supervisory review is enforced by the `HumanReviewGate` node (§ 5); recordkeeping mirrors books-and-records expectations. |
| **FINRA 2026 Annual Regulatory Oversight Report** (GenAI section) | "Supervision and recordkeeping for GenAI outputs" is treated as non-negotiable; HITL gate + audit trail are designed to satisfy this. |
| **SR 11-7 / OCC 2011-12** (model risk management) | A firm using ADV-Lens treats it as a third-party model under its own MRM framework. The `docs/adr/` directory + this compliance note + the `eval/` golden-set results are intended to be the artifacts that framework consumes. |

### 3.3 What we deliberately do *not* do

- **No prediction of regulatory outcomes.** A "high" severity finding
  is calibrated to *what an examiner would request a written
  explanation for*, not *the firm will be sanctioned*. Outcome
  prediction is outside the system's scope and would conflict with
  Marketing Rule risks.
- **No client-segment recommendations.** The system does not opine on
  whether a given brochure is suitable for any client.
- **No fee benchmarking that crosses the line into competitive intel.**
  Peer comparison is a defensive aid (am I disclosing what my peers
  disclose?), not a pricing-strategy tool.

## 4. Vendor and sub-processor disclosure

A firm using ADV-Lens needs this for its own vendor-management review.

| Component | Vendor | Data flow |
|---|---|---|
| Brochure ingestion | SEC (public) | Read-only HTTPS fetch; identifiable User-Agent. |
| Section segmentation | Local (regex) | Offline; deterministic; no network. |
| Section segmentation rescue | Anthropic (Claude Haiku 4.5) | When triggered (~15-25% of brochures per ADR 0014), the full brochure text is sent to Anthropic for span identification. |
| Fee/Disciplinary/Conflicts extractors | Anthropic (Sonnet 4.6 / Haiku 4.5) | The section text is sent to Anthropic via the official SDK. Outputs are typed Pydantic objects. |
| Redline writer | Anthropic (Opus 4.7) | The four extractor outputs + peer context are sent to Anthropic. Output is the `RedlineReport`. |
| Embeddings (peer corpus + retrieval) | Local (`bge-small-en-v1.5`) | CPU inference; no external call. |
| Cross-encoder rerank | Local (`ms-marco-MiniLM-L-6-v2`) | CPU inference; no external call. |
| Vector store | Self-hosted Qdrant | Local Docker container in default deployment. |
| Audit storage | Self-hosted Postgres | Local Docker container in default deployment. |
| Observability | Self-hosted Langfuse | Optional; local Docker container; only emits when API keys are configured. |

**Anthropic's Zero Data Retention (ZDR) program** is available for
accounts that need to assert no LLM-provider retention of brochure
content. ADV-Lens does not assume ZDR; firms that need it should
enable it on their Anthropic account before any production run.

**On-prem variant** (ADR 0012, pending) replaces the Anthropic calls
with Ollama-served Qwen 2.5 / Llama 3.3 for firms that cannot send any
brochure text off-premises.

## 5. Human-in-the-loop gate

### 5.1 Where the gate sits

The terminal node of every pipeline invocation is `hitl_gate`. It is
"marker-style" (ADR 0010), not a true LangGraph `interrupt_before`. It:

1. computes a SHA-256 `report_hash` over the canonical JSON of the
   `RedlineReport`,
2. sets `state.review_status = "pending_review"`,
3. returns control without writing any decision row of its own.

A CCO acts via the reviewer UI at `/review/{trace_id}` (server-rendered
form; ADR 0016) or via `POST /report/decision` directly. Both paths
write one row to the `human_reviews` table with `decision`, `reviewer`,
`rationale`, the exact `report_hash` the CCO read, and a UTC timestamp.
Decision options are `approved`, `rejected`, `revise_requested`.
Re-posts deliberately create new rows (the audit semantic is "the
reviewer acted twice"), not updates. The UI is a presentation layer
on top of the same write path; it does not maintain a separate audit
log or apply any transformation to the row before commit.

### 5.2 Why a hash

A re-run of the pipeline against the same CRD produces a new
`trace_id` and may produce a `RedlineReport` with different bytes (LLM
non-determinism, brochure version change, peer-corpus update). The
hash pins the approval to the *exact* bytes the CCO saw. Operators
asking "is CRD X approved right now" must look up the latest
`human_reviews` row by CRD and compare its `report_hash` to the
`report_hash` of the latest complete `pipeline_runs.result`. A match
means the approval still stands; a mismatch means the report has
moved and the firm needs a fresh review.

### 5.3 Disabling HITL

`ENABLE_HITL=false` makes the gate auto-approve — intended for batch
backfills and integration tests, not production. The user manual § 7.5
flags this as a configuration to inspect during audit prep.

### 5.4 Pre-file self-review (draft-upload path)

The reviewer UI accepts uploaded draft brochures (PDFs that have not
yet been filed with the SEC) via `POST /review/runs/upload`. The
upload bytes are written to the same on-disk cache the IAPD fetcher
populates, keyed on a synthetic numeric CRD with a `99` prefix. From
the audit trail's perspective these runs are indistinguishable from
filed runs — same `pipeline_runs` row shape, same `llm_calls` rows,
same HITL gate, same `human_reviews` row pinned to `report_hash`. The
synthetic-CRD prefix makes drafts trivially distinguishable in
queries when an examiner asks (e.g. *"show me only filed-brochure
reviews from the last quarter"*: `WHERE brochure_crd NOT LIKE '99%'`).
Bytes never leave the local machine until and unless the operator
explicitly chooses to share them.

## 6. Audit trail design

Three Postgres tables together form the system of record:

### 6.1 `llm_calls` — one row per LLM invocation

Columns: `trace_id`, `node`, `brochure_crd`, `model`, `temperature`,
`prompt` (JSON: system + user), `response` (JSON: parsed Pydantic
dump), `prompt_tokens`, `completion_tokens`, `cost_usd`, `created_at`
(UTC). Recorded for every Sonnet, Haiku, and Opus call — extractor,
segmenter rescue, and redline writer alike. Retention: indefinite by
default; firms should configure a retention policy keyed on Rule 204-2
expectations.

### 6.2 `human_reviews` — one row per CCO decision

Columns: `id`, `trace_id`, `report_hash` (64-char hex), `brochure_crd`,
`decision`, `reviewer`, `rationale` (1-4096 chars), `created_at` (UTC).
Re-posts append; nothing is mutated in place. Retention: indefinite.

### 6.3 `pipeline_runs` — one row per pipeline invocation

Columns: `trace_id` (unique-indexed), `brochure_crd`,
`brochure_version_id`, `status` (`queued | running | complete |
failed`), `result` (JSON: full final `ADVState`), `error`,
`created_at`, `started_at`, `completed_at`. The Day-14 reaper sweeps
rows stuck in `running` longer than `PIPELINE_RUN_REAP_THRESHOLD_MINUTES`
(default 10). Retention recommendation: keep `complete` rows for 90
days, `failed` rows longer for debugging.

### 6.4 The audit-trail bundle

A planned `GET /report/audit/{trace_id}` endpoint will return a single
ZIP containing the cached input PDF, the per-Item extraction JSONs,
the `RedlineReport`, all `llm_calls` rows for the trace, and all
`human_reviews` rows for the report hash. Until that lands (Week 5 per
the user manual), operators can assemble the bundle by joining on
`trace_id` and `report_hash` directly.

### 6.5 Langfuse (optional second layer)

When `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` are configured,
each LLM call also emits a Langfuse generation span keyed on the same
`trace_id` (deterministic via `Langfuse.create_trace_id(seed=trace_id)`).
The Postgres tables remain the system-of-record; Langfuse is the
dashboard. See user manual § 8.1.1 for setup.

## 7. Failure-mode acknowledgement

ADV-Lens is honest about its limits. The following classes of failure
are documented in ADRs and surfaced as findings (not hidden) when they
occur in a real run:

- **Multi-program / bundled-Items brochures** (ADR 0014). Some real-
  world Part 2A brochures bundle Items 5/10/11/12 narratives into
  per-program subsections without standalone `Item N` headers. The
  regex segmenter cannot isolate them; an LLM (Haiku 4.5) fallback
  rescues the spans. When even the fallback cannot find substantive
  content, the report flags the gap as a high-severity finding rather
  than fabricating one. The Brown Advisory live sample
  (`docs/examples/sample-report.json`) demonstrates this end-to-end.
- **SEC IAPD URL/UA fragility** (ADR 0015). The SEC has changed
  endpoint paths and added bot detection on the file CDN since the
  project began. The diagnostic playbook for the next migration is
  preserved in the `iapd.py` module docstring. When the SEC moves
  again, only `adv_lens.ingestion.iapd` and its companion test file
  are affected.
- **Peer corpus availability.** When Qdrant is unreachable or the peer
  corpus is empty, `retrieve_peers` returns empty `peer_context` and
  the redline writer notes the absence of peer benchmarking in the
  scorecard headline. The report is still produced; its peer-relative
  category scores are flagged as absolute (not relative) in their
  rationales.
- **LLM non-determinism.** Anthropic deprecated `temperature` on the
  claude-4 model family, so single-shot eval F1 has run-to-run noise
  of up to ~0.15 on individual fixtures. The eval harness will move to
  N=3 multi-run averaging in Week 4 (see user manual § 9.1). Until
  then, a single fixture's score should not be over-weighted.

## 8. Synthetic data and test corpora

All golden-set fixtures (`eval/fixtures/`) are synthetic. None contain
real adviser names, CRDs, or client identifiers. The `realism-style`
fixtures (Day 14b) use structural patterns from real ADV brochures
(multi-paragraph SEC settlements, dually-registered IAR shops, bank-
owned trust company affiliations) but with anonymous fictional firm
names. This is deliberate: bundling identifiable real-firm prose has a
singling-out concern that anonymous fixtures avoid.

The first live IAPD run sample (`docs/examples/sample-report.json`)
uses a real public SEC filing — Brown Advisory LLC, CRD 110181 —
because it is a public document and the system is designed to consume
public documents. No synthetic-vs-real mixing happens at evaluation
time.

## 9. What to do if your firm is examined

1. **Point the examiner at this file** as the regulatory-posture
   summary, then at `docs/architecture.md` for system design.
2. **For any specific report under examination,** retrieve the
   `pipeline_runs` row by `trace_id`, the `human_reviews` rows by
   `report_hash`, and every `llm_calls` row by `trace_id`. Together
   they reconstruct the full provenance: what was extracted, by which
   model, at what cost, with what reviewer sign-off.
3. **Show the eval harness.** `eval/results/<run_id>/report.md` has
   per-section F1 against a versioned golden set. Demonstrates that
   the system is measured, not just deployed.
4. **Show the ADRs.** `docs/adr/` documents every non-trivial design
   decision with Context / Decision / Consequences. Particularly
   relevant under examination: ADRs 0008 (redline output), 0010 (HITL
   gate), 0014 (segmenter limits), 0015 (SEC URL/UA fragility).
5. **Disable HITL only with documentation.** If `ENABLE_HITL=false`
   was set during the run under examination, be prepared to explain
   why (typically: backfill of historical brochures already approved
   under prior process).

## 10. Things this document explicitly does not promise

- That ADV-Lens findings are *correct* — they are model-generated and
  require human review.
- That the system catches every disclosure gap. The CCO judgment
  remains primary.
- That a high-severity finding will be sanctioned, or that a low-
  severity finding will not.
- That the audit trail is itself sufficient for every regulator's
  preferred record format. Firms with specific examiner expectations
  may need to augment the export shape.

## Revision history

- **2026-04-24** — Placeholder created.
- **2026-04-26** — First CCO-grade pass after live IAPD run on Brown
  Advisory established the system end-to-end. Sections 4 (vendor
  disclosure), 7 (failure-mode acknowledgement), and 9 (exam
  playbook) added.
