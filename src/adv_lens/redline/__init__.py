"""Per-brochure redline rendering — RedlineReport JSON → HTML / PDF.

The redline writer (`adv_lens.extractors.redline`) produces a
`RedlineReport` Pydantic object. This module renders it to a
CCO-readable 1-page HTML artifact (and optional PDF) suitable for
emailing or printing. The JSON is the source of truth; the HTML is the
view layer.
"""

from adv_lens.redline.render import render_redline_html, render_redline_pdf

__all__ = ["render_redline_html", "render_redline_pdf"]
