# ADR 0011 — Async pipeline worker: in-process now, real queue later

- **Status:** Accepted
- **Date:** 2026-04-25
- **Decider:** Robert Colling

## Context

A single ``POST /pipeline/run`` invocation now drives:

- 1 IAPD fetch + segmenter (~2-5 s)
- 3 parallel extractor LLM calls (Sonnet/Haiku, ~10-25 s)
- 1 hybrid peer retrieval + cross-encoder rerank (~1-3 s)
- 1 Opus redline write (~10-30 s)
- 1 HITL gate (sub-second)

End-to-end ~30-90 s on a real brochure. Holding a sync HTTP connection
that long doesn't scale, times out behind most reverse proxies, and
prevents horizontal scaling of the API tier independent of the
pipeline-running tier.

Day 13 needs an async-job pattern: POST returns 202 immediately, a
background runner does the work, GET polls.

The portfolio question is which queue. Options weighed:

- **arq** — Redis-backed, asyncio-native. Adds Redis to the infra (we
  already have Postgres + Qdrant + Langfuse). The single cleanest
  Python-async option but the new dep is friction.
- **procrastinate** — Postgres-backed asyncio queue. No new infra.
  Ships with its own queue tables + migrations.
- **Celery / dramatiq** — battle-tested, broker-agnostic. Heavyweight
  and not asyncio-native; awkward fit.
- **In-process ``asyncio.create_task``** — zero new deps. Persisted job
  state in a new ``pipeline_runs`` table. Caller UX identical to
  procrastinate from the API side. Production weakness: a process
  restart kills in-flight jobs.

## Decision

### 1. In-process ``asyncio.create_task`` for week-3 MVP.

``POST /pipeline/run``:
1. Validates body (Pydantic).
2. Inserts a ``PipelineRun`` row with ``status="queued"``.
3. Calls the injected scheduler (production: wraps
   ``asyncio.create_task(run_pipeline_job(...))``).
4. Returns ``202 Accepted`` with ``{trace_id, status, status_url}``.

``run_pipeline_job`` walks the row through ``queued → running →
(complete | failed)``. Each transition is its own short transaction
(open Session, mutate, commit, close) so the long-running pipeline
never holds a connection open.

``GET /pipeline/run/{trace_id}`` returns the row in its current state.
``200`` if found, ``404`` otherwise.

Rejected alternatives:

- arq added a Redis dep we don't otherwise need; rejected for week 3.
- procrastinate's queue tables are a real thing to maintain and would
  duplicate ``pipeline_runs`` semantics; rejected for week 3.
- "Just hold the connection" doesn't survive a real workload.

### 2. The scheduler is FastAPI-injected and overridable.

``adv_lens.app.jobs.scheduler.get_scheduler`` returns
``schedule_pipeline_job`` in production. Tests override via
``app.dependency_overrides[get_scheduler]`` to capture calls without
launching tasks — this keeps endpoint tests deterministic and the
runner tests separate from the scheduling concern.

### 3. The runner takes a session factory, not a session.

The background task outlives the FastAPI request. Passing the request's
``Session`` would use it after the request's dependency cleanup closes
it. Instead the endpoint extracts the engine from the request session
and builds a per-task ``session_factory()`` callable. The runner opens a
fresh Session per transaction.

### 4. Pipeline runner is also injectable.

``run_pipeline_job(..., pipeline_runner=None)`` defaults to lazy-loading
``adv_lens.app.graph.pipeline.run_pipeline``. Tests pass a fast canned
coroutine that returns an ``ADVState`` without touching Anthropic.

### 5. The runner never raises.

Failures land in ``row.error`` with ``status="failed"``. Even a row
that vanishes mid-flight (deleted by an operator) is logged and
returned-from cleanly. The caller polling ``GET /pipeline/run/{trace_id}``
always sees a terminal state, never a stuck "running".

### 6. Restart semantics: in-flight jobs die; reaper sweeps stuck rows.

Process restart kills any tasks currently running. The corresponding
rows stay ``status="running"`` with a stale ``started_at``. The Day-14
reaper (``adv_lens.app.jobs.reaper.reap_stuck_runs`` + the
``python -m adv_lens.app.jobs.reaper`` CLI) sweeps rows older than
``settings.pipeline_run_reap_threshold_minutes`` (default 10) and marks
them ``status="failed"`` with
``error="reaped: stuck in running for >Nmin (worker died or stalled)"``.
``--dry-run`` reports candidates without mutating; ``--verbose`` prints
each reaped trace_id. Operators wire it into cron / a k8s CronJob; the
deployment shape is intentionally not codified in-process so it matches
the operator's existing scheduling story.

This is the obvious weakness of in-process scheduling and the trigger
for moving to a real queue. When that day comes:

- Swap the body of ``schedule_pipeline_job`` to enqueue a job in
  arq/procrastinate.
- Run the worker as a separate process: ``python -m
  adv_lens.app.worker``.
- The runner code is reusable — its inputs are ``trace_id`` and
  ``session_factory``, which the worker provides.
- The endpoint contract doesn't change.

### 7. ``pipeline_runs`` and ``human_reviews`` join on different keys.

``PipelineRun.trace_id`` is the pipeline run's identity. ``HumanReview``
rows carry both ``trace_id`` and ``report_hash``; the latter pins to
the exact RedlineReport bytes (ADR 0010). A re-run with the same CRD
gets a new ``trace_id`` but produces a report whose hash may or may
not match the prior approval. Operators querying "is CRD X approved
right now" look up the latest ``HumanReview`` row by CRD and compare
its ``report_hash`` to the ``report_hash`` in the latest complete
``PipelineRun.result``.

## Consequences

- **API contract changes.** ``POST /pipeline/run`` was returning the
  full ``ADVState`` synchronously; now it returns ``202`` + a status
  URL. This is a breaking change but the project is pre-MVP with no
  external consumers — acceptable. Documented in the README endpoint
  block.
- **Process restarts lose in-flight work.** Mitigated by the Day-14
  reaper. Acceptable for the portfolio piece; production-grade swap
  to arq is one ADR away.
- **The job table grows monotonically.** Retention policy is a Day-15+
  concern (probably keep complete rows for 90 days, failed rows
  longer for debugging). Operators today can ``DELETE FROM
  pipeline_runs WHERE status = 'complete' AND created_at < ...`` if
  needed.
- **No notifications.** Polling is the only progress mechanism today.
  WebSocket / SSE push is a parking-lot item.
- **The ADVState round-trip is JSON via Pydantic.** ``state.model_dump(mode="json")``
  goes into ``PipelineRun.result``, ``ADVState.model_validate(row.result)``
  reconstructs it. The reducer-annotated ``Extractions`` field round-trips
  cleanly because Annotated metadata is type-only.
- **Tests stay deterministic.** Endpoint tests use the captured-scheduler
  pattern; runner tests use a fake pipeline coroutine. Neither requires
  waiting on real asyncio scheduling.
