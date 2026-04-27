# ADR 0005 — Extractor pattern: schema, prompt, client, audit, scoring

- **Status:** Accepted
- **Date:** 2026-04-24
- **Decider:** Robert Colling

## Context

Week 2 introduces the first LLM-backed nodes: per-Item structured
extractors (fee, disciplinary, conflicts). All three share the same
shape — feed one Item section into Claude with Instructor, get back a
Pydantic object, write to the audit table — so the cost of getting the
pattern wrong compounds. This ADR locks down the contract so adding the
next two extractors is mechanical.

Outstanding pre-decisions made today: how the audit sink works without
Postgres, whether extractors run in parallel, how the eval runner
handles fixtures that need an Anthropic key in CI.

## Decision

### 1. One ``LLMClient`` for every node.

`adv_lens.llm.client.LLMClient.extract(model, system, prompt,
response_model, trace_id, node, brochure_crd)` is the only entry point.
Instructor handles the schema-tool round-trip with Anthropic. Returns the
parsed Pydantic object. Errors become `LLMError`. Tests pass a
``LLMClient`` subclass with a swapped instructor; production wires the
real Anthropic SDK lazily.

### 2. Audit sink is a pluggable async callable.

`AuditSink = Callable[[LLMCallRecord], Awaitable[None]]`. Three
implementations:

- ``PostgresAuditSink`` — production. Writes one ``llm_calls`` row per
  call via SQLModel + `asyncio.to_thread`.
- ``MemoryAuditSink`` — tests. Collects records in a list for assertions.
- ``logging_audit_sink`` — dev fallback. Logs at INFO when Postgres is
  unreachable; the pipeline keeps running.

`make_audit_sink()` defaults to Postgres-with-logging-fallback so a
developer iterating without `docker compose up` doesn't get a wall of
exceptions, but the missed audit is loud.

**Audit failure must never lose the extraction.** `LLMClient.extract`
catches sink exceptions, logs them at ERROR, and returns the parsed
result. Per CLAUDE.md the audit is for compliance, not correctness.

### 3. Per-extractor module is small.

Each extractor owns three things in one file (`extractors/<name>.py`):
the system prompt, the response schema (often imported from
`extractors/schemas.py`), and a thin `Extractor` class that wraps an
``LLMClient``. The class exposes one async `extract(section_body, *,
trace_id, brochure_crd)` method. No retries, no fallback chains here —
those belong in the LangGraph node or the LLMClient.

### 4. Schema design favours comparable fields.

Categorical fields are `Literal[...]` enums; numeric fields are
integers in base units (USD, basis points); free-text concerns go into
`extraction_warnings` and the scorer ignores them. Optional fields are
the default; the prompt instructs the model to leave a field `None`
rather than guess.

### 5. State container is a typed ``Extractions`` model.

`ADVState.extractions: Extractions` with one field per extractor (`fee`,
`disciplinary`, `conflicts`). Strings between nodes are still a smell
(CLAUDE.md). `Extractions.merge(other)` is a field-wise overwrite that
preserves None-fields on either side.

### 6. Extractors run sequentially today; parallel-merge planned.

The current pipeline runs `segment → extract_fee → END`. Day 7+ adds
disciplinary and conflicts as parallel branches off `segment`. LangGraph's
default reducer for non-list state fields is "last write wins," which
would corrupt `Extractions` if two parallel writes land. We will
register a custom reducer (`Annotated[Extractions, merge_extractions]`)
when the second parallel branch arrives — not before, to keep this ADR
honest about what's actually wired.

### 7. Pipeline factory is graceful when no Anthropic key is set.

`build_pipeline(include_extractors=None)` defaults to "include extractors
iff `ANTHROPIC_API_KEY` is set." Unkeyed environments (a fresh `docker
compose up` without `.env` filled in) still run fetch + segment cleanly.
Tests pass an explicit bool.

### 8. Eval runner reports skipped, not failed, when LLM fixtures can't run.

LLM-backed fixtures (`fee`, `disciplinary`, `conflicts`, `redline`)
return `None` from `run_pipeline_stub` when no API key is present;
the runner counts them as skipped and the run still passes. CI without
secrets stays green. CI with `ANTHROPIC_API_KEY` set runs the full eval.

### 9. Fee scorer is field-level F1 over a flattened comparable record.

We flatten `FeeExtraction` into a set of `(field, value)` tuples (with
`(schedule, program, field, value)` for per-program fields and a tier
tuple for tier breakpoints), compute precision/recall/F1, and pass at
F1 ≥ 0.8. Free-text fields (`extraction_warnings`) are excluded. Threshold
is intentionally short of 1.0 — fee schedules are messy and we want to
catch regressions, not enforce extraction perfection.

## Consequences

- **Adding the next extractor is ~120 LOC.** One schema, one prompt,
  one node, one scorer, three fixtures. Same pattern, no new
  infrastructure.
- **Audit table fills from day one.** Every Anthropic call lands in
  Postgres with prompt, response, tokens, cost. CCO-defensible from
  the first run.
- **The eval harness is honest about what it measured.** `passed`,
  `skipped`, `total` are all distinct. No cooked numbers from
  CI-skipped fixtures.
- **A future router model swap is a one-line change.** The extractor's
  `model=...` default is read from settings; flip the env var and the
  whole extractor moves to a different Claude tier.
- **The parallel-merge reducer is a known TODO.** Capturing it here so
  Day 7 doesn't reinvent it from scratch.
