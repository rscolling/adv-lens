"""Ingestion CLI — ``python -m adv_lens.ingestion.cli``.

Subcommands:

* ``fetch-brochure <CRD> [--vid <BRCHR_VRSN_ID>]`` — download one brochure PDF.
  With ``--vid`` we skip the firm-search hop and go straight to the file server.
  Without, we resolve the CRD via IAPD search and fetch every current brochure.

* ``load-iard <csv_path>`` — stream a bulk Part 1 CSV and print per-row JSON.
  Week 2 pipes this into Qdrant; for now it's a validation-only dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from adv_lens.ingestion.iapd import IAPDClient
from adv_lens.ingestion.iard import IARDBulkLoader
from adv_lens.ingestion.models import BrochureRef


async def _fetch_brochure(crd: str, vid: str | None, force: bool) -> int:
    async with IAPDClient() as client:
        if vid is not None:
            refs = [BrochureRef(crd=crd, brochure_version_id=vid)]
        else:
            refs = await client.list_current_brochures(crd)
            if not refs:
                print(f"No current brochures found on IAPD for CRD={crd}", file=sys.stderr)
                return 1
        for ref in refs:
            result = await client.fetch_brochure(ref, force=force)
            print(
                json.dumps(
                    {
                        "crd": ref.crd,
                        "brochure_version_id": ref.brochure_version_id,
                        "path": str(result.pdf_path),
                        "bytes": result.bytes_downloaded,
                        "sha256": result.sha256,
                        "from_cache": result.from_cache,
                    }
                )
            )
    return 0


def _load_iard(csv_path: Path, limit: int | None) -> int:
    loader = IARDBulkLoader(csv_path)
    for i, row in enumerate(loader.iter_rows()):
        if limit is not None and i >= limit:
            break
        print(row.model_dump_json())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="adv-lens-ingest")
    sub = parser.add_subparsers(dest="command", required=True)

    fb = sub.add_parser("fetch-brochure", help="Fetch Part 2A brochure PDF(s) for a CRD.")
    fb.add_argument("crd")
    fb.add_argument("--vid", help="Specific BRCHR_VRSN_ID; skip CRD resolution.")
    fb.add_argument("--force", action="store_true", help="Bypass the on-disk cache.")

    li = sub.add_parser("load-iard", help="Stream a bulk Part 1 CSV as JSONL.")
    li.add_argument("csv_path", type=Path)
    li.add_argument("--limit", type=int, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fetch-brochure":
        return asyncio.run(_fetch_brochure(args.crd, args.vid, args.force))
    if args.command == "load-iard":
        return _load_iard(args.csv_path, args.limit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
