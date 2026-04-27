"""CLI: render a saved RedlineReport JSON to HTML and (optionally) PDF.

Usage::

    uv run python -m adv_lens.redline.cli docs/examples/sample-report.json
    uv run python -m adv_lens.redline.cli docs/examples/sample-report.json --pdf

The input JSON may be either a bare ``RedlineReport`` payload OR the
trimmed wrapper used in ``docs/examples/sample-report.json`` (i.e.
``{"meta": {...}, "redline": {...}}``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adv_lens.extractors.schemas import RedlineReport
from adv_lens.redline.render import render_redline_html, render_redline_pdf


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adv-lens-redline-render")
    p.add_argument("source", type=Path, help="Path to RedlineReport JSON or wrapped sample-report JSON")
    p.add_argument(
        "--out", type=Path, default=None,
        help="Output HTML path (default: <source>.html next to the source)",
    )
    p.add_argument("--pdf", action="store_true", help="Also render PDF via headless Chrome")
    args = p.parse_args(argv)

    raw = json.loads(args.source.read_text(encoding="utf-8"))
    meta = raw.get("meta") if isinstance(raw, dict) and "redline" in raw else None
    payload = raw["redline"] if meta is not None else raw
    report = RedlineReport.model_validate(payload)

    out_html = args.out or args.source.with_suffix(".html")
    html = render_redline_html(report, meta=meta)
    out_html.write_text(html, encoding="utf-8")
    print(f"wrote {out_html} ({out_html.stat().st_size} bytes)")

    if args.pdf:
        out_pdf = out_html.with_suffix(".pdf")
        render_redline_pdf(html, out_path=out_pdf)
        print(f"wrote {out_pdf} ({out_pdf.stat().st_size} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
