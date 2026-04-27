"""LLM-backed segmenter fallback per ADR 0014.

When the regex `HeuristicSegmenter` produces an empty or too-small body
for any of the five Items the extractors actually consume (5, 9, 10, 11,
12), this module asks Haiku 4.5 to locate the missing section spans in
the full brochure text. The regex result for Items it correctly found
stays authoritative — the LLM only fills gaps.

Design (per ADR 0014):

* **Triggered on body length, not regex success.** The 0-char and 144-char
  bodies on Brown Advisory both trip a single threshold cleanly.
* **Per-Item rescue, not full re-segmentation.** Preserves the regex's
  deterministic result for canonical brochures and for Items that did
  parse cleanly in difficult ones.
* **Haiku 4.5.** Span identification on English-language disclosure text;
  no domain reasoning required, no need for Sonnet/Opus.
* **One LLM call max per pipeline invocation.** All needed Items are
  located in a single prompt round-trip.
* **Output is character spans, not body text.** The downstream `Section`
  contract is unchanged; the fallback is invisible to extractors.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, field_validator

from adv_lens.llm.client import LLMClient, LLMError
from adv_lens.segmenter.models import (
    ITEM_TITLES,
    ItemNumber,
    Section,
    SegmentedBrochure,
)

logger = logging.getLogger(__name__)

# The five Items the extractors actually consume. Other Items (cover
# page, methods of analysis, custody narrative, etc.) are segmented but
# not currently scored, so a small body for those isn't worth a
# fallback round-trip.
RESCUE_ITEMS: frozenset[int] = frozenset({5, 9, 10, 11, 12})

# Below this size a section body is assumed to be a TOC fragment, page
# header noise, or a cross-reference rather than the actual narrative.
# Empirically: every real Item 5/9/10/11/12 narrative in our golden set
# is >5,000 chars; the largest false-positive (a TOC entry on Brown
# Advisory) was 144 chars. 2,000 is comfortably between the two.
RESCUE_THRESHOLD_CHARS = 2_000


class _ItemSpan(BaseModel):
    """One Item's char-offset span as identified by the LLM."""

    item_number: int = Field(ge=1, le=18)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    title: str | None = None

    @field_validator("char_end")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:
        start = info.data.get("char_start")
        if start is not None and v <= start:
            raise ValueError(f"char_end ({v}) must be > char_start ({start})")
        return v


class _RescueResponse(BaseModel):
    """Structured response from the rescue prompt."""

    spans: list[_ItemSpan] = Field(default_factory=list)


def _items_needing_rescue(seg: SegmentedBrochure) -> list[int]:
    """Return Item numbers in {5,9,10,11,12} with <THRESHOLD-char bodies
    or missing from the regex segmentation entirely."""
    found = {int(s.item_number): s for s in seg.sections}
    return [
        n
        for n in sorted(RESCUE_ITEMS)
        if n not in found or len(found[n].body) < RESCUE_THRESHOLD_CHARS
    ]


def _rescue_prompt(text: str, needed: list[int]) -> str:
    titles = "\n".join(f"  - Item {n}: {ITEM_TITLES[n]}" for n in needed)
    return f"""You are locating Form ADV Part 2A Item-section spans in a brochure
where the regex segmenter could not find a clean narrative header for
some required Items.

The brochure text is provided below verbatim. The Items the regex
could not isolate cleanly are:

{titles}

For each listed Item, return the character offsets (char_start,
char_end) into the brochure text that bound the Item's full narrative
section — the actual disclosure prose, not Table-of-Contents entries
and not body cross-references like "see Item 5 below."

Rules:

1. char_start should land at the first character of the section's
   substantive content (or its in-document section header).
2. char_end should land at the last character before the next Item's
   section begins, or at len(text) for the final Item.
3. If an Item's narrative is split across multiple program-specific
   subsections (e.g., a multi-program brochure where Item 5 is
   discussed separately for each program), return the span that
   covers ALL of those subsections in document order — start at the
   first program's Item-5 discussion, end before the next Item starts.
4. If you genuinely cannot find an Item's narrative anywhere in the
   text (the brochure simply omits it), do NOT include that Item in
   your response. The downstream system will surface the absence as a
   finding.
5. Return ONLY the requested Items. Do not invent spans for other
   Items.

Brochure text (length {len(text):,} chars):

<<<
{text}
>>>
"""


async def rescue_missing_items(
    text: str,
    segmented: SegmentedBrochure,
    llm_client: LLMClient,
    *,
    trace_id: str = "segmenter-fallback",
    brochure_crd: str | None = None,
    model: str | None = None,
) -> SegmentedBrochure:
    """Run the LLM rescue if any extractor-consumed Items are too small.

    Returns a new ``SegmentedBrochure`` with rescued sections merged in
    (replacing the regex's tiny/missing entries). The original is
    returned unchanged when no rescue is needed or when the LLM call
    fails (the regex result stays authoritative; the failure is logged
    + recorded in ``warnings``).
    """
    needed = _items_needing_rescue(segmented)
    if not needed:
        return segmented

    from adv_lens.app.settings import settings as default_settings

    chosen_model = model or default_settings.model_disciplinary  # Haiku 4.5

    try:
        response = await llm_client.extract(
            model=chosen_model,
            system=(
                "You are a span-identification assistant. You only return "
                "character offsets that you can verify by re-reading the "
                "supplied text. You do not invent content."
            ),
            prompt=_rescue_prompt(text, needed),
            response_model=_RescueResponse,
            trace_id=trace_id,
            node="segmenter_llm_fallback",
            brochure_crd=brochure_crd,
        )
    except LLMError as e:
        logger.warning("segmenter_llm_fallback: rescue call failed (regex result kept): %s", e)
        new_warnings = [
            *segmented.warnings,
            f"LLM rescue call failed for Items {needed}: {type(e).__name__}",
        ]
        return segmented.model_copy(update={"warnings": new_warnings})

    # Validate spans against text bounds + needed-Items allowlist.
    rescued: dict[int, Section] = {}
    text_len = len(text)
    for span in response.spans:
        if span.item_number not in needed:
            continue
        if not (0 <= span.char_start < span.char_end <= text_len):
            logger.warning(
                "segmenter_llm_fallback: dropping out-of-bounds span %s for Item %d",
                (span.char_start, span.char_end),
                span.item_number,
            )
            continue
        body = text[span.char_start : span.char_end]
        if len(body.strip()) < RESCUE_THRESHOLD_CHARS // 4:
            # Even the LLM couldn't find substantive content — don't
            # paper over with another tiny body.
            continue
        rescued[span.item_number] = Section(
            item_number=ItemNumber(span.item_number),
            title=span.title or ITEM_TITLES[span.item_number],
            body=body,
            char_start=span.char_start,
            char_end=span.char_end,
        )

    if not rescued:
        new_warnings = [
            *segmented.warnings,
            f"LLM rescue produced no usable spans for Items {needed}",
        ]
        return segmented.model_copy(update={"warnings": new_warnings})

    # Merge: keep the regex sections that weren't rescued, swap in the
    # rescued ones for Items the LLM successfully found.
    merged_sections: list[Section] = []
    for s in segmented.sections:
        n = int(s.item_number)
        if n in rescued:
            merged_sections.append(rescued.pop(n))
        else:
            merged_sections.append(s)
    # Items that the regex never produced at all but the LLM rescued.
    for n in sorted(rescued):
        merged_sections.append(rescued[n])
    merged_sections.sort(key=lambda s: int(s.item_number))

    rescued_items = sorted(
        int(s.item_number)
        for s in merged_sections
        if int(s.item_number) in needed
        and (
            int(s.item_number) not in {int(o.item_number) for o in segmented.sections}
            or len(s.body) >= RESCUE_THRESHOLD_CHARS
        )
    )
    new_warnings = [
        *segmented.warnings,
        f"LLM rescue replaced/added Items {rescued_items} (regex bodies were <{RESCUE_THRESHOLD_CHARS} chars)",
    ]
    new_missing = [
        i
        for i in segmented.missing_items
        if int(i) not in {int(s.item_number) for s in merged_sections}
    ]
    return segmented.model_copy(
        update={
            "sections": merged_sections,
            "missing_items": new_missing,
            "warnings": new_warnings,
            "backend": f"{segmented.backend}+llm_fallback",
        }
    )
