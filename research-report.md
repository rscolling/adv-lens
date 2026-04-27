# Wealth Management AI Portfolio Projects — Research Report

**Date:** 2026-04-24
**Topic:** GitHub portfolio projects to demonstrate AI consulting capability for wealth management / RIAs / family offices
**Audience:** Senior engineer (Robert Colling) targeting F2 Strategy and peer consultancies

---

## Key Findings

- **The wealthtech market buys five things right now, in order of dollars spent:** (1) advisor meeting note-taking + CRM sync (Jump, Zocks, Zeplyn own this — Zoom at 27%, Fathom/Jump around 20%, Zocks 10%), (2) document ingestion and reconciliation from custodian PDFs and alt-investment statements (Masttro, Addepar, Orion), (3) Marketing Rule / Reg Notice 24-09 surveillance of communications (Smarsh AI Assistant launched March 2025, Luthor.ai, Saifr), (4) client onboarding + KYC + suitability automation, (5) portfolio reporting narrative generation. F2 Strategy's 2025 survey of 42 firms shows **95% of RIAs have active AI initiatives vs 23% of bank trust**, and the #1 measured benefit is "90 minutes saved per client meeting" and reduction in NIGO (not-in-good-order) forms.

- **GitHub is saturated in exactly the places portfolio demos usually live.** 10-K RAG chatbots, generic financial advisor chatbots, SEC EDGAR agent toolkits (stefanoamorelli/sec-edgar-agentkit, run-llama/sec-insights, alphanome-ai/sec-parser) are done to death. What is comparatively empty: **Form ADV Part 2 analytics, SEC Marketing Rule violation detection, IPS drafting with compliance overlays, RIA-specific meeting note evaluation harnesses, suitability memo generation from structured intake, alt-investment PDF reconciliation agents.** These are the spaces where a credible portfolio piece stands out.

- **The regulatory reality in April 2026 is more settled than it was a year ago.** The SEC's Predictive Data Analytics (PDA) proposal was withdrawn June 12, 2025. What remains binding: SEC Marketing Rule 206(4)-1 (AI-washing enforcement is active — March 2024 adviser settlement is still the template), FINRA Reg Notice 24-09 (technology-neutral rules apply to GenAI, top use case logged is "summarization and information extraction"), FINRA 2026 Annual Regulatory Oversight Report (supervision and recordkeeping for GenAI outputs), and state-level AI governance expectations. A portfolio piece that visibly engages with these — not just mentions them — signals "I can ship to a CCO."

- **The actual differentiators in 2026 are boring and important:** (a) evaluation harnesses with golden sets that handle financial text-specific failure modes (numeric hallucination, citation misattribution, suitability drift), (b) structured output enforcement via Pydantic/Instructor/Outlines so CCOs get schema guarantees, (c) human-in-the-loop gating with audit trails (exactly what Don Quixote already demonstrates generically — the wealth-specific version is the gap), (d) on-prem / VPC deployability because most RIAs with >$5B AUM treat client PII as a residency issue, (e) observability that a compliance officer (not an MLE) can read.

- **F2 Strategy's stated consulting model is OCTO (Outsourced CTO) + roadmap + implementation.** They serve $2B–$20B AUM RIAs. Doug Fritz's public framing is that AI cannot "fix" a broken process — data hygiene and onboarding must come first. Portfolio pieces that look like **technical enablement artifacts a consultant would hand to an OCTO engagement** (architecture sketches + working reference implementations + eval harnesses + compliance memos) will resonate more than stand-alone products.

- **The single most unoccupied niche:** open-source, compliance-audited agentic workflows targeted at RIAs in the $2B–$20B band, with Langfuse/LangSmith traces a CCO can read, Pydantic-validated outputs, and an eval harness that actually grades the text against Marketing Rule language. Every project below leans into this gap.

---

## Project Proposals

### Project 1: ADV-Lens — Form ADV Part 2A Intelligence + Competitive Disclosure Benchmarking

- **One-line pitch:** A LangGraph agent that ingests any RIA's Form ADV Part 2A brochure and produces a compliance-and-competitive scorecard: fee-structure benchmarking vs peer advisers, disciplinary disclosure flags, conflict-of-interest enumeration, and a redline against SEC plain-English expectations.
- **Problem it solves (who wakes up wanting this):** RIA Chief Compliance Officers doing annual ADV reviews and M&A diligence teams evaluating RIA roll-up targets. Right now this is a paralegal doing 40 hours of manual reading. F2 Strategy does M&A tech due diligence for exactly this market — this is a plausible consulting deliverable.
- **Architecture sketch:**
  - Ingestion: pull brochures from SEC IAPD (adviserinfo.sec.gov) + IARD bulk ADV Part 1 CSVs (free, public).
  - Parsing: `alphanome-ai/sec-parser` for structure + LlamaParse fallback for the irregular PDFs.
  - Pipeline: LangGraph state machine — (1) section segmenter, (2) fee-schedule extractor with Pydantic schema, (3) disciplinary-disclosure classifier, (4) conflicts-of-interest enumerator, (5) peer-comparison retriever (Qdrant over pre-indexed peer brochures filtered by AUM band and strategy), (6) redline report writer.
  - Eval: Langfuse traces + a hand-labeled golden set of ~60 ADV sections across AUM bands with correct fee-structure / disciplinary / conflict extractions. Score with exact-match for structured fields, LLM-as-judge for narrative redlines, with a second judge model to catch the first judge's drift.
  - Deployment: FastAPI + Docker, VPC-deployable, Ollama fallback for on-prem inference.
- **Skill it demonstrates to a hiring manager:** Real regulatory literacy (not just "I read a 10-K"), structured extraction discipline, peer-benchmarking retrieval patterns, and an eval harness scoped to a compliance audience. Shows you can ship to a CCO.
- **Compliance/risk posture:** All data is public (SEC-filed). Output is positioned as *analyst aid*, not legal advice — explicit disclaimer, no auto-redaction, CCO sign-off expected. Exactly the posture Reg Notice 24-09 and the Marketing Rule demand.
- **Effort estimate:** 3 weeks to MVP (single-firm analysis), 5–6 weeks to polished portfolio piece with peer benchmarking and the full eval harness.
- **Differentiation:** GitHub has EDGAR 10-K RAG projects in abundance. Form ADV Part 2 with structured extraction + peer benchmarking + compliance redline is, as of April 2026, not publicly present in a credible form. This is the niche.

---

### Project 2: MeetingLens-RIA — Advisor Meeting Note Capture with Compliance-Grade Eval Harness

- **One-line pitch:** Not another Jump/Zocks clone. A **golden-set evaluation harness + reference implementation** that grades AI meeting-note output against SEC Marketing Rule, FINRA Reg Notice 24-09, and state fiduciary expectations — plus a working on-prem capture pipeline for firms that can't send client voice data to Zoom AI.
- **Problem it solves (who wakes up wanting this):** An RIA's CCO who just deployed Jump or Zocks and now has to defend the accuracy of AI-generated notes to the SEC on exam. Also an OCTO engagement that needs to evaluate vendors — "we benchmarked Jump, Zocks, and Zeplyn against 200 simulated advisor conversations."
- **Architecture sketch:**
  - Synthetic advisor-conversation corpus: 150–250 Whisper-transcribed mock client meetings (scripted + crowdsourced), labeled for: performance promises (Marketing Rule trigger), suitability statements, testimonial-adjacent language, tax advice boundaries, undisclosed conflicts.
  - Capture pipeline: Parakeet/Whisper local transcription, speaker diarization, Ollama + Llama 3.3 summarization. On-prem end-to-end.
  - Eval harness: Langfuse traces + Braintrust-style golden set with CI scoring. Categories: fidelity (did the summary match the transcript), compliance-flag recall (did it catch the performance-promise language), CRM-field precision (for Wealthbox/Redtail-shaped structured output via Instructor/Pydantic), hallucination detection (named entities not in transcript).
  - Output: a report card comparing the local pipeline against cloud vendors on the same golden set (vendors via their public APIs, where terms allow).
- **Skill it demonstrates to a hiring manager:** You understand that the wealthtech buying question isn't "does it work" — it's "can I defend it on exam." Evaluation engineering at this level is rare in public portfolios.
- **Compliance/risk posture:** Uses synthetic conversations (no real client PII). Explicitly tests for Reg Notice 24-09 compliance failure modes. The eval harness itself is the compliance artifact — a CCO can re-run it quarterly.
- **Effort estimate:** 4 weeks to MVP (corpus + local pipeline + baseline eval), 6 weeks to polished piece with vendor benchmarking.
- **Differentiation:** GitHub has five open-source local meeting notetakers (Meetily, Recap, OpenNotes, Minutes, Meminto). None of them have a financial-services eval harness. Shipping the harness is the differentiator — the capture pipeline is table stakes.

---

### Project 3: IPS-Drafter — LangGraph Investment Policy Statement Agent with Compliance Overlay

- **One-line pitch:** Agentic drafting of Investment Policy Statements from a structured intake, with Marketing Rule language guardrails, per-section citations to CFA/Fi360 best-practice references, and a human-in-the-loop review gate before delivery.
- **Problem it solves (who wakes up wanting this):** An advisor drafting IPSs takes 3–6 hours per client. Tools like Easy-Peasy AI and Go Legal AI exist but are generic drafters; none are built around LangGraph with compliance overlays and a citation-validation gate. Family offices drafting IPSs for multiple entities (family LLCs, trusts, foundations) need variants keyed off shared governance documents.
- **Architecture sketch:**
  - Intake: structured Pydantic schema (client type, entity class, risk tolerance instrument answers, return objectives, liquidity constraints, tax situation, ESG/SRI preferences, prohibited holdings list).
  - LangGraph orchestration: (1) intake validator, (2) section drafter nodes for Purpose / Responsibilities / Asset Allocation / Rebalancing Policy / Performance Measurement / Review Cadence, (3) citation retriever over a curated corpus (CFA IPS guide, Fi360 Fiduciary Handbook, Morgan Stanley and Schwab public templates), (4) Marketing Rule language auditor (flags performance promises, forward-looking statements without disclaimers), (5) fiduciary consistency checker (cross-section conflict detection), (6) human review gate, (7) final writer.
  - Validation: Instructor/Pydantic for every structured handoff; sections that fail schema bounce back.
  - Eval: golden set of 25 hand-reviewed IPS drafts, scored on citation accuracy, section-requirement coverage (Multnomah Group checklist), and Marketing Rule cleanliness.
  - Observability: Langfuse trace per draft; CCO can see every intermediate step.
- **Skill it demonstrates to a hiring manager:** LangGraph as a centerpiece (not bit-part), structured-output discipline, retrieval grounded in real fiduciary documents, and — most importantly — that you think about HITL as a compliance feature, not an afterthought.
- **Compliance/risk posture:** The Marketing Rule auditor node is the star. Citations are from public, non-copyrighted sources (SEC, state regulators, Multnomah Group publicly released templates, CFA Institute public standards). Output is explicitly "draft, subject to advisor review" — matches the Easy-Peasy / go-legal.ai framing that has survived CCO review.
- **Effort estimate:** 3 weeks MVP (happy-path drafting), 5 weeks polished with the Marketing Rule auditor and eval harness.
- **Differentiation:** Existing IPS tools are single-prompt generators. A LangGraph-orchestrated multi-node agent with a Marketing Rule auditor and golden-set eval is not publicly shipping. This is where F2 Strategy's "we deliver tech that passes CCO review" value prop intersects with a demo.

---

### Project 4: AltRecon — Alternative Investment Statement Reconciliation Agent

- **One-line pitch:** A multi-modal agent that ingests the PDFs family offices actually drown in — capital call notices, LP distributions, K-1s, private fund statements — extracts positions into a canonical schema, and reconciles against a portfolio accounting source of truth.
- **Problem it solves (who wakes up wanting this):** Family office ops managers and mid-market RIAs with alt sleeves. F2 Strategy's Q2 2025 report explicitly called out that "fewer wealth management firms use technology to manage alternative investments than expected" — this is a named F2 consulting focus area. Masttro and Addepar both ship AI extraction here but charge accordingly.
- **Architecture sketch:**
  - Ingestion: PDF multi-modal (Claude Sonnet 4.6 or GPT-4o vision), LlamaParse for tabular fallback.
  - Entity extraction: Pydantic schema per document type (Capital Call, Distribution, Quarterly Statement, K-1). Document-type router first.
  - Reconciliation agent (LangGraph): (1) extract, (2) normalize entity names to canonical LEI / fund identifiers, (3) load accounting truth (CSV/SQL), (4) diff, (5) classify breaks (timing / amount / missing / entity-mismatch), (6) draft ticket with proposed journal entries, (7) HITL gate.
  - Public-data test corpus: SEC Form PF filings (public for certain funds), publicly released LP reports from endowment funds (e.g., Yale, Harvard, CalPERS publish some), sanitized synthetic statements.
  - Eval: break-classification precision/recall, extraction F1 on a 100-document labeled set, Langfuse traces.
- **Skill it demonstrates to a hiring manager:** Multi-modal extraction at the level an actual ops team cares about, not just chatbot demos. Reconciliation logic that understands the domain. Schema discipline. This is the kind of project that maps 1:1 to an F2 or Masttro consulting engagement.
- **Compliance/risk posture:** Reconciliation output is proposed-journal-entries, not final; HITL gate is mandatory; PII only in synthetic documents (public LP reports are institutional, not client-level). Matches the posture family offices will accept from external tooling.
- **Effort estimate:** 4 weeks to MVP on a single doc type (capital calls), 6 weeks for multi-doc type + reconciliation + eval.
- **Differentiation:** GitHub has "generic invoice extraction" and "10-K parsers." Nothing in the alt-investment reconciliation niche is public. This is the highest-dollar pain point among the projects on this list.

---

### Project 5: MarketingRule-Guard — SEC Rule 206(4)-1 Language Classifier + Surveillance Pipeline

- **One-line pitch:** A classifier + evaluation pipeline that grades advisor marketing content (blog posts, client emails, pitch decks, LinkedIn posts) against SEC Marketing Rule violation patterns — performance promises without disclaimers, undisclosed testimonials, unsubstantiated superlatives, AI-washing.
- **Problem it solves (who wakes up wanting this):** CCOs who currently either block all marketing or eyeball every post. Smarsh and Luthor ship this commercially. The open-source version doesn't exist — this is the "look, I understand the regulation in enough depth that I can operationalize it" portfolio piece.
- **Architecture sketch:**
  - Violation taxonomy: hand-curated from SEC enforcement actions (March 2024 adviser settlement, September 2023 omnibus action against 9 advisers, FAQ updates) — 12–18 categories.
  - Classifier: fine-tune a small model (Claude Haiku 3.5 via API, or a local Qwen 2.5 14B) on a synthesized + hand-labeled training set of ~1,200 marketing snippets. Pair with a rule-based layer (spaCy patterns) for high-precision triggers (dollar figures near "returns," testimonial words, "AI-powered" + strategy claims).
  - Surveillance pipeline: LangGraph — (1) content ingest, (2) rule-layer flagging, (3) classifier layer, (4) disposition (pass / review / block) with confidence, (5) reason-code generator citing the specific SEC FAQ / rule text, (6) reviewer queue.
  - Eval: confusion matrix by violation category, precision/recall per category, ROC curves. A CCO-readable dashboard.
  - Corpus: public marketing material from ADV-registered advisers' websites (scraping public marketing pages is acceptable), synthetic violations injected.
- **Skill it demonstrates to a hiring manager:** Classifier engineering plus rules-based hybrid (shows you know when not to use LLMs for everything). Direct engagement with a live, actively enforced regulation. Surveillance workflow design.
- **Compliance/risk posture:** Output is "flag for review," never auto-block or auto-publish. The AI-washing category is itself an enforced rule (March 2024 settlement) — this project demonstrates that you avoid the trap, not step in it.
- **Effort estimate:** 3 weeks MVP (rule layer + one-shot classifier), 5 weeks polished with fine-tuned classifier + dashboard.
- **Differentiation:** Luthor.ai and Smarsh are closed-source and enterprise-priced. The open-source equivalent demonstrates regulatory literacy at a level hiring managers rarely see.

---

### Project 6: ClientBrief — Multi-Source Client Meeting Prep Agent

- **One-line pitch:** Given an upcoming client meeting on an advisor's calendar, assemble a one-page brief: portfolio drift flags, recent news on their holdings, life-event signals from CRM notes, outstanding service tickets, last meeting's commitments.
- **Problem it solves (who wakes up wanting this):** Every advisor. F2 Strategy's survey explicitly calls out "more preparation for client meetings" as the #2 measured AI benefit (after time savings). Commercially this is where Jump is expanding past note-taking.
- **Architecture sketch:**
  - Data sources: synthetic CRM (Wealthbox-shaped CSV), FRED macro data, yfinance holdings data, public company news via a free news API or SEC 8-K filings, mock portfolio-accounting truth.
  - LangGraph orchestration: (1) meeting retrieval, (2) client context hydration, (3) parallel fan-out — portfolio drift check, news sweep, life-event miner on CRM notes, outstanding-commitment extractor — (4) synthesizer with Pydantic-structured brief, (5) HITL review before email-to-advisor.
  - Eval: 30-scenario golden set (known drift / no drift, known life event / none, known news / none) — measure brief completeness and precision.
- **Skill it demonstrates to a hiring manager:** Orchestration across heterogeneous data sources (exactly what OCTO engagements need), parallel agent fan-out patterns, pragmatic use of LangGraph as workflow control plane.
- **Compliance/risk posture:** Brief is advisor-facing only (not client-facing) — lower Marketing Rule exposure. CRM notes only ever read inside the firm's trust boundary.
- **Effort estimate:** 2 weeks MVP, 4 weeks polished with eval harness.
- **Differentiation:** Medium differentiation. This one is closer to what vendors already ship. Its value as a portfolio piece is that it's **visibly consulting-shaped** — the kind of thing F2 would recommend a firm build internally with custom CRM schema. I'd build this third, after 1 and 2.

---

### Project 7: ADV-Diff — Quarterly Form ADV Change Detector for M&A Diligence

- **One-line pitch:** Cron job + LangGraph agent that monitors Form ADV amendments across a watch-list of RIAs and produces human-readable change summaries: new conflicts disclosed, AUM shifts, disciplinary additions, fee changes, custodian changes.
- **Problem it solves (who wakes up wanting this):** RIA aggregators (Focus Financial, Dynasty, Hightower, Creative Planning), M&A advisory shops, and the OCTO teams at F2 who are advising on roll-ups. Right now this is manual diffing in Word.
- **Architecture sketch:**
  - Scheduler polls IAPD / IARD CSVs weekly.
  - When a watched CRD filing updates: fetch new + prior brochure, diff via a section-aware differ (reusing Project 1's parser).
  - LangGraph pipeline: classify diff into categories (fee change, conflict new/removed, disciplinary new, AUM change, custodian change, personnel change), score materiality, draft summary.
  - Notifications via email/Slack with Langfuse trace link.
  - Public, no synthetic data needed.
- **Skill it demonstrates to a hiring manager:** Ops-shaped engineering (not just notebooks), scheduling/monitoring, familiarity with SEC data pipelines, end-to-end pragmatism.
- **Compliance/risk posture:** Reads only public filings. Output is advisory, not recommendation.
- **Effort estimate:** 2 weeks if Project 1 exists (reuses parser), 3 weeks standalone.
- **Differentiation:** Medium-low. Genuinely useful but technically a straightforward delta of Project 1. **Honest note: I'd include this only as a bolt-on to Project 1, not as a standalone portfolio piece.**

---

### Project 8: SuitabilityMemo-Drafter — Structured Intake to Compliance-Reviewed Suitability Memo

- **One-line pitch:** Takes a structured client intake (risk tolerance instrument, time horizon, income/net worth, experience, objectives) and drafts the suitability memo an advisor would normally write for each recommended allocation, with cross-section consistency checking.
- **Problem it solves (who wakes up wanting this):** Broker-dealer hybrid advisors and RIAs with annual suitability review obligations. Currently a 30–60 minute task per client, done inconsistently.
- **Architecture sketch:** Similar LangGraph pattern to IPS-Drafter but narrower: (1) intake validator, (2) allocation context retriever, (3) section drafters (client profile / objective / rationale / risk acknowledgments / review cadence), (4) cross-section consistency checker, (5) Marketing Rule auditor (reused from Project 5 if it exists), (6) HITL gate.
- **Skill it demonstrates to a hiring manager:** Same as IPS-Drafter but slightly narrower scope.
- **Compliance/risk posture:** Output is draft-for-advisor-review, explicitly. Strong Pydantic validation on intake.
- **Effort estimate:** 2–3 weeks MVP (narrower than IPS-Drafter).
- **Differentiation:** Low-medium. **Honest note: this is filler. If you build Project 3 (IPS-Drafter), this is an obvious second application of the same architecture and not a separate portfolio piece — include it as a second use case within Project 3's repo rather than as its own project.**

---

## Ranked Shortlist — Top 3 to Build

1. **Project 1: ADV-Lens** — This is the single most defensible portfolio piece. Form ADV Part 2 analytics with peer benchmarking and compliance redlines is a genuine gap on GitHub, the data is fully public, the compliance posture is clean (CCO aid, not legal advice), and it maps directly to F2 Strategy's stated M&A and roadmap work. The eval harness alone is a credibility artifact. Build this first.

2. **Project 2: MeetingLens-RIA** — Build this second, but frame it differently than "another meeting notetaker." The headline is the **evaluation harness** that grades output against Marketing Rule and Reg Notice 24-09 failure modes. Every RIA in the target segment is currently buying Jump/Zocks/Zeplyn and has no defensible way to measure accuracy for SEC exam. Shipping the harness is the consulting deliverable; the on-prem reference pipeline is the "by the way, here's what a private deployment looks like" proof point.

3. **Project 3: IPS-Drafter** — This is where LangGraph gets to be the centerpiece and where the Marketing Rule auditor node earns its keep. It's also the most visually impressive in a portfolio walkthrough — you can show a 12-page PDF being drafted from a structured intake with each node's decisions traced in Langfuse. Fold Project 8 (SuitabilityMemo-Drafter) in as a second application of the same architecture to show pattern reuse without building a separate repo.

**What to skip:** Project 7 (ADV-Diff) as a standalone — fold into Project 1's README. Project 8 — fold into Project 3. Project 6 (ClientBrief) is fine but saturated; build it fourth only if you need a fourth.

**What this leaves you with:** Three repositories. ADV-Lens (regulatory analytics), MeetingLens-RIA (eval harness + on-prem reference), IPS-Drafter (agentic drafting with compliance overlay). Together they hit all of: SEC data fluency, evaluation engineering, LangGraph as centerpiece, Pydantic/structured output discipline, Langfuse observability, HITL gating, on-prem deployability, and explicit engagement with Rule 206(4)-1 and Reg Notice 24-09. That's a consultant's portfolio, not a hobbyist's.

---

## Sources

1. [F2 Strategy — Where Wealth Management is Going with AI](https://www.f2strategy.com/insight/what-ive-seen-where-wealth-management-is-going-with-ai) — F2 Strategy's strategic framing of AI adoption priorities.
2. [F2 Strategy — Wealth Management Firms Begin to Implement AI with an Eye on Security and Regulation](https://www.f2strategy.com/insight/wealth-management-firms-begin-to-implement-ai-with-an-eye-on-security-and-regulation) — F2's security/regulation lens on AI rollouts.
3. [F2 Strategy — homepage](https://f2strategy.com/) — service lines (Technology, Marketing, Managed Services / OCTO), market segments served.
4. [F2 Report: AI Use Grew 23% Since 2023 Among Wealth Management Firms](https://wealthsolutionsreport.com/2025/06/10/f2-report-ai-use-grew-23-since-2023-among-wealth-management-firms/) — 2025 Q2 trend report, 42-firm survey, 95% RIA AI adoption.
5. [F2 Strategy — Q2 2025 Alternative Investments report](https://f2strategy.com/insight/q2-2025-alternative-investments) — alt-investment technology gap (pain point for Project 4).
6. [The Oasis Group — AI Readiness Index](https://t3technologyhub.com/the-oasis-group-releases-ai-readiness-index-first-maturity-benchmark-for-wealth-management-industry/) — maturity benchmark across five pillars, 35 questions.
7. [FINRA Regulatory Notice 24-09](https://www.finra.org/rules-guidance/notices/24-09) — GenAI rule-applicability reminder.
8. [FINRA 2026 Annual Regulatory Oversight Report — GenAI](https://www.finra.org/rules-guidance/guidance/reports/2026-finra-annual-regulatory-oversight-report/gen-ai) — current enforcement posture; top GenAI use case ("summarization and information extraction").
9. [Kitces — SEC Marketing Rule: Compliance Policies Checklist](https://www.kitces.com/blog/sec-marketing-rule-enforcement-investment-adviser-key-takeways-compliance-tips-regulations/) — operational Marketing Rule breakdown.
10. [Morrison Foerster — AI Compliance Tips for Investment Advisers](https://www.mofo.com/resources/insights/251015-ai-compliance-tips-for-advisers) — October 2025 SEC AI compliance guidance.
11. [SEC.gov — Marketing Rule FAQs](https://www.sec.gov/rules-regulations/staff-guidance/division-investment-management-frequently-asked-questions/marketing-compliance-frequently-asked-questions) — primary source for Project 5 violation taxonomy.
12. [Ontra — New FAQs significantly impact SEC Marketing Rule compliance](https://www.ontra.ai/blog/new-faqs-significantly-impact-sec-marketing-rule-compliance/) — Marketing Rule FAQ change history.
13. [Luthor.ai — AI-Powered Workflow to Comply with the SEC Marketing Rule](https://www.luthor.ai/guides/ai-powered-workflow-sec-marketing-rule-compliance-rias-guide) — commercial benchmark for Project 5.
14. [SEC IAPD — Form ADV Part 2 Data Files](https://adviserinfo.sec.gov/adv) — bulk download source for Projects 1 and 7.
15. [SEC — Form ADV Data](https://www.sec.gov/foia-services/frequently-requested-documents/form-adv-data) — historical Part 1 CSVs pre-2025.
16. [SEC — Information About Registered Investment Advisers](https://www.sec.gov/data-research/sec-markets-data/information-about-registered-investment-advisers-exempt-reporting-advisers) — ongoing feed.
17. [stefanoamorelli/sec-edgar-agentkit (GitHub)](https://github.com/stefanoamorelli/sec-edgar-agentkit) — existing EDGAR work to avoid duplicating.
18. [alphanome-ai/sec-parser (GitHub)](https://github.com/alphanome-ai/sec-parser) — proposed dependency for Projects 1 and 7.
19. [run-llama/sec-insights (GitHub)](https://github.com/run-llama/sec-insights) — LlamaIndex SEC reference, for comparison.
20. [vyayasan/kyc-analyst (GitHub)](https://github.com/vyayasan/kyc-analyst) — existing open-source KYC/AML with HITL — shows the HITL + compliance certification pattern already landing on GitHub.
21. [Kitces — Best AI Notetakers for Financial Advisor Meetings](https://www.kitces.com/blog/ai-notetakers-client-meeting-for-financial-advisors-adoption-satisfaction-trends-research-productivity/) — Jump/Zocks/Zeplyn market share and satisfaction data.
22. [WealthTechToday — Best AI Notetakers & Assistants for Financial Advisors 2025](https://wealthtechtoday.com/2025/04/29/best-ai-notetakers-for-financial-advisors-2025-a-strategic-buyers-guide/) — buyer's-guide framing for Project 2.
23. [WealthTechToday — AI Notetakers and Compliance in Wealth Management](https://wealthtechtoday.com/2025/07/29/ai-notetakers-and-compliance-in-wealth-management-what-firms-need-to-know/) — compliance considerations that Project 2's eval harness must cover.
24. [WealthTechToday — The Dirty Secret About AI Implementation for RIAs](https://wealthtechtoday.com/2025/10/13/ai-implementation-for-rias-data-discipline/) — data-discipline framing relevant to F2's positioning.
25. [WealthTechToday — No Supervision, No Safety: Agentic AI Governance in RIAs](https://wealthtechtoday.com/2025/12/30/agentic-ai-governance-for-rias/) — governance gap Project 2's harness addresses.
26. [Smarsh — AI Assistant for Professional Archiving](https://fintech.global/2025/03/11/smarsh-transforms-compliance-with-ai-assistant-launch-for-professional-archives/) — commercial surveillance benchmark for Project 5.
27. [WealthManagement.com — Orion Denali AI rollout 2026](https://www.wealthmanagement.com/artificial-intelligence/wealthstack-roundup-orion-denali-ai-rollout-to-begin-in-2026) — Orion's AI strategy and timing.
28. [Envestnet Tamarac Q4 2025 platform enhancements](https://www.prnewswire.com/news-releases/envestnet-unveils-fourth-quarter-tamarac-platform-enhancements-accelerating-advisor-efficiency-security--client-experience-302648240.html) — competitor product direction.
29. [Masttro — Agentic AI for Family Offices](https://masttro.com/insights/agentic-ai-family-offices) — family-office reconciliation commercial benchmark for Project 4.
30. [Advisorpedia — Inside the WealthTech Revolution: How F2 Strategy Redefined Consulting for Advisors](https://www.advisorpedia.com/future-of-advice/inside-the-wealth-tech-revolution-how-f2-strategy-redefined-consulting-for-advisors/) — OCTO model and target-firm sizing ($2B–$20B AUM).
31. [InvestmentNews — The F2 Formula: Doug and Liz Fritz](https://www.investmentnews.com/ria-news/f2-strategies/262463) — founder background and consulting posture.
32. [Debevoise — FINRA's 2025 Regulatory Oversight Report: Focus on AI](https://www.debevoise.com/insights/publications/2025/02/finras-2025-regulatory-oversight-report-focus-on) — AI supervision and recordkeeping specifics.
33. [Sidley — US Securities & Commodities AI Guidelines for Responsible Use (Feb 2025)](https://www.sidley.com/en/insights/newsupdates/2025/02/artificial-intelligence-us-financial-regulator-guidelines-for-responsible-use) — regulatory landscape survey.
34. [Kitces — AI Compliance: Applying Existing SEC Regulatory Frameworks](https://www.kitces.com/blog/artificial-intelligence-compliance-considerations-investment-advisers-sec-securities-exchange-commission-legal-regulation-framework/) — framework mapping for Project 3's auditor node.
35. [LangChain — How Kensho Built a Multi-Agent Framework with LangGraph for Trusted Financial Data Retrieval](https://www.langchain.com/blog/customers-kensho) — reference LangGraph financial-services pattern.
36. [ACA Group — How AI Is Transforming Marketing Review Without Losing Human Judgment](https://www.acaglobal.com/industry-insights/how-ai-is-transforming-marketing-review-without-losing-human-judgment/) — HITL framing for Project 5.
37. [Multnomah Group — Best Practice Investment Policy Statement](https://blog.multnomahgroup.com/forward-thinking/best-practice-investment-policy-statement) — IPS checklist source for Project 3.
38. [WealthTechToday — AI in RIA Operations](https://wealthtechtoday.com/2025/06/30/the-silent-revolution-how-ai-in-ria-operations-is-eating-your-tech-stack/) — operations automation use cases.
39. [SEC.gov — PDA Proposal Withdrawal (June 2025)](https://www.sec.gov/rules-regulations/2025/06/s7-12-23) — confirms the regulatory landscape shift.
40. [ncontracts — AI Compliance for RIAs: Key Risks and Best Practices](https://www.ncontracts.com/nsight-blog/investment-advisers-artificial-intelligence) — RIA-specific AI risk framework.

---

## Confidence

**Overall confidence: High** on the market shape (what firms are buying, what's saturated on GitHub, F2 Strategy's consulting posture) and on the regulatory citations (SEC Marketing Rule, Reg Notice 24-09, PDA withdrawal, recent FINRA reports are all well-sourced). **Medium** on effort estimates — they assume the candidate ships like a senior engineer with LangGraph experience already, which Robert demonstrably does (Don Quixote already shows 8-agent LangGraph production infra), but every project has a long tail in eval-set creation. **Medium-low** on the exact competitive positioning of Projects 1 and 5 — I'm confident no polished public repo exists in these niches as of the research date, but a credible private/paid tool might. That doesn't hurt the portfolio value; it reinforces it, since the pitch becomes "here's the open-source version of what vendors charge $40k/yr for."
