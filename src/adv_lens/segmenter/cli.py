"""Segmenter CLI — ``python -m adv_lens.segmenter.cli <pdf_path>``.

Prints the segmentation result as JSON. Bodies are truncated for readability
by default; pass ``--full`` to emit the complete section text.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from adv_lens.segmenter.heuristic import HeuristicSegmenter


def _to_json(segmented, *, full: bool) -> dict:
    data = segmented.model_dump(mode="json")
    if not full:
        for s in data["sections"]:
            body = s["body"]
            if len(body) > 240:
                s["body"] = body[:240] + f"\n… [{len(body) - 240} more chars]"
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adv-lens-segment")
    parser.add_argument("pdf_path", type=Path, help="Path to a Form ADV Part 2A brochure PDF.")
    parser.add_argument("--full", action="store_true", help="Emit full section bodies.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    segmenter = HeuristicSegmenter()
    segmented = segmenter.segment_pdf(args.pdf_path)
    print(json.dumps(_to_json(segmented, full=args.full), indent=2))
    return 0 if not segmented.missing_items else 1


if __name__ == "__main__":
    sys.exit(main())
