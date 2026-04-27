"""Pipeline CLI — ``python -m adv_lens.app.graph.cli <CRD> [--vid <ID>]``.

Runs the full LangGraph pipeline (currently fetch + segment) on one CRD and
prints the final ``ADVState`` as JSON. Useful for smoke-testing the wiring
without spinning up FastAPI.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from adv_lens.app.graph.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="adv-lens-pipeline")
    p.add_argument("crd")
    p.add_argument("--vid", help="Specific BRCHR_VRSN_ID; skip CRD resolution.")
    p.add_argument("--trace-id", help="Explicit trace ID; default is auto-generated.")
    return p


async def _run(crd: str, vid: str | None, trace_id: str | None) -> int:
    state = await run_pipeline(crd, brochure_version_id=vid, trace_id=trace_id)
    print(state.model_dump_json(indent=2))
    return 0 if not state.errors else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(_run(args.crd, args.vid, args.trace_id))


if __name__ == "__main__":
    sys.exit(main())
