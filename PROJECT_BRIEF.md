# Project 1: ADV-Lens

**Status:** Build first.
**Effort:** 3 weeks MVP → 5–6 weeks polished portfolio piece.

---

## One-line pitch

A LangGraph agent that ingests any RIA's Form ADV Part 2A brochure and produces a compliance-and-competitive scorecard: fee-structure benchmarking vs peer advisers, disciplinary disclosure flags, conflict-of-interest enumeration, and a redline against SEC plain-English expectations.

---

## The problem

An RIA's Chief Compliance Officer doing an annual ADV review spends ~40 hours a year manually reading comparable brochures. An M&A diligence team evaluating an RIA roll-up target does the same work on every target. Both are paralegal-grade reads that should be machine-assisted.

Neither group wants a chatbot. They want a structured scorecard they can defend.

## Who wakes up wanting this

- RIA CCOs doing annual ADV review
- M&A diligence teams at RIA aggregators (Focus, Dynasty, Hightower, Creative Planning)
- OCTO consulting teams at F2 Strategy advising on acquisitions

## Why this matters for the portfolio

F2 Strategy does M&A tech due diligence for RIAs as a named practice area. This project is a plausible consulting deliverable, not a toy.

---

## Architecture sketch

### Data sources (all public)

- **SEC IAPD** (adviserinfo.sec.gov) — Form ADV brochures, per firm, PDF
- **IARD bulk ADV Part 1 CSVs** — Structured filing data (AUM bands, registrations, disciplinary flags)
- **SEC plain-English expectations** — Form ADV Part 2A General Instructions (the reference document the brochure is graded against)

### Pipeline (LangGraph state machine)

```
┌──────────────────┐
│  Ingestion       │  SEC IAPD fetch + bulk CSV
└────────┬─────────┘
         ▼
┌──────────────────┐
│  Section Segmenter│  alphanome-ai/sec-parser + LlamaParse fallback
└────────┬─────────┘
         ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Fee Extractor   │   │ Disciplinary     │   │ Conflicts        │
│ (Pydantic)       │   │ Classifier       │   │ Enumerator       │
└────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
         ▼                       ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Peer Comparison Retriever (Qdrant, AUM-band + strategy filter) │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
                   ┌──────────────────┐
                   │ Redline Report    │  plain-English scoring
                   │ Writer            │  vs SEC expectations
                   └────────┬──────────┘
                            ▼
                   ┌──────────────────┐
                   │ HITL Review Gate │  CCO sign-off required
                   └──────────────────┘
```

### Models per node

- Section Segmenter: Claude Haiku 4.5 (fast, cheap, structure-aware)
- Fee Extractor: Claude Sonnet 4.6 (Pydantic-validated output)
- Disciplinary Classifier: Claude Haiku 4.5 (clear-cut category labels)
- Conflicts Enumerator: Claude Sonnet 4.6 (nuance required)
- Peer Retriever: Qdrant with bge-small-en-v1.5 embeddings, filter by AUM band + strategy tags from ADV Part 1
- Redline Writer: Claude Opus 4.7 (this is the output a human reads)

### Evaluation harness

- **Golden set:** 60 ADV sections hand-labeled across AUM bands ($100M to $20B+) with correct:
  - Fee structures (extracted as structured objects)
  - Disciplinary classifications
  - Conflicts enumerated
- **Scoring:**
  - Structured field extraction → exact-match F1
  - Narrative redline → LLM-as-judge, with a **second judge model** cross-checking the first to catch judge drift
- **Langfuse traces:** every pipeline run, full node-by-node trace
- **CI:** runs golden set on every PR; fails on precision/recall regression

### Deployment

- FastAPI + Docker Compose
- VPC-deployable
- Ollama fallback branch for pure on-prem inference (demonstrate you thought about it)

---

## What skill this demonstrates

- Real regulatory literacy (not just "I read a 10-K")
- Structured extraction discipline at production level
- Peer-benchmarking retrieval patterns
- Evaluation harness scoped to a compliance audience
- "I can ship to a CCO" signal

---

## Compliance / risk posture

- **All data is public** — SEC-filed, free to download.
- **Output is positioned as analyst aid, not legal advice.** Explicit disclaimer in every report.
- **No auto-redaction, no auto-publish.** CCO sign-off gate before any report leaves the system.
- **Audit table** captures every LLM call, every retrieval, every human decision.
- Matches Reg Notice 24-09 (technology-neutral rules apply to GenAI) and Marketing Rule 206(4)-1 expectations for supervision.

---

## Differentiation

GitHub has **EDGAR 10-K RAG projects in abundance**. As of April 2026, there is **no credible public Form ADV Part 2 analytics project** with structured extraction + peer benchmarking + compliance redline. This is the niche.

---

## Suggested week-by-week

### Week 1 — Foundations
- Repo scaffold (`uv init`, FastAPI skeleton, Docker Compose, Langfuse container)
- SEC IAPD fetcher + IARD bulk CSV loader
- Section segmenter node using alphanome-ai/sec-parser
- First 10 ADV brochures in Qdrant
- Empty eval harness scaffold (pytest, fixtures dir, CI job)

### Week 2 — Extraction
- Fee Extractor node with Pydantic schema
- Disciplinary Classifier node
- Conflicts Enumerator node
- Hand-label 20 golden-set sections (down payment on the 60 target)
- First end-to-end pipeline run on a single brochure

### Week 3 — Benchmarking + MVP
- Peer Retriever with AUM-band + strategy filters
- Redline Report Writer
- HITL Review Gate with audit table
- Golden set up to 40 labeled sections
- First readable report out. **MVP milestone.**

### Week 4 — Eval at depth
- Golden set up to 60 labeled sections
- LLM-as-judge + cross-judge scoring
- CI running eval on every PR
- First regression caught in CI

### Week 5 — Polish
- Architecture doc + ADRs
- Compliance doc
- 3 Langfuse traces linked from README
- Demo video / GIF
- README final pass

### Week 6 — Optional bolt-on: ADV-Diff
- Scheduled quarterly ADV change detector (Project 7 from the research)
- Reuses this project's parser
- Adds monitoring + change-summary agent
- Raises the portfolio piece from "one-shot analysis" to "ops-grade monitoring"

---

## Cut-list if time runs tight

If you hit week 3 and the MVP isn't working:

- **Cut Ollama fallback branch.** Keep Claude-only. Note it as future work.
- **Cut the Opus redline writer.** Use Sonnet 4.6 for the redline. Slightly less polished output, same architecture.
- **Cut to 40 golden-set items instead of 60.** Label depth over breadth.

Do not cut: the eval harness, the HITL gate, the compliance doc, the audit table. Those are the differentiators.
