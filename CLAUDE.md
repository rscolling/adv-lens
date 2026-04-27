# CLAUDE.md — WealthTech Portfolio Project

This file tells Claude Code what this project is, who's building it, and the quality bar. Read it before touching code.

## What this is

A credibly-shipped open-source portfolio piece targeting AI consulting firms that serve wealth management, RIAs, and family offices (F2 Strategy, Oasis Group, and peers).

One of three planned repos:

1. **ADV-Lens** — Form ADV Part 2A intelligence + peer benchmarking
2. **MeetingLens-RIA** — Advisor meeting note evaluation harness + on-prem reference pipeline
3. **IPS-Drafter** — Investment Policy Statement LangGraph agent with compliance overlay

The active project is whichever one this repo is. The full research report (`research-report.md`) and per-project brief (`PROJECT_BRIEF.md`) are in the repo root.

## Who Robert is (so you calibrate)

Senior AI Solutions Architect at VHA (federal healthcare). Pivoting to private-sector AI consulting. Holds 11 Anthropic-issued certifications. Existing portfolio:

- **Don Quixote** — 8-container multi-agent production infra with Langfuse observability, human-in-loop gating, per-agent memory allowlists
- **Bear Creek Trail** — Unity 6 mobile game built by 7 Claude Code agents; shipped to Google Play
- **Bear Creek Cinema + claude-docs-rag** — paired RAG architecture portfolio

He ships production systems. Don't assume novice. Don't over-explain. When he asks a question, he wants the answer + the tradeoff, not a tutorial.

## The audience this code speaks to

Three readers, in priority order:

1. **A hiring manager at F2 Strategy or peer consultancy.** Technically literate, time-pressured, will skim the README and look at one or two commits. They're grading: "Does this person understand the wealth management domain? Can they ship production code? Do they think about compliance?"
2. **A Chief Compliance Officer** at an RIA. Not an MLE. Reads the compliance doc, looks at the Langfuse traces, asks "could I defend this on exam?"
3. **A senior engineer evaluating the code for hiring.** Wants to see: architecture decisions justified, tests that mean something, eval harness that scores the right thing, structured outputs with schema enforcement.

Write for those three, not for Hacker News.

## Stack (pre-committed — challenge only for a reason)

- Python 3.12
- `uv` for package management
- FastAPI
- Anthropic Claude (Opus/Sonnet/Haiku per node cost tier) + Ollama (Qwen 2.5 / Llama 3.3) for on-prem fallback
- **LangGraph as the orchestration centerpiece** — not a bit-part
- Pydantic + Instructor for all structured outputs between nodes
- Qdrant for vector retrieval (pgvector as alt branch where on-prem matters)
- Hybrid retrieval: dense (bge-small-en-v1.5) + sparse (BM25), RRF, cross-encoder rerank
- Langfuse (self-hosted via docker-compose) for observability
- pytest + pytest-asyncio for tests
- ruff for lint
- Docker Compose for local orchestration
- MkDocs Material for docs site
- GitHub Actions for CI (lint, test, eval harness)

## Quality bar — what "done" means

A portfolio piece is not done until it has all of:

1. **Working code** end-to-end on a sample input via `docker compose up`.
2. **Evaluation harness** — hand-labeled golden set checked into `eval/` with size appropriate to the project (~50-250 items), CI job runs it on every PR, results committed as markdown + JSON.
3. **Architecture doc** — `docs/architecture.md` with a diagram + ADRs for the top 5 design decisions (format: context, decision, consequences).
4. **Compliance doc** — `docs/compliance.md` covering: what regulatory posture this project takes, which SEC/FINRA rules it engages with, what the tool is NOT (not legal advice, not auto-publish, not recommendation), human-in-the-loop gates, audit trail design.
5. **Langfuse traces** — README links to at least 3 real traces showing the agent pipeline on real input.
6. **Demo** — 60-90s GIF or video embedded in the README.
7. **README** — problem statement, who it's for, architecture, how to run, eval results, known limitations, roadmap.

**If you skip the eval harness, compliance doc, or Langfuse traces, the project is incomplete.** Those three are what separate this from the 500 "LangChain RAG over 10-Ks" demos.

## Conventions

- **Structured output everywhere.** Every node-to-node handoff in LangGraph is a Pydantic model, validated via Instructor or equivalent. Strings between nodes are a smell.
- **HITL by default.** Any node that would produce output consumed by a human (draft memo, flag, score) routes through an explicit `HumanReviewGate` node that writes to an audit table and waits. Don't make it optional.
- **Audit tables are first-class.** Every LLM call writes: input, output, model ID, temperature, token counts, cost estimate, timestamp, trace ID. Do this from day one, not as a retrofit.
- **Golden sets live in-repo.** Not in a separate evaluation service. Checked into `eval/fixtures/`. Version-controlled. Diffable in PRs.
- **Secrets never in VCS.** `.env.example` with keys, `.env` gitignored. Use pydantic-settings for config loading.
- **ADRs in `docs/adr/`.** Numbered, dated, one-decision-per-file. Format: Context / Decision / Consequences.
- **Tests that matter.** Don't test Pydantic's validation or FastAPI's routing — test the agent's actual behavior on known inputs with known expected outputs.

## What to avoid

- Building a UI before the pipeline works end-to-end. This is a backend portfolio piece. A minimal CLI or FastAPI endpoint is enough. Ship a UI only after the agent and eval harness are solid.
- Adding features the README doesn't promise. If it's not in the project brief, it doesn't go in.
- Generic "LangChain RAG over documents" patterns. Every project in this set exists precisely because that pattern is saturated.
- Trading bots, alpha generation, "AI financial advisor" chatbot framing. Wrong audience. This portfolio is for consulting firms that help advisors, not replace them.
- Real client PII. Ever. Even in synthetic test data that resembles real clients. Synthesize from scratch or use public institutional data (Yale endowment reports, etc.).

## Working with Robert

- He writes short, direct messages. Respond in kind.
- When he proposes an approach, he's usually thinking out loud. Pressure-test it if you disagree; don't just execute.
- He already knows LangGraph, Claude API, Docker, Langfuse, Qdrant, Python async patterns. Skip the preamble.
- He doesn't know the wealth management domain deeply yet — flag where the project brief's domain assumptions might need calibration with a real CCO or advisor conversation.
- He won't submit an application or publish a repo without reviewing it first. Your job is to make the repo publishable, not to publish it.

## First move in a new session

Read in order:
1. This file (`CLAUDE.md`)
2. `PROJECT_BRIEF.md` (the specific project scope)
3. `research-report.md` § Key Findings + the project's section (skim the rest)
4. Existing code (if any)

Then propose a plan before writing code. Week-by-week, including the eval harness from week one, not as a retrofit.
