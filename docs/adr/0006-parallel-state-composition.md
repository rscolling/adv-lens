# ADR 0006 — Parallel state composition via LangGraph reducer

- **Status:** Accepted
- **Date:** 2026-04-24
- **Decider:** Robert Colling
- **Activates:** the plan in ADR 0005 § 6.

## Context

Day 7 lands the second extractor (Disciplinary). Both `extract_fee_node`
and `extract_disciplinary_node` fan out from `segment_brochure` and run
concurrently in LangGraph. Each returns a partial state update like
`{"extractions": Extractions(fee=...)}` or `{"extractions":
Extractions(disciplinary=...)}`.

LangGraph's default reducer for non-list/non-dict state fields is
**replace** ("last write wins"). With two parallel branches both writing
to `state.extractions`, the second write would clobber the first — the
fee result lands in state, then the disciplinary result lands and the
fee field is reset to None. This ADR documents how we wire a custom
reducer to compose those parallel writes correctly.

## Decision

### 1. Annotate the field with the reducer.

`adv_lens.app.graph.state.ADVState.extractions` is declared as:

```python
extractions: Annotated[Extractions, merge_extractions] = Field(
    default_factory=Extractions
)
```

LangGraph reads the `Annotated[...]` metadata when compiling a
StateGraph backed by a Pydantic model and uses the metadata callable as
the field's reducer. Every time a node returns a partial that touches
`extractions`, the reducer is called with `(current_state.extractions,
new_partial)` and its return value becomes the new state value.

### 2. The reducer is a free function, not a method.

`merge_extractions(left, right) -> Extractions` lives in
`adv_lens.extractors.schemas` next to the model it operates on.
Equivalent to `left.merge(right)` (the existing instance method), kept
as a free function so the type annotation reads naturally. Tests cover
both forms.

### 3. Reducer semantics: right-wins on populated fields.

`merge_extractions(left, right)` takes each field on `right` if it is
not None, otherwise keeps the field on `left`. Symmetric in the sense
that two disjoint partials compose into a populated container; biased
toward `right` only when both sides set the same field (which happens
on a re-run, not on a parallel fan-out).

### 4. Nodes return only their own field.

After Day 7, every extractor node returns
`{"extractions": Extractions(<one_field>=value)}` — never a manually
pre-merged state. The reducer owns composition. This makes nodes
trivially independent; adding a third extractor (conflicts on Day 8) is
the same pattern with no changes to the existing nodes.

### 5. The reducer applies on every write, including sequential ones.

When `include_extractors=False` and only `extract_fee` runs, the
reducer is still invoked once with `left=Extractions()` (the default)
and `right=Extractions(fee=...)`. The result is the same as if no
reducer were present, so wiring the reducer is safe even before
parallel fan-out exists.

### 6. Topology change: fan-out from segmenter, fan-in at END.

The pipeline diagram is now:

```text
START → fetch_brochure → segment_brochure
                           ├─→ extract_fee ────────┐
                           └─→ extract_disciplinary ┘ → END
```

Both extractor nodes have `END` as their downstream edge — LangGraph's
standard fan-in pattern. The reducer fires twice (once per branch
arrival) and `state.extractions` ends up with both fields populated.

## Consequences

- **Day-8 conflicts extractor is mechanical.** Add the node, add the
  edge from `segment_brochure`, add the edge to `END`. No reducer
  changes; no node-signature changes; the reducer handles the
  three-way composition.
- **Idempotency on re-runs is preserved.** The reducer is right-wins,
  so re-running the fee node alone replaces the fee field while
  preserving disciplinary. Useful for partial re-runs in week-3 retry
  flows.
- **Test surface includes a topology check.** `tests/test_extractor_disciplinary.py`
  asserts the Annotated metadata is on the field
  (`__metadata__` introspection) and that `build_pipeline` exposes both
  extractor nodes. If a future refactor accidentally drops the
  reducer, the test fails loudly.
- **The reducer is a single point of failure.** A buggy
  `merge_extractions` would silently lose extractor output. Mitigated
  by direct unit tests on `merge_extractions(left, right)` against
  disjoint and overlapping inputs.
- **Pydantic state is the right substrate.** Trying to do this on a
  TypedDict state requires a separate reducer registry; the
  `Annotated` form keeps the schema and reducer co-located.
