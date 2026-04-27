"""Golden-set eval runner.

Discovers fixtures under eval/fixtures/<section_type>/*.json, runs the
registered scorer for each section_type, writes report.json + report.md to
eval/results/<run_id>/.

In weeks 2-4 each section_type gets a real scorer and a real pipeline call;
for now the smoke scorer just checks fixture == synthetic 'actual' so CI
has a green signal on day 1.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from adv_lens.app.settings import settings as default_settings
from adv_lens.segmenter import HeuristicSegmenter
from eval.schemas import EvalReport, GoldenItem, ScoreResult
from eval.scorers import get_scorer

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESULTS_DIR = Path(__file__).parent / "results"

# Section types that need a live Anthropic key to produce an "actual."
LLM_BACKED_SECTION_TYPES = frozenset({"fee", "disciplinary", "conflicts", "redline"})


def load_fixtures(section_type: str | None = None) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    types = (
        [section_type] if section_type else [d.name for d in FIXTURES_DIR.iterdir() if d.is_dir()]
    )
    for st in types:
        for path in sorted((FIXTURES_DIR / st).glob("*.json")):
            items.append(GoldenItem.model_validate_json(path.read_text(encoding="utf-8")))
    return items


def run_pipeline_stub(item: GoldenItem) -> dict | None:
    """Dispatch fixture → real node output. Returns None to signal 'skip'.

    Segmenter runs live against the heuristic backend. LLM-backed types
    (fee / disciplinary / conflicts / redline) require an Anthropic key —
    the runner skips them with a logged note when the key is missing.
    """
    import asyncio

    if item.section_type == "smoke":
        return dict(item.expected)
    if item.section_type == "segmenter":
        text = item.inputs.get("text") or ""
        segmented = HeuristicSegmenter().segment_text(text, source=item.id)
        return {"items_found": [int(n) for n in segmented.items_found]}
    if item.section_type == "fee":
        if not default_settings.anthropic_api_key:
            return None
        return asyncio.run(_run_fee(item))
    if item.section_type == "disciplinary":
        if not default_settings.anthropic_api_key:
            return None
        return asyncio.run(_run_disciplinary(item))
    if item.section_type == "conflicts":
        if not default_settings.anthropic_api_key:
            return None
        return asyncio.run(_run_conflicts(item))
    if item.section_type == "redline":
        if not default_settings.anthropic_api_key:
            return None
        return asyncio.run(_run_redline(item))
    return None


async def _run_fee(item: GoldenItem) -> dict:
    from adv_lens.extractors.fee import FeeExtractor
    from adv_lens.llm.client import make_llm_client

    extractor = FeeExtractor(make_llm_client())
    section_body = item.inputs.get("section_body") or ""
    result = await extractor.extract(
        section_body, trace_id=f"eval-{item.id}", brochure_crd=item.brochure_crd
    )
    return result.model_dump(mode="json")


async def _run_disciplinary(item: GoldenItem) -> dict:
    from adv_lens.extractors.disciplinary import DisciplinaryExtractor
    from adv_lens.llm.client import make_llm_client

    extractor = DisciplinaryExtractor(make_llm_client())
    section_body = item.inputs.get("section_body") or ""
    result = await extractor.extract(
        section_body, trace_id=f"eval-{item.id}", brochure_crd=item.brochure_crd
    )
    return result.model_dump(mode="json")


async def _run_conflicts(item: GoldenItem) -> dict:
    from adv_lens.extractors.conflicts import ConflictsExtractor
    from adv_lens.llm.client import make_llm_client

    extractor = ConflictsExtractor(make_llm_client())
    result = await extractor.extract(
        item.inputs.get("item_10_body"),
        item.inputs.get("item_11_body"),
        item.inputs.get("item_12_body"),
        trace_id=f"eval-{item.id}",
        brochure_crd=item.brochure_crd,
    )
    return result.model_dump(mode="json")


async def _run_redline(item: GoldenItem) -> dict:
    from adv_lens.extractors.redline import RedlineWriter
    from adv_lens.extractors.schemas import Extractions
    from adv_lens.llm.client import make_llm_client

    writer = RedlineWriter(make_llm_client())
    extractions = Extractions.model_validate(item.inputs.get("extractions") or {})
    peer_context = item.inputs.get("peer_context") or []
    result = await writer.write(
        crd=item.brochure_crd,
        brochure_version_id=item.inputs.get("brochure_version_id"),
        extractions=extractions,
        peer_context=peer_context,
        trace_id=f"eval-{item.id}",
    )
    return result.model_dump(mode="json")


def run(section_type: str | None = None) -> EvalReport:
    started = datetime.now(UTC)
    items = load_fixtures(section_type)
    if not items:
        raise RuntimeError(f"No fixtures found under {FIXTURES_DIR}")

    results: list[ScoreResult] = []
    skipped_items: list[str] = []
    for item in items:
        try:
            actual = run_pipeline_stub(item)
        except Exception as e:
            # A single fixture blowing up (LLM error, validation error, …)
            # must not abort the whole run — record it as a hard failure and
            # keep going so the report still gets written.
            results.append(
                ScoreResult(
                    item_id=item.id,
                    section_type=item.section_type,
                    score=0.0,
                    passed=False,
                    detail={"error": f"{type(e).__name__}: {e}"},
                )
            )
            continue
        if actual is None:
            skipped_items.append(item.id)
            continue
        scorer = get_scorer(item.section_type)
        results.append(scorer(item, actual))

    by_section: dict[str, dict] = defaultdict(lambda: {"total": 0, "passed": 0, "scores": []})
    for r in results:
        by_section[r.section_type]["total"] += 1
        by_section[r.section_type]["passed"] += int(r.passed)
        by_section[r.section_type]["scores"].append(r.score)
    for agg in by_section.values():
        scores = agg.pop("scores")
        agg["mean_score"] = sum(scores) / len(scores) if scores else 0.0

    report = EvalReport(
        run_id=started.strftime("%Y%m%dT%H%M%SZ"),
        started_at=started,
        completed_at=datetime.now(UTC),
        total=len(results),
        passed=sum(r.passed for r in results),
        skipped=len(skipped_items),
        mean_score=sum(r.score for r in results) / len(results) if results else 0.0,
        by_section=dict(by_section),
        results=results,
        skipped_items=skipped_items,
    )

    out_dir = RESULTS_DIR / report.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    return report


def _render_markdown(report: EvalReport) -> str:
    lines = [
        f"# ADV-Lens eval — {report.run_id}",
        "",
        f"- total: **{report.total}**",
        f"- passed: **{report.passed}**",
        f"- skipped: **{report.skipped}** (no Anthropic key, or no scorer)",
        f"- mean score: **{report.mean_score:.3f}**",
        "",
        "## By section",
        "",
        "| section | total | passed | mean score |",
        "| --- | ---: | ---: | ---: |",
    ]
    for section, agg in sorted(report.by_section.items()):
        lines.append(f"| {section} | {agg['total']} | {agg['passed']} | {agg['mean_score']:.3f} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    section = sys.argv[1] if len(sys.argv) > 1 else None
    r = run(section)
    print(
        json.dumps(
            {
                "total": r.total,
                "passed": r.passed,
                "skipped": r.skipped,
                "mean_score": r.mean_score,
            },
            indent=2,
        )
    )
    if r.passed < r.total:
        sys.exit(1)
