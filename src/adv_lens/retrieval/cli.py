"""Retrieval CLI — ``python -m adv_lens.retrieval.cli``.

Subcommands:

* ``seed-peers <peers.json>`` — run the pipeline per CRD and index the
  resulting Item sections into Qdrant. Idempotent.
* ``query "<text>" [--item N] [--aum-band BAND] [--exclude-crd CRD] [-k 5]``
  — semantic search over the peer corpus. Useful for sanity-checking the
  index before week-2 extractor wiring.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from adv_lens.retrieval.qdrant_store import make_peer_store
from adv_lens.retrieval.seed import (
    load_peer_specs,
    report_to_markdown,
    seed_peers,
    write_report,
)


async def _seed(specs_path: Path, report_out: Path | None, fail_fast: bool) -> int:
    specs = load_peer_specs(specs_path)
    # Seed always uses the hybrid-capable store so sparse vectors get written.
    store = make_peer_store(hybrid=True, rerank=False)
    report = await seed_peers(specs, store, fail_fast=fail_fast)
    if report_out is not None:
        write_report(report_out, report)
    print(report_to_markdown(report))
    failed = sum(1 for entry in report.values() if entry.get("errors"))
    return 0 if failed == 0 else 1


def _query(
    text: str,
    item: int | None,
    aum_band: str | None,
    state_code: str | None,
    exclude_crd: str | None,
    k: int,
    hybrid: bool,
    rerank: bool,
) -> int:
    store = make_peer_store(hybrid=hybrid, rerank=rerank)
    hits = store.query_peers(
        text,
        item_number=item,
        aum_band=aum_band,
        main_office_state=state_code,
        exclude_crd=exclude_crd,
        k=k,
        hybrid=hybrid,
        rerank=rerank,
    )
    print(json.dumps([h.model_dump() for h in hits], indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="adv-lens-retrieval")
    sub = p.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed-peers", help="Index brochures listed in a JSON peer-spec file.")
    seed.add_argument("peers_file", type=Path)
    seed.add_argument("--report-out", type=Path, default=None)
    seed.add_argument("--fail-fast", action="store_true")

    q = sub.add_parser("query", help="Semantic search over the peer corpus.")
    q.add_argument("text")
    q.add_argument("--item", type=int, default=None, help="Item number 1-18.")
    q.add_argument("--aum-band", default=None)
    q.add_argument("--state", dest="state_code", default=None, help="Main office state.")
    q.add_argument("--exclude-crd", default=None)
    q.add_argument("-k", type=int, default=5)
    q.add_argument("--hybrid", action="store_true", help="Dense + BM25 sparse with RRF fusion.")
    q.add_argument(
        "--no-rerank",
        dest="rerank",
        action="store_false",
        default=True,
        help="Skip the cross-encoder reranking pass.",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "seed-peers":
        return asyncio.run(_seed(args.peers_file, args.report_out, args.fail_fast))
    if args.command == "query":
        return _query(
            args.text,
            args.item,
            args.aum_band,
            args.state_code,
            args.exclude_crd,
            args.k,
            args.hybrid,
            args.rerank,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
