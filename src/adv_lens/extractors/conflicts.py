"""Conflicts Extractor — Items 10/11/12 in one LLM call.

Item 10 (Other Financial Industry Activities and Affiliations),
Item 11 (Code of Ethics, Personal Trading), and Item 12 (Brokerage
Practices) are read together. The combined call lets the model
cross-check (an affiliated broker-dealer disclosed in Item 10 should
align with the directed-brokerage practice in Item 12) and keeps the
token spend down vs three separate Sonnet calls.

Default model is Sonnet 4.6 — these Items have nuance (Section 28(e)
safe-harbor logic, "brokerage for client referrals" detection) that
Haiku struggles with on real brochures.
"""

from __future__ import annotations

from adv_lens.extractors.schemas import ConflictsExtraction
from adv_lens.llm.client import LLMClient

CONFLICTS_SYSTEM_PROMPT = """\
You are a paralegal-grade extractor for U.S. SEC Form ADV Part 2A
brochures, Items 10, 11, and 12.

You will receive the body text of all three Items concatenated, each
introduced by a clear ``=== Item N ===`` header. Return a
ConflictsExtraction Pydantic object filling in only the fields that the
text supports.

Decision rules:

ITEM 10 (Affiliations):
- ``affiliated_broker_dealer``: True if the firm or any related person
  is registered as / owns / is owned by a broker-dealer. False if the
  text explicitly says "no affiliated broker-dealer" or equivalent.
  Null only if the section is silent.
- ``affiliated_investment_adviser``: True if there's an affiliated RIA
  (sister firm, parent advisory).
- ``affiliated_investment_company``: True if affiliated with a fund
  family / mutual fund sponsor.
- ``affiliated_insurance``: True if affiliated insurance agency or
  insurance company. Common in hybrid advisory shops.
- ``affiliated_bank``: True if affiliated bank or trust company.
- ``uses_other_investment_advisers``: True if the firm uses sub-advisers
  or model portfolios from third-party managers.
- ``affiliations``: short tags ("broker-dealer", "RIA holding company",
  "insurance agency", "fund complex"). Dedup; keep <8 entries.

ITEM 11 (Code of Ethics + Personal Trading):
- ``has_code_of_ethics``: True when the firm states it has adopted a
  Code of Ethics (Rule 204A-1 essentially mandates this). Null only
  when the brochure is silent.
- ``recommends_securities_with_material_interest``: True when the firm
  or related persons may recommend securities they have a material
  financial interest in (proprietary funds, principal transactions).
- ``personal_trading_in_recommended_securities``: True when supervised
  persons may buy/sell the same securities recommended to clients.
- ``requires_personal_trade_preclearance``: True when employees must
  pre-clear personal trades (or specific categories like IPOs / limited
  offerings).
- ``requires_personal_trade_reporting``: True when employees must
  submit quarterly transaction reports / annual holdings reports.

ITEM 12 (Brokerage):
- ``accepts_soft_dollars``: True when the firm receives research or
  brokerage services from broker-dealers in exchange for client
  commissions ("soft dollar arrangements", "research credits", "Section
  28(e) benefits").
- ``soft_dollar_within_28e_safe_harbor``: True when the firm states
  arrangements fall within Section 28(e) of the '34 Act (research only).
  False if explicitly outside the safe harbor. Null if soft dollars are
  declined or not addressed.
- ``accepts_directed_brokerage``: True when clients may direct the firm
  to use a specific broker.
- ``requires_directed_brokerage``: True when the firm requires clients
  (e.g., wrap clients) to direct trades to a specific custodian/broker.
- ``brokerage_for_referrals``: True when the firm directs trades to
  brokers who refer clients to the firm (material conflict).
- ``aggregates_orders``: True when the firm aggregates client orders
  for execution (block trades).

Universal rules:
- A null is always safer than a guess. Optional booleans default to None.
- Add free-text concerns to ``extraction_warnings`` when the prose is
  ambiguous, contradictory, or missing entirely for an Item.
- Never invent affiliations, sanctions, or facts not in the text.
"""

ITEM_10_HEADER = "=== Item 10 — Other Financial Industry Activities and Affiliations ==="
ITEM_11_HEADER = "=== Item 11 — Code of Ethics, Participation or Interest in Client Transactions and Personal Trading ==="
ITEM_12_HEADER = "=== Item 12 — Brokerage Practices ==="


def build_combined_prompt(item_10: str | None, item_11: str | None, item_12: str | None) -> str:
    """Concatenate the three Item bodies under explicit section headers."""
    parts: list[str] = []
    parts.append(ITEM_10_HEADER)
    parts.append((item_10 or "").strip() or "(Item 10 not present in brochure.)")
    parts.append("")
    parts.append(ITEM_11_HEADER)
    parts.append((item_11 or "").strip() or "(Item 11 not present in brochure.)")
    parts.append("")
    parts.append(ITEM_12_HEADER)
    parts.append((item_12 or "").strip() or "(Item 12 not present in brochure.)")
    return "\n".join(parts)


class ConflictsExtractor:
    """Wraps an LLMClient with the conflicts-extraction prompt + schema."""

    NODE_NAME = "conflicts_extractor"

    def __init__(self, llm: LLMClient, model: str | None = None) -> None:
        self._llm = llm
        from adv_lens.app.settings import settings

        self._model = model or settings.model_conflicts

    async def extract(
        self,
        item_10_body: str | None,
        item_11_body: str | None,
        item_12_body: str | None,
        *,
        trace_id: str,
        brochure_crd: str | None = None,
    ) -> ConflictsExtraction:
        # If all three sections are missing/empty, return an empty extraction
        # with a warning rather than calling the model on placeholder text.
        if not any(b and b.strip() for b in (item_10_body, item_11_body, item_12_body)):
            return ConflictsExtraction(
                extraction_warnings=[
                    "Items 10/11/12 all missing or empty; no extraction performed."
                ]
            )
        prompt = build_combined_prompt(item_10_body, item_11_body, item_12_body)
        return await self._llm.extract(
            model=self._model,
            system=CONFLICTS_SYSTEM_PROMPT,
            prompt=prompt,
            response_model=ConflictsExtraction,
            trace_id=trace_id,
            node=self.NODE_NAME,
            brochure_crd=brochure_crd,
        )
