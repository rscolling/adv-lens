"""Disciplinary Extractor — turns Item 9 prose into a ``DisciplinaryExtraction``.

Item 9 — Disciplinary Information disclosures are dominated by "Not
applicable" / "The firm has no disciplinary history to disclose." The
extractor must recognise that case and set ``has_disciplinary_history``
to False with no events. When events do exist, each one becomes a typed
``DisciplinaryEvent`` for downstream peer comparison.

Default model is Haiku 4.5 — clear-cut category labels, low cost
(~$0.80/MTok input). Override via ``settings.model_disciplinary``.
"""

from __future__ import annotations

from adv_lens.extractors.schemas import DisciplinaryExtraction
from adv_lens.llm.client import LLMClient

DISCIPLINARY_SYSTEM_PROMPT = """\
You are a paralegal-grade extractor for U.S. SEC Form ADV Part 2A
brochures, Item 9 — Disciplinary Information.

Your task: given the body text of Item 9, return a DisciplinaryExtraction
Pydantic object capturing whether the firm has any disclosed disciplinary
history and, if so, the structured details of each event.

Decision rules:

1. ``has_disciplinary_history``:
   - False when the text says "Not applicable", "no disciplinary history",
     "no material disciplinary events to disclose", or any equivalent
     plain-English denial.
   - True when ANY event is described, even if the firm claims it is
     "not material." Capture the event regardless of materiality.
   - If the text is genuinely ambiguous, set False and add an
     ``extraction_warnings`` entry explaining why.

2. For each event, fill in fields conservatively:
   - ``event_type``: pick the closest from the controlled vocabulary
     (criminal / sec_administrative / state_administrative / sro /
     civil_judicial / other_regulatory / other). SEC = sec_administrative,
     FINRA / NFA / exchanges = sro, courts = civil_judicial.
   - ``event_date``: only when a full date is disclosed. Otherwise use
     ``event_year`` for the year alone.
   - ``involved_party_type``: "firm" when the named respondent is the
     adviser entity, "principal" for owners/officers, "supervised_person"
     for employees / IARs, "affiliate" for related entities.
   - ``allegation``: a short paraphrase (one sentence). Do not copy
     long verbatim passages.
   - ``resolution``: pick from settled / consent_order / found_in_violation
     / convicted / dismissed / vacated / pending / unknown. "Without
     admitting or denying" → settled. "Consent" → consent_order.
   - ``sanction_monetary_usd``: integer USD penalty/fine/disgorgement.
     Sum if multiple monetary sanctions for the same event. Null if
     none / not stated.
   - ``sanction_suspension_days``: integer days of suspension/bar where
     finite. Null if "permanent bar" (use ``sanction_other`` for that).
   - ``sanction_other``: short tags from this controlled vocabulary when
     applicable: "censure", "cease and desist", "bar", "permanent bar",
     "undertaking", "compliance review", "disgorgement", "supervisory
     restriction". Add other tags only when necessary.
   - ``is_material``: only set when the firm explicitly characterises
     materiality. Otherwise null.

3. Never invent facts. A null is always safer than a fabricated date,
   amount, or party name. The CCO who reads this output will use it to
   defend an exam.
"""


class DisciplinaryExtractor:
    """Wraps an LLMClient with the disciplinary-extraction prompt + schema."""

    NODE_NAME = "disciplinary_extractor"

    def __init__(self, llm: LLMClient, model: str | None = None) -> None:
        self._llm = llm
        from adv_lens.app.settings import settings

        self._model = model or settings.model_disciplinary

    async def extract(
        self,
        section_body: str,
        *,
        trace_id: str,
        brochure_crd: str | None = None,
    ) -> DisciplinaryExtraction:
        if not section_body.strip():
            # Empty Item 9 → safest default is "no history disclosed" with a
            # warning so a reviewer can reconcile against IARD Part 1.
            return DisciplinaryExtraction(
                has_disciplinary_history=False,
                extraction_warnings=[
                    "Empty Item 9 body; defaulted has_disciplinary_history=False."
                ],
            )
        return await self._llm.extract(
            model=self._model,
            system=DISCIPLINARY_SYSTEM_PROMPT,
            prompt=section_body,
            response_model=DisciplinaryExtraction,
            trace_id=trace_id,
            node=self.NODE_NAME,
            brochure_crd=brochure_crd,
        )
