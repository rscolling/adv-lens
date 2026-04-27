"""Regex-based Part 2A Item segmenter.

Form ADV Part 2A is template-mandated: every compliant brochure contains
Items 1-18 in order, each introduced by a line that starts with ``Item N``
(case/punctuation varies). We detect those headers, slice the body between
consecutive headers, and return a ``SegmentedBrochure``.

Two practical wrinkles:

1. **Table of Contents pollutes matches.** Every brochure includes Item 3
   - Table of Contents, which itself contains every other Item's title.
   We dedupe by choosing the *last* occurrence of each Item header in the
   first 40% of the document and the *first* occurrence thereafter - the
   TOC is always near the top, so the real section header for Items 4+ is
   the later of the two.

2. **Formatting drift.** Some firms write ``ITEM 5``, others ``Item 5:``,
   others ``Item 5.``, and a few use an em-dash. The regex accepts all
   common variants; anything stranger ends up in ``warnings`` for a human
   to look at.
"""

from __future__ import annotations

import re
from pathlib import Path

from adv_lens.segmenter.base import Segmenter, SegmenterError
from adv_lens.segmenter.models import (
    ITEM_TITLES,
    ItemNumber,
    Section,
    SegmentedBrochure,
)
from adv_lens.segmenter.text_extract import extract_text_from_pdf

# Matches a line that starts with "Item N" (N in 1..18), optional punctuation,
# and optional same-line title text. MULTILINE + IGNORECASE. Unicode en/em
# dashes are intentional -- brochures use them as header separators.
_ITEM_HEADER_PATTERN = "^\\s*item\\s+(1[0-8]|[1-9])[\\s.:\\-–—)]+([^\\n]*)$"  # noqa: RUF001
ITEM_HEADER_RE = re.compile(_ITEM_HEADER_PATTERN, re.IGNORECASE | re.MULTILINE)


class HeuristicSegmenter(Segmenter):
    """Regex-based segmenter. Deterministic, offline, no LLM."""

    backend_name = "heuristic"

    def segment_pdf(self, pdf_path: Path) -> SegmentedBrochure:
        text = extract_text_from_pdf(pdf_path)
        return self.segment_text(text, source=str(pdf_path))

    def segment_text(self, text: str, *, source: str = "inline") -> SegmentedBrochure:
        if not text or not text.strip():
            raise SegmenterError("Empty text passed to segmenter")

        header_hits = _collect_header_hits(text)
        picked = _pick_real_headers(header_hits, total_chars=len(text))

        sections: list[Section] = []
        warnings: list[str] = []

        sorted_by_pos = sorted(picked.items(), key=lambda kv: kv[1][0])
        expected_order = [item for item, _ in sorted_by_pos]
        if expected_order != sorted(expected_order):
            warnings.append(
                f"Items detected out of canonical order: {[int(i) for i in expected_order]}"
            )

        for idx, (item, (start, header_end, title)) in enumerate(sorted_by_pos):
            next_start = sorted_by_pos[idx + 1][1][0] if idx + 1 < len(sorted_by_pos) else len(text)
            body = text[header_end:next_start]
            sections.append(
                Section(
                    item_number=item,
                    title=title or ITEM_TITLES[int(item)],
                    body=body,
                    char_start=start,
                    char_end=next_start,
                )
            )

        found = {s.item_number for s in sections}
        missing = [ItemNumber(n) for n in sorted(ITEM_TITLES) if ItemNumber(n) not in found]

        return SegmentedBrochure(
            source=source,
            total_chars=len(text),
            sections=sections,
            missing_items=missing,
            warnings=warnings,
            backend=self.backend_name,
        )


def _collect_header_hits(text: str) -> dict[ItemNumber, list[tuple[int, int, str]]]:
    """Return every (start, end, same-line-title) tuple per Item number."""
    hits: dict[ItemNumber, list[tuple[int, int, str]]] = {}
    for m in ITEM_HEADER_RE.finditer(text):
        n = int(m.group(1))
        title = m.group(2).strip().strip("-–—:.,")  # noqa: RUF001
        hits.setdefault(ItemNumber(n), []).append((m.start(), m.end(), title))
    return hits


def _pick_real_headers(
    hits: dict[ItemNumber, list[tuple[int, int, str]]],
    *,
    total_chars: int,
) -> dict[ItemNumber, tuple[int, int, str]]:
    """Pick the one true header per Item, filtering out Table-of-Contents noise.

    Heuristic: the TOC (Item 3) lives in the first ~40% of the document. A
    second occurrence of the same Item header *after* the TOC is the real
    section. If there's only one hit, we take it - some short brochures have
    no TOC expansion that repeats every Item name.
    """
    toc_cutoff = int(total_chars * 0.4)
    picked: dict[ItemNumber, tuple[int, int, str]] = {}
    for item, positions in hits.items():
        after_toc = [p for p in positions if p[0] >= toc_cutoff]
        if after_toc:
            picked[item] = after_toc[0]
        else:
            picked[item] = positions[0]
    return picked
