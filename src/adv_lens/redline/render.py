"""Render a `RedlineReport` to HTML and (optionally) PDF.

Two functions:

* ``render_redline_html(report, *, meta=None) -> str`` — Jinja2 template
  → standalone HTML string. Self-contained CSS in the template; no
  external assets. Safe to email or attach.
* ``render_redline_pdf(html, *, out_path) -> Path`` — Chrome
  ``--headless --print-to-pdf`` to produce a printable PDF from the
  HTML. Mirrors the user-manual build pipeline (no LaTeX, no
  wkhtmltopdf, no weasyprint deps — just the Chrome that ships on every
  dev box).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from adv_lens.extractors.schemas import RedlineReport

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "report.html.j2"


def _score_colour(score: int) -> str:
    """Return a CSS colour for a 0-100 score."""
    if score >= 90:
        return "#198754"  # green
    if score >= 80:
        return "#52a35d"
    if score >= 70:
        return "#a07f1f"  # amber
    if score >= 60:
        return "#c47f1f"
    if score >= 50:
        return "#cc6e2a"  # orange
    return "#b32e2e"  # red


def _score_bg_top(score: int) -> str:
    if score >= 80:
        return "#e7f5ec"
    if score >= 60:
        return "#fff5e0"
    return "#fce8e8"


def _score_bg_bottom(score: int) -> str:
    if score >= 80:
        return "#cfeeda"
    if score >= 60:
        return "#fde6c2"
    return "#f7c8c8"


_SEV_COLOURS = {
    "critical": "#7f1d1d",
    "high": "#b32e2e",
    "medium": "#cc6e2a",
    "low": "#a07f1f",
    "info": "#5b6770",
}
_SEV_BACKGROUNDS = {
    "critical": "#fde2e2",
    "high": "#fce8e8",
    "medium": "#fdebd5",
    "low": "#fdf3d4",
    "info": "#eef0f3",
}


def _severity_colour(sev: str) -> str:
    return _SEV_COLOURS.get((sev or "").lower(), "#5b6770")


def _severity_bg(sev: str) -> str:
    return _SEV_BACKGROUNDS.get((sev or "").lower(), "#eef0f3")


def render_redline_html(
    report: RedlineReport,
    *,
    meta: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render a `RedlineReport` to a standalone HTML string."""
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(_TEMPLATE_NAME)
    when = generated_at or datetime.now(UTC)
    return template.render(
        report=report,
        meta=meta or {},
        generated_at=when.strftime("%Y-%m-%d %H:%M UTC"),
        score_colour=_score_colour,
        score_bg_top=_score_bg_top,
        score_bg_bottom=_score_bg_bottom,
        severity_colour=_severity_colour,
        severity_bg=_severity_bg,
    )


def render_redline_pdf(
    html: str,
    *,
    out_path: Path,
    chrome_path: str | None = None,
) -> Path:
    """Render the HTML string to PDF via headless Chrome.

    Writes ``html`` to a temp file, invokes Chrome with
    ``--print-to-pdf=<out_path>``, deletes the temp file, returns
    ``out_path``. Raises ``RuntimeError`` if no Chrome / Edge binary is
    found or the conversion produces no PDF.
    """
    chrome = chrome_path or _find_chrome()
    if chrome is None:
        raise RuntimeError(
            "No Chrome/Edge binary found for PDF rendering. Install Chrome or pass chrome_path=..."
        )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", encoding="utf-8", delete=False) as f:
        tmp_html = Path(f.name)
        f.write(html)
    try:
        # Chrome's --print-to-pdf wants Windows-native absolute paths
        # on Windows; resolve and pass straight through.
        out_abs = str(out_path.resolve())
        url = tmp_html.resolve().as_uri()
        result = subprocess.run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={out_abs}",
                "--virtual-time-budget=5000",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if not out_path.exists():
            raise RuntimeError(
                f"Chrome did not produce PDF at {out_path}. stderr: {result.stderr[:500]}"
            )
    finally:
        with contextlib.suppress(OSError):
            tmp_html.unlink(missing_ok=True)
    return out_path


def _find_chrome() -> str | None:
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for cand in candidates:
        if Path(cand).exists():
            return cand
    # Fall back to PATH lookup
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        path = shutil.which(name)
        if path:
            return path
    return None
