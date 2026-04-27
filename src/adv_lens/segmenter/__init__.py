"""Form ADV Part 2A section segmenter.

Splits a brochure PDF into the 18 item sections mandated by SEC Form ADV
Part 2A General Instructions (Item 1 — Cover Page, ... Item 18 — Financial
Information) plus any Appendices (wrap-fee programs file Appendix 1).

Pluggable backends via the ``Segmenter`` protocol:

- ``HeuristicSegmenter`` — regex on canonical Item headers; the default,
  deterministic, offline-testable.
- ``LlamaParseSegmenter`` — fallback for PDFs the heuristic can't segment
  (deferred — placeholder raises NotImplementedError until Week 2).

See ADR 0003 for why heuristic-first beats sec-parser here.
"""

from adv_lens.segmenter.base import Segmenter, SegmenterError
from adv_lens.segmenter.heuristic import HeuristicSegmenter
from adv_lens.segmenter.models import ItemNumber, Section, SegmentedBrochure

__all__ = [
    "HeuristicSegmenter",
    "ItemNumber",
    "Section",
    "SegmentedBrochure",
    "Segmenter",
    "SegmenterError",
]
