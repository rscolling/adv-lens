# ADR 0008 — Redline output format and judging strategy

- **Status:** Accepted (output format); LLM-as-judge plan deferred to ADR 0009
- **Date:** 2026-04-25
- **Decider:** Robert Colling

## Context

The Redline Writer is the artifact a Chief Compliance Officer reads. Every
upstream choice — segmenter granularity, extractor schemas, peer corpus
shape, parallel reducer — exists to make this output defensible on exam.
This ADR locks the output format so the Week-3 HITL gate, the Week-4
LLM-as-judge eval, and any downstream consumers (HTML render, audit
export) can plan against a stable shape.

Two related decisions get separated for cleanness:

- **Format** (this ADR) — the `RedlineReport` schema, the writer's
  prompt contract, the structural validator that grades it today.
- **LLM-as-judge** (deferred to ADR 0009 in week 4) — the dual-judge
  scoring strategy, judge-drift mitigation, and the prompt-engineering
  loop on the judge.

## Decision

### 1. The output is a single typed `RedlineReport`.

Composed of:
- `Scorecard` — overall score (0-100), four named category scores
  (compliance, transparency, conflicts_handling, fee_competitiveness),
  and a one-sentence headline the CCO can paste into a memo.
- `findings: list[Finding]` — 4-12 atomic observations, each with a
  controlled-vocabulary category, a five-level severity, an optional
  Item reference (1-18), an SEC-expectation reference, an optional
  peer-comparison sentence, and a recommended next action.
- `peer_comparisons: list[PeerComparisonNote]` — populated only when
  `state.peer_context` is non-empty (week-3 retrieve_peers node).
- `extraction_warnings_seen` — echoes upstream extractor warnings so a
  reviewer can drill from finding back to source.

### 2. Severity vocabulary is fixed and policy-loaded.

The system prompt assigns concrete meaning to each level:
- `critical` ONLY for disclosed criminal events, undisclosed material
  conflicts, or unresolved active enforcement.
- `high` for disclosed regulatory sanctions, soft dollars outside
  Section 28(e), or required directed brokerage to an affiliated BD.
- `medium` for typical disclosed conflicts (12b-1, hybrid BD/RIA,
  in-28(e) soft dollars).
- `low` / `info` for benign observations and positive signals.

Locking the policy in the prompt — not the schema — keeps it editable
without a schema migration when the CCO audience tells us a category
is over- or under-weighted.

### 3. Findings are stable handles.

`Finding.id` follows `F-NNN` (zero-padded, report-scoped). Stable
within a report so the audit table's `human_reviews.report_hash` plus
a finding ID forms a citation that survives the report's lifetime.
The structural validator enforces the format and uniqueness today.

### 4. Tone is conservative; this is analyst aid, not legal advice.

The prompt explicitly hedges with "appears", "may", "warrants review"
unless the source extraction carries a regulatory finding. This pairs
with the compliance posture in `docs/compliance.md` and the disclaimer
that ships in the published report.

### 5. One Opus 4.7 call per pipeline run, after fan-in.

The writer is the join point of the three parallel extractor branches
(see ADR 0006). Total Opus spend per brochure is bounded to one call;
input includes the three structured extractions + peer hits, so the
prompt scales linearly with brochure complexity, not pipeline depth.
`max_tokens=8192` because reports can run several hundred words across
4-12 findings + scorecard rationales.

### 6. Empty-extractions short-circuit.

If all three extractor outputs are None (the upstream extractor branches
all failed), the writer returns a deterministic minimal `RedlineReport`
with `overall_score=0` and a "no usable extractor output" headline.
No LLM call is made. The audit row would be misleading without an
input to ground it.

### 7. Brochure metadata round-trips through the report.

`brochure_crd` and `brochure_version_id` are echoed into the report and
backfilled by the writer if the model omits them. This is what the
HITL gate cites and what the published report's footer carries. The
structural validator enforces the round-trip.

### 8. Today's scorer is structural-only; LLM-as-judge lands in week 4.

The brief promises LLM-as-judge with a second-judge cross-check to
catch judge drift. The structural validator
(`eval/scorers/redline.py`) enforces the shape we can grade
deterministically:
- All four scorecard categories present, non-zero.
- 4-12 findings.
- Finding IDs match `F-NNN` and are unique.
- Each finding has non-empty summary + detail.
- High/critical severity findings cite an Item reference.
- Severity distribution is not pathological (>85% in one bucket
  scores down).
- Brochure metadata round-trips.

Pass threshold 0.8. The structural validator catches regressions
(prompt drift introducing missing fields, malformed IDs, severity
collapse) without the cost or noise of running judge models on
every PR. Week-4 ADR 0009 covers the dual-judge plan, judge-drift
mitigation, and the eval-on-PR cost gate.

### 9. The HITL gate (week 3) acts on the typed `RedlineReport`.

`HumanReviewGate` will read `state.redline`, present the report to a
CCO via the FastAPI surface, and write an approve/reject/revise
decision to the `human_reviews` table. The gate doesn't mutate the
report — revisions get a new pipeline run with the same `trace_id`
chained for audit. ADR 0010 locks the HITL design when it lands.

## Consequences

- **The eval harness has a stable target.** Prompt iteration on the
  writer can run against the structural validator without touching the
  schema; schema changes (e.g., adding a field) require updating the
  validator in lockstep.
- **The HITL gate has a typed input.** The week-3 work is one
  FastAPI route + one Postgres write, not a schema design exercise.
- **The judge models in week 4 have a fixed scoring surface.** The
  dual-judge prompt grades `RedlineReport` instances, not free-form
  text — the judge can be itself structured-output via Instructor.
- **Cost upper bound is explicit.** One Opus call per pipeline run.
  At ~$15/MTok input + $75/MTok output, a typical run is well under
  $0.50 — defensible to a CCO who asks "what does this report cost
  to produce?"
- **Empty-extractions case is deterministic.** A pipeline run where
  every extractor failed produces a real `RedlineReport` (with the
  failure-headline scorecard) rather than a null. Downstream consumers
  always get a typed object.
- **Severity policy is in the prompt, not code.** Updating the
  policy is a prompt edit + golden-set rerun, not a schema migration.
  Trade-off: drift between prompt policy and validator expectations
  has to be caught by review (the validator only enforces structure,
  not the semantic policy).
