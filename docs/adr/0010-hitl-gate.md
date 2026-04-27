# ADR 0010 — HumanReviewGate: marker-style HITL with audit-table decision

- **Status:** Accepted
- **Date:** 2026-04-25
- **Decider:** Robert Colling
- **Supersedes / Amends:** activates the HITL plan referenced in CLAUDE.md and ADR 0008 § 9.

## Context

CLAUDE.md mandates: *"Any node that would produce output consumed by a
human (draft memo, flag, score) routes through an explicit
HumanReviewGate node that writes to an audit table and waits."* The
RedlineReport landing in Day 10 is exactly that kind of output, so the
gate has to be wired before the pipeline can be released.

LangGraph 0.2+ supports a true interrupt-based pause via
``interrupt_before=["hitl_gate"]`` plus a checkpointer (Postgres /
SQLite). When a CCO acts, the caller resumes the graph by re-invoking
with the saved thread_id. That's the most literal reading of "writes
... and waits."

The interrupt-style design has a real cost: it requires a
checkpointer in every code path, including unit tests, and couples the
pipeline lifecycle to a persistent thread state. For Day 12 — which
is the first cut of HITL — we picked a simpler shape that delivers
the same audit posture without that coupling.

## Decision

### 1. The gate is a terminal LangGraph node, not an interrupt point.

``hitl_gate_node`` runs after ``write_redline`` and is the last node
in the pipeline before END. The pipeline always runs to completion and
returns the final ``ADVState`` (with ``state.redline`` and
``state.review_status``). No checkpointer; no thread resumption.

### 2. The gate sets ``state.review_status = "pending_review"`` and computes ``state.report_hash``.

``review_status`` is a typed Literal on ADVState
(``not_started`` / ``pending_review`` / ``approved`` / ``rejected`` /
``revise_requested``). ``report_hash`` is the SHA256 of the canonical
``RedlineReport.model_dump_json()``. Together they're the marker the
caller (UI, downstream system) uses to decide whether the report is
releasable.

### 3. The gate writes nothing to the audit table.

``human_reviews`` records **human** decisions, not the system marker.
A "pending" row would muddy the semantics — a CCO reading the audit
log should see one row per actual decision, not a system pre-flag.
The decision row gets written when the CCO acts (next item).

### 4. ``POST /report/decision`` is the human-decision write path.

Body: ``trace_id``, ``brochure_crd``, ``report_hash``, ``reviewer``,
``decision`` (``approved`` / ``rejected`` / ``revise``), ``rationale``.
Validation:

- ``report_hash`` must be 64 hex chars (the gate's SHA256 format).
- ``brochure_crd`` must be numeric.
- ``rationale`` is required, 1-4096 chars — a CCO defending an exam
  needs to explain the call.
- ``decision`` is the controlled vocabulary above.

Endpoint writes one ``HumanReview`` row and returns it (201). Pinning
the decision to ``report_hash`` means the audit row references the
exact bytes the reviewer saw — a later report regeneration with new
numbers gets a new hash and won't satisfy the same approval.

### 5. Re-posting the same decision creates a new audit row.

Idempotency is the caller's concern. The audit semantic is "the
reviewer acted twice" — recording both lets a future investigation
reconstruct the timeline. ``GET /report/decision/{trace_id}`` returns
all decisions in chronological order so the latest decision is
unambiguous.

### 6. ``settings.enable_hitl=False`` short-circuits to "approved".

Dev / batch modes that don't have a CCO in the loop (e.g., the eval
harness running against the corpus) auto-approve at the gate. The
audit table doesn't get a row — that's deliberate, since no human
decision occurred. Production keeps ``enable_hitl=True``.

### 7. Defensive default when ``state.redline`` is None.

If every upstream step failed and there's no redline to review, the
gate sets ``review_status="rejected"`` rather than
``"pending_review"``. A blank report shouldn't sit in a CCO's inbox.

## Consequences

- **No new dependencies.** The existing `HumanReview` SQLModel from
  Day-1 scaffold finally gets used. No checkpointer install, no
  pipeline lifecycle complexity.
- **Tests run with in-memory SQLite via StaticPool** — every other
  test file already uses in-memory engines, the pattern stays
  consistent.
- **Async pause-and-resume becomes a Day-13 concern, not blocked by
  the HITL design.** When the pipeline moves to a background worker,
  the worker surfaces the report-pending event via a notification (or
  the queue's result store); the decision endpoint stays exactly as
  defined here.
- **The RedlineReport is the citation, not a database ID.**
  ``report_hash`` makes the citation portable — a CCO can copy the
  hash into a memo and a future investigator can verify the report
  bytes match.
- **Re-runs after "revise" need explicit chaining.** A `revise`
  decision triggers a new pipeline run with the same operator-meaningful
  context (brochure CRD + version), but the new run gets a fresh
  ``trace_id``. Linking the two via a ``parent_trace_id`` field on
  the new run is Day 13+ work.
- **The eval harness can run without HITL noise.** Because
  ``settings.enable_hitl=False`` auto-approves, the eval pipeline
  doesn't need a fake-CCO step.
