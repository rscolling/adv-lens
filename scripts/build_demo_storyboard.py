"""Build a 4-panel storyboard PNG showing the ADV-Lens demo flow.

Output: docs/images/demo-storyboard.png

Run with: uv run python scripts/build_demo_storyboard.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

LINE_CONTINUATION = " \\"


def make_terminal_panel(width: int, height: int, lines: list[tuple[str, str]]) -> Image.Image:
    img = Image.new("RGB", (width, height), "#1e1e2e")
    draw = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 22)
        font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 16)
    except OSError:
        title_font = ImageFont.load_default()
        font = ImageFont.load_default()
    palette = {
        "prompt":  "#a6e3a1",
        "out":     "#cdd6f4",
        "comment": "#7f849c",
        "json":    "#f9e2af",
        "title":   "#89b4fa",
    }
    y = 16
    for tag, text in lines:
        color = palette.get(tag, "#cdd6f4")
        f = title_font if tag == "title" else font
        for sub in text.split("\n"):
            draw.text((20, y), sub, fill=color, font=f)
            y += 30 if tag == "title" else 22
        y += 4
    return img


def screenshot_panel(path: str, width: int, height: int) -> Image.Image:
    src = Image.open(path).convert("RGB")
    src.thumbnail((width - 20, height - 20))
    canvas = Image.new("RGB", (width, height), "#f4f4f7")
    canvas.paste(src, ((width - src.width) // 2, (height - src.height) // 2))
    return canvas


def main() -> None:
    pw, ph, gap, margin = 900, 700, 20, 30
    p1 = screenshot_panel("docs/images/iapd-brown-advisory-summary.png", pw, ph)

    p2_lines = [
        ("title", "1. Fetch the brochure + run the pipeline"),
        ("comment", "# Resolve CRD via IAPD search + cache the Part 2A PDF"),
        ("prompt", "$ uv run python -m adv_lens.ingestion.cli" + LINE_CONTINUATION),
        ("prompt", "      fetch-brochure 110181"),
        ("json", '{"crd": "110181", "brochure_version_id": "1037550",'),
        ("json", ' "bytes": 666759, "from_cache": false}'),
        ("comment", ""),
        ("comment", "# Run the full LangGraph pipeline (~60s)"),
        ("prompt", "$ uv run python -m adv_lens.app.graph.cli" + LINE_CONTINUATION),
        ("prompt", "      110181 --vid 1037550" + LINE_CONTINUATION),
        ("prompt", "      --trace-id brown-advisory-rescued"),
        ("out", "... fetch_brochure  OK"),
        ("out", "... segment_brochure  OK (heuristic+llm_fallback)"),
        ("out", "... extract_fee | extract_disciplinary |"),
        ("out", "    extract_conflicts (parallel)  OK"),
        ("out", "... retrieve_peers  OK"),
        ("out", "... write_redline (Opus 4.7)  OK"),
        ("out", "... hitl_gate -> review_status: pending_review"),
        ("json", "overall_score: 68   findings: 11"),
        ("json", "trace: brown-advisory-rescued"),
    ]
    p2 = make_terminal_panel(pw, ph, p2_lines)
    p3 = screenshot_panel("docs/images/sample-redline-page1-preview.png", pw, ph)

    p4_lines = [
        ("title", "2. CCO reviews -> records the decision"),
        ("comment", "# Render the report to HTML+PDF for the CCO to read"),
        ("prompt", "$ uv run python -m adv_lens.redline.cli" + LINE_CONTINUATION),
        ("prompt", "      docs/examples/sample-report.json --pdf"),
        ("out", "wrote sample-report.html (21,084 bytes)"),
        ("out", "wrote sample-report.pdf (152,164 bytes)"),
        ("comment", ""),
        ("comment", "# CCO posts decision -> writes audit row"),
        ("prompt", "$ curl -X POST .../report/decision" + LINE_CONTINUATION),
        ("prompt", "      -H 'content-type: application/json'" + LINE_CONTINUATION),
        ("prompt", "      -d '{\"trace_id\":\"brown-advisory-rescued\","),
        ("prompt", "            \"brochure_crd\":\"110181\","),
        ("prompt", "            \"report_hash\":\"<from state>\","),
        ("prompt", "            \"reviewer\":\"jane.cco@firm.example\","),
        ("prompt", "            \"decision\":\"revise_requested\","),
        ("prompt", "            \"rationale\":\"Confirm Items 11/12 spans\"}'"),
        ("json", '{"id": 1, "decision": "revise_requested",'),
        ("json", ' "report_hash": "9a7c...",'),
        ("json", ' "created_at": "2026-04-26T..."}'),
        ("comment", ""),
        ("comment", "# Audit trail in Postgres:"),
        ("comment", "# 6 rows llm_calls, 1 human_reviews, 1 pipeline_runs."),
    ]
    p4 = make_terminal_panel(pw, ph, p4_lines)

    w_total = pw * 2 + gap + margin * 2
    h_total = ph * 2 + gap + margin * 2 + 80
    canvas = Image.new("RGB", (w_total, h_total), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        big_font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 32)
    except OSError:
        big_font = ImageFont.load_default()
    draw.text(
        (margin, 20),
        "ADV-Lens demo flow - Brown Advisory LLC (CRD 110181)",
        fill="#1e1e2e",
        font=big_font,
    )

    top = 80
    canvas.paste(p1, (margin, top))
    canvas.paste(p2, (margin + pw + gap, top))
    canvas.paste(p3, (margin, top + ph + gap))
    canvas.paste(p4, (margin + pw + gap, top + ph + gap))

    for col in range(2):
        for row in range(2):
            x = margin + col * (pw + gap)
            y = top + row * (ph + gap)
            ImageDraw.Draw(canvas).rectangle(
                [x, y, x + pw, y + ph], outline="#cccccc", width=2
            )

    out_path = Path("docs/images/demo-storyboard.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG", optimize=True)
    print(f"wrote {out_path}: {canvas.size[0]}x{canvas.size[1]}, {out_path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
