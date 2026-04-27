# ADR 0016 — Server-rendered review UI for the HITL gate

- **Status:** Accepted (revised 2026-04-27 — scope expanded to include
  pipeline-trigger form, see § Decision item 5)
- **Date:** 2026-04-27
- **Decider:** Robert Colling
- **Amends:** ADR 0010 (HumanReviewGate). The decision endpoints defined
  there are unchanged; this ADR adds a UI surface on top of them.

## Context

ADR 0010 specified a marker-style HITL gate that emits a
``review_status="pending_review"`` plus a ``report_hash``, with the
actual reviewer decision recorded via ``POST /report/decision``.
Through Week 3 the only consumer was ``curl`` — fine for a backend
piece, but a hiring manager skimming the repo can't see the gate
working without standing up the API in their head.

CLAUDE.md gates a UI behind two prerequisites: pipeline working
end-to-end and an eval harness that scores something real. Both are
now in place (live Brown Advisory run, 17/19 / 0.921 mean F1). The UI
is therefore in-bounds and worth building because:

1. **The CCO persona doesn't read JSON.** The redline already renders
   to HTML — without a review surface a CCO has to copy hashes around
   by hand to record a decision.
2. **The demo GIF is unconvincing without a UI.** The previous
   playbook ([docs/demo-playbook.md](../demo-playbook.md)) recorded
   terminal commands and a PDF viewer — accurate but not what an RIA
   buyer would see in production.
3. **The audit story is more legible end-to-end.** The current
   ``human_reviews`` table is invisible without a query; the UI makes
   it the visible result of every decision, which is the compliance
   posture the project advertises.

The non-goals are equally important:

- **No JSON-editing in the browser.** The redline body is rendered
  read-only; a "revise_requested" decision triggers a re-run, not an
  inline edit.
- **No multi-tenant authentication.** This is a portfolio piece; the
  README states it explicitly. Auth lands when a real RIA pilots it.

(An earlier version of this ADR also carved out "no CRUD on pipeline
runs" — but operator feedback during the demo walk-through made it
clear that a "watch a brochure get analyzed end-to-end" affordance was
the missing piece for a hiring-manager demo. § Decision item 5
documents the limited form of CRUD that did land: kick-off only,
read-only thereafter, no edit/delete.)

## Decision

### 1. Server-rendered FastAPI + Jinja2 + HTMX. No SPA.

Routes mounted at ``/review`` on the existing FastAPI app:

- ``GET /review`` — list of pipeline runs with status, score, headline.
- ``GET /review/{trace_id}`` — full review page: iframe of redline +
  decision form + decision history.
- ``GET /review/{trace_id}/redline.html`` — the existing
  ``render_redline_html`` output, served unchanged for the iframe.
- ``POST /review/{trace_id}/decide`` — HTMX form post, returns the
  decisions panel as a partial.

**Why server-rendered:** the page is essentially a list, an embedded
report, and a form with three radio buttons. A React or Next.js SPA
would add a build step, hydration, and a separate deploy story for
zero user-facing benefit. Jinja2 is already a dep (the redline
renderer uses it), and HTMX is one CDN script tag.

**Why HTMX, not plain HTML form:** a plain form would full-page-reload
on every decision submit. HTMX swaps just the decisions-panel partial
in place — same backend code path, same audit semantics, but the
demo recording shows a click → instant inline update without the page
flashing. That difference is what makes the GIF worth recording.

### 2. The redline body is iframed, not re-templated.

The existing ``render_redline_html(report, *, meta=...)`` returns a
standalone HTML document used by both the email/PDF path and the UI.
Refactoring it into a body fragment + page shell would touch a
surface (and a snapshot of bytes) that's already published in the
sample bundle. Iframing keeps the renderer hands-off:

- Same bytes the email/PDF path produces.
- The UI shell owns its own chrome (nav, decision form) without
  fighting the report's print-styled CSS.
- One route per concern: ``/review/{trace_id}`` is the shell;
  ``/review/{trace_id}/redline.html`` is the report.

The cost is one extra HTTP request per page load for the iframe
contents. Fine for a single-CCO local app; if this ever needed to
scale to thousands of concurrent reviewers we'd inline the body.

### 3. Decisions go through the existing audit semantics.

The new ``POST /review/{trace_id}/decide`` endpoint validates the
same fields that ``POST /report/decision`` does (decision ∈
{approved, rejected, revise}, reviewer non-empty, rationale ≤4096
chars, report_hash 64-hex), writes the same ``HumanReview`` row, and
returns the same audit history. The UI is a presentation layer on top
of ADR 0010's contract; ADR 0010's invariants (re-posts create new
rows, hash pins to bytes, ``enable_hitl=False`` short-circuits) all
still hold.

The ``POST /report/decision`` JSON endpoint is **kept**. Stripping it
would break the existing CLI walkthrough in the demo playbook and any
external integrations that already consume it.

### 5. Pipeline kick-off via a small form, not full CRUD.

The dashboard exposes one new form: CRD (required) + brochure version
ID (optional). Submitting it does the same thing the existing
``POST /pipeline/run`` JSON endpoint does — inserts a ``PipelineRun``
row with ``status=queued``, schedules the runner via the existing
``get_scheduler`` dependency, redirects back to ``/review`` with a
flash message — but accepts form-encoded input so a browser can drive
it. The list view's row rendering already handles every status, so no
new UI is needed once the row exists.

**Why this is "limited CRUD" not full CRUD:**

- **Create only.** No edit, no delete, no cancel. A run that's failing
  shows up as ``status=failed`` with the error message in the row;
  the operator inspects, fixes the root cause (env var, CRD typo),
  and creates a new run.
- **Same audit semantics as the JSON endpoint.** The JSON
  ``POST /pipeline/run`` is unchanged; the form route ``POST
  /review/runs`` is a parallel surface that produces the same
  ``PipelineRun`` row + same ``schedule_pipeline_job`` call. Everything
  downstream — reaper, status polling, redline write — sees identical
  rows regardless of which entry point created them.
- **Honest about cost.** The form caption explicitly says ~30-90s and
  ~$1 of Anthropic spend per run, plus a yellow chip warning when
  ``ANTHROPIC_API_KEY`` is empty. The point is to make the cost visible
  so a demo viewer doesn't think pipeline runs are free.
- **Validation tighter than the JSON endpoint.** The CRD field strips
  whitespace and rejects non-numeric input *before* the row is
  created, so a stray copy-paste doesn't pollute the table with a
  guaranteed-to-fail run. The JSON endpoint trusts its caller; the
  form does not.

This shape keeps the "UI reviews; it does not run" frame mostly
intact — the UI still doesn't *do* the run (the LangGraph pipeline
does), but it can now *trigger* one. That distinction matters for a
CCO persona: triggering is operator-grade work; doing the analysis
isn't.

### 4. Demo-data seeding via a one-shot CLI, not autoloaded.

A new ``python -m adv_lens.app.web.seed`` command upserts a
``PipelineRun`` row from ``docs/examples/sample-state.json`` so a
fresh ``docker compose up`` produces a non-empty review list. It is
**not** invoked automatically on app startup — auto-seeding would
hide a real "your DB is empty" signal behind demo data.

The seed command is idempotent: re-running with the same trace_id
overwrites the row's status/result so a refreshed sample-state.json
applies cleanly.

## Consequences

- **Two new runtime deps.** ``python-multipart`` (FastAPI ``Form()``
  decoding — required for the HTMX form submit) and an explicit
  ``jinja2>=3.1`` pin (FastAPI's ``Jinja2Templates`` already imported
  it transitively, but pinning it directly makes the dep graph
  honest). HTMX itself is a CDN script — no Python package, no build
  step.
- **The README's framing changes.** "Sample output" was the rendered
  PDF; the new section leads with the UI screenshot and the GIF.
  The PDF stays as a downloadable artifact for offline review.
- **The demo playbook's recording target shifts.** The new playbook
  records the UI flow (list → click row → review report → submit
  decision → see audit history), which is materially more legible
  in 60-90 seconds than the terminal sequence was.
- **The seed CLI is the new "first run" command.** README quickstart
  goes ``docker compose up`` → ``uv run python -m
  adv_lens.app.web.seed`` → open ``localhost:8000/review``. Three
  steps; same total work as before; ends on a clickable artifact
  instead of a JSON blob.
- **No new tests outside the UI module.** All new tests live in
  ``tests/test_review_ui.py`` (25 tests covering the five routes,
  empty/missing/invalid edges, the seed CLI, the root redirect, and
  the pipeline-trigger form's validation + scheduler-call capture).
  ADR 0010's existing tests are unchanged.
