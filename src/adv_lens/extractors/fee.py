"""Fee Extractor — turns Item 5 prose into a ``FeeExtraction``.

System prompt is held in this module so eval and prompt-engineering live
together; tweaking the prompt and re-running the golden set are the loop.
"""

from __future__ import annotations

from adv_lens.extractors.schemas import FeeExtraction
from adv_lens.llm.client import LLMClient

FEE_SYSTEM_PROMPT = """\
You are a paralegal-grade extractor for U.S. SEC Form ADV Part 2A
brochures, Item 5 — Fees and Compensation.

Your task: given the body text of Item 5, return a FeeExtraction Pydantic
object that captures every fee program the firm offers, in structured form,
suitable for peer comparison.

Rules:
1. Use integers in base units (USD, basis points). 1.00% = 100 bps.
2. If a value is not stated in the text, leave it null. Do NOT guess.
3. One firm may offer multiple pricing programs (advisory, wrap, hourly,
   retainer, etc.). Capture each as a separate FeeSchedule entry.
4. Tier breakpoints: ``min_assets_usd`` is inclusive; ``max_assets_usd``
   is exclusive (None means open-ended at the top).
5. ``other_compensation_disclosed`` is a short list of tags from this
   controlled vocabulary when applicable: "12b-1 fees", "soft dollars",
   "directed brokerage", "commissions", "referral fees", "trail
   commissions", "principal transactions". Add other tags only when
   necessary; do not paraphrase.
6. Add free-text concerns to ``extraction_warnings`` when the prose is
   ambiguous or self-contradictory.

Be conservative. The CCO who reads this output will use it to defend an
exam. A null is always safer than a fabricated number.
"""


class FeeExtractor:
    """Wraps an LLMClient with the fee-extraction prompt + schema."""

    NODE_NAME = "fee_extractor"

    def __init__(self, llm: LLMClient, model: str | None = None) -> None:
        self._llm = llm
        # Per-call default model; settings.model_fee_extractor wins if model is None.
        from adv_lens.app.settings import settings

        self._model = model or settings.model_fee_extractor

    async def extract(
        self,
        section_body: str,
        *,
        trace_id: str,
        brochure_crd: str | None = None,
    ) -> FeeExtraction:
        if not section_body.strip():
            # Empty Item 5 is a real (rare) failure mode — return a typed empty
            # extraction rather than calling the model.
            return FeeExtraction(
                extraction_warnings=["Empty Item 5 body; no extraction performed."]
            )
        return await self._llm.extract(
            model=self._model,
            system=FEE_SYSTEM_PROMPT,
            prompt=section_body,
            response_model=FeeExtraction,
            trace_id=trace_id,
            node=self.NODE_NAME,
            brochure_crd=brochure_crd,
        )
