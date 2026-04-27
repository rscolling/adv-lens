"""Segmenter protocol + shared errors."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from adv_lens.segmenter.models import SegmentedBrochure


class SegmenterError(Exception):
    """Raised when a segmenter cannot produce a usable segmentation."""


@runtime_checkable
class Segmenter(Protocol):
    """Anything that turns a brochure PDF into a SegmentedBrochure.

    Backends are free to call out to external services (LlamaParse) or
    operate purely offline (HeuristicSegmenter). The protocol is deliberately
    narrow so the LangGraph node can swap backends via config without
    knowing which one is active.
    """

    def segment_pdf(self, pdf_path: Path) -> SegmentedBrochure: ...

    def segment_text(self, text: str, *, source: str = "inline") -> SegmentedBrochure: ...
