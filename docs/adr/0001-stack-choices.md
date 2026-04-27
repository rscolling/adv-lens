# ADR 0001 — Stack choices

- **Status:** Accepted
- **Date:** 2026-04-24
- **Decider:** Robert Colling

## Context

ADV-Lens is a portfolio piece for wealth-management AI consulting. Three readers
grade it: a hiring manager at F2 Strategy or a peer consultancy, a Chief
Compliance Officer at an RIA, and a senior engineer evaluating the code for
hire. All three weigh "is this defensible in production" more than "is this
novel." Stack choices are pre-committed in `CLAUDE.md`; this ADR captures the
reasoning so it's auditable, not buried in chat.

## Decision

| Concern | Choice | Why |
| --- | --- | --- |
| Language | **Python 3.12** | Client ecosystem (anthropic, langgraph, sec-parser, instructor, sentence-transformers) is Python-native. 3.12 for `UTC` constant, `type` keyword, and faster interpreter. |
| Package manager | **uv** | Reproducible, fast, lockfile-first. Matches Docker best practice; trivial on CI. |
| Web framework | **FastAPI** | Pydantic-first, async-native, OpenAPI for free. The `/report/{crd}` endpoint and HITL approval endpoints lean on Pydantic bodies. |
| LLM API | **Anthropic Claude** (Haiku 4.5 / Sonnet 4.6 / Opus 4.7 per node cost tier) + Ollama fallback branch | Claude's tool-use + structured-output reliability is the tightest match for Instructor. Cost-tiered node assignment is itself a compliance-signalling choice (the README tells the CCO which model drew which conclusion). Ollama branch exists for "could we run this on-prem?" answerable with code, not slides. |
| Orchestration | **LangGraph** | Required: the portfolio signal is LangGraph-as-centerpiece, not a bit-part. Section segmenter → extractors → peer retrieval → redline → HITL gate fits the state-machine model naturally. |
| Structured output | **Pydantic + Instructor** | Every node-to-node handoff is validated. Strings between nodes are a smell (CLAUDE.md). Instructor wraps the Anthropic client so retries on schema failure are implicit. |
| Vector DB | **Qdrant** (default), pgvector as alt branch | Qdrant's filter + payload model fits "filter by AUM band + strategy tag" cleanly. pgvector branch proves on-prem-via-Postgres is viable for firms that won't run another datastore. |
| Retrieval | Hybrid: **bge-small-en-v1.5 dense + BM25 sparse + RRF + cross-encoder rerank** | Dense alone misses regulatory terminology; sparse alone misses paraphrase. Hybrid + rerank is table stakes for compliance retrieval where the CCO will ask "why did this cite that section." |
| Observability | **Langfuse** (self-hosted) | Every node-trace goes into a UI a CCO can read, not just an APM dashboard an MLE can read. Self-host matters — PHI-adjacent shops won't accept a SaaS trace sink. |
| Audit storage | **Postgres via SQLModel** | Shared with Langfuse (one Postgres). Every LLM call + every HITL decision row lands here day one, not retrofit. CCO exam deliverable. |
| Testing | **pytest + pytest-asyncio** | Standard. Asyncio mode = auto for FastAPI TestClient + async tool calls. |
| Lint | **ruff** | Fast enough to run on every save; selected rule groups (E/F/I/N/UP/B/SIM/RUF) catch the usual Python drift without bike-shedding. |
| Container | **Docker Compose** | Boots Langfuse + Postgres + Qdrant + app with one command. Matches the CLAUDE.md expectation of "working code end-to-end on a sample input via `docker compose up`." |
| Eval | **Golden-set in-repo + pytest-runnable + CI artifact upload** | Golden sets live next to code, versioned, diffable in PRs. The eval harness itself is a compliance artifact — a CCO can re-run it quarterly. |
| CI | **GitHub Actions** | Three jobs: lint / test / eval. Eval uploads results as artifacts. Wiring regression-detection (block PR on F1 drop) activates in week 4 once the golden set is sized. |
| Docs | **MkDocs Material** (planned for week 5) | Low-ceremony, renders the architecture diagram + ADRs + compliance note cleanly. Not in the initial scaffold. |

## Consequences

- **Any CCO who reads the repo can trace a report to its source LLM calls, its retrieved peer context, and its human review.** That's the whole point.
- **Swapping Anthropic for another vendor requires touching one module** (`app/llm/`, arriving week 2) — the cost tiers and Instructor wiring are all centralized there.
- **The Ollama fallback and pgvector branch are commitments we either deliver or explicitly cut.** Cut-list in `PROJECT_BRIEF.md` has Ollama as first-to-go; pgvector stays aspirational unless a hiring conversation asks for it.
- **The eval harness lands day one.** No node ships without a fixture and a scorer. This is the single biggest differentiator vs the "LangChain RAG over 10-Ks" baseline on GitHub.
