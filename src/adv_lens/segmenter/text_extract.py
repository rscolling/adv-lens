"""PDF → text extraction.

Thin wrapper around pypdf. Deliberately minimal: we don't try to recover
layout, preserve tables, or OCR scanned brochures. Brochures filed on SEC
IAPD are overwhelmingly born-digital PDFs with extractable text. The small
fraction that aren't route to the LlamaParse fallback (ADR 0003).
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from adv_lens.segmenter.base import SegmenterError


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Return concatenated page text. Pages joined by a single newline.

    Raises SegmenterError when the PDF yields no extractable text — this is
    the signal for the caller to fall back to LlamaParse or OCR.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        reader = PdfReader(str(path))
    except Exception as e:  # pypdf raises its own hierarchy; keep this boundary simple
        raise SegmenterError(f"pypdf failed to open {path.name}: {e}") from e

    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise SegmenterError(
            f"No extractable text in {path.name}. Likely a scanned PDF; route to LlamaParse fallback."
        )
    return text
