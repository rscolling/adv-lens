# ADV-Lens demo playbook

A 60-90s screen recording of the full ADV-Lens flow against Brown
Advisory LLC, ending with a CCO decision. Designed to be captured
in one take by the operator (every step is deterministic given the
cached brochure + a real `ANTHROPIC_API_KEY`).

A still-frame approximation of what the recording covers is in
[`docs/images/demo-storyboard.png`](images/demo-storyboard.png) — 4
panels in a 2x2 grid (IAPD page, pipeline terminal, rendered redline,
HITL decision).

## Recording setup

- Window: ~1280x900, OBS / ScreenToGif / Kap, output to MP4 or GIF
  (≤8 MB GIF for direct README embed).
- One terminal pane on the right, browser pane on the left.
- Repo root open in the terminal; `.env` already configured with
  `ANTHROPIC_API_KEY` (and optionally `LANGFUSE_*`).
- Brown Advisory brochure already cached at
  `data/brochures/110181/1037550.pdf` (skip step 1 below if running
  the recording multiple times).

## Tool walkthrough — ScreenToGif on Windows (recommended)

ScreenToGif is the one-tool option for Windows: record → trim → optimise
→ save as GIF in a single application. Free, ~5 MB download, no install
required (portable ZIP available).

1. **Install once.** Download from <https://www.screentogif.com/>
   (stable installer or portable ZIP). No license needed for personal
   use; MIT/MS-PL licensed.
2. **Launch → Recorder.** This drops a transparent capture window on
   the desktop. Drag the corners to enclose the demo area
   (~1280×900). Settings to set before the first capture:
   - **FPS:** 15 (good GIF compromise; 24 if you have spare bytes).
   - **Capture mode:** "Capture using DirectX" if available, else
     default GDI.
   - **Frame trigger:** "On frame change" keeps file size down.
3. **Stage the demo area.** Position your two-pane layout (terminal
   right, browser/PDF viewer left) entirely inside the ScreenToGif
   capture rectangle. Open the IAPD URL in the browser pane *before*
   you hit Record so the page is settled.
4. **Record.** Press **F7** (or click the Record button). Run the
   playbook steps below in sequence; press **F8** to stop. Total
   capture: 60-90 seconds.
5. **Trim in the editor.** ScreenToGif's editor opens automatically
   with the captured frames. Use the timeline to:
   - delete the first/last few hundred milliseconds (mouse settling,
     stop-button click),
   - `Edit → Reduce Frame Rate` if the file is over 8 MB (drop to
     10 fps; rarely needed for 60-90s of mostly-text recording).
6. **Save as GIF.**
   - `File → Save As → Gif`
   - Encoder: **FFmpeg** if installed, else built-in **System** encoder
     is fine.
   - Loop forever, no last-frame delay.
   - Save to `docs/demo.gif` in this repo.
7. **Wire into README.** Replace the storyboard image reference in the
   README "Demo" section:
   ```markdown
   ![ADV-Lens demo](docs/demo.gif)
   ```
   Keep the storyboard PNG link as a fallback for skim-readers.

### Fallback — Windows 11 Snipping Tool (built-in, MP4 only)

If you can't install third-party software:

1. Press **Win+Shift+S** → click the **Record** icon (square camera).
2. Drag the capture rectangle, click **Start**, run the playbook,
   click **Stop** in the system tray.
3. Save the MP4 anywhere.
4. Convert MP4 → GIF using ffmpeg (`uv tool install
   ffmpeg-python` won't help — install ffmpeg standalone):
   ```bash
   ffmpeg -i demo.mp4 -vf "fps=15,scale=1280:-1:flags=lanczos" \
       -loop 0 docs/demo.gif
   ```
5. Optimise GIF size with `gifsicle -O3 docs/demo.gif -o docs/demo.gif`
   if it overshoots 8 MB.

The MP4 path adds two install steps (Snipping Tool is built in;
ffmpeg + gifsicle are not) but works without any GUI tool. ScreenToGif
is the more practical Windows-native choice if you'll do this more
than once.

### Embedding MP4 instead of GIF

GitHub renders MP4 inline in markdown via HTML5 video tags:

```markdown
<video src="docs/demo.mp4" controls width="800"></video>
```

MP4 is smaller per second of recording and supports audio (not used
here, but option-preserving). GIF is more universally embedded — it
shows in raw repository views, in `git log` mirrors, and in
README-rendered Slack previews. **Default to GIF for this README.**

### What "good" looks like

Reviewers will skim, not watch. Optimise for:

- The IAPD page is recognisable as an SEC page in the first second.
- The pipeline run shows *some* streaming output (don't let the screen
  freeze for 30 s on a black terminal — either record at lower FPS
  while pipeline runs, or cut to the result).
- The redline PDF is on screen for at least 10 s — long enough to
  read the headline, see the score gauge, and notice the colour-coded
  finding cards.
- The HITL `curl` and the 201 response are both visible.

## Capture script (60-90 seconds total)

### t=0:00–0:10 — Open IAPD firm summary in browser

Navigate to `https://adviserinfo.sec.gov/firm/summary/110181`. Show
the firm name (BROWN ADVISORY), CRD (110181), SEC# (801-38826), and
the **PART 2 BROCHURES** button. *Voiceover-equivalent caption:* "Any
SEC-registered RIA's brochure is here, identified by CRD."

### t=0:10–0:25 — Fetch the brochure

In the terminal:

```bash
uv run python -m adv_lens.ingestion.cli fetch-brochure 110181
```

Expected output (one JSON line):
```json
{"crd": "110181", "brochure_version_id": "1037550",
 "path": "data/brochures/110181/1037550.pdf",
 "bytes": 666759, "from_cache": true,
 "sha256": "4492c6704f63ebec..."}
```

*Caption:* "ADV-Lens fetches the Part 2A PDF and caches it
content-addressed."

### t=0:25–0:55 — Run the full pipeline

```bash
uv run python -m adv_lens.app.graph.cli 110181 --vid 1037550 \
    --trace-id demo-$(date +%s)
```

The pipeline runs ~30-60 seconds. Recording can show the JSON output
streaming or just cut to the end. *Caption:* "fetch → segment → 3
extractors in parallel → peer retrieval → Opus redline writer → HITL
gate." Highlight the `segmenter_backend: heuristic+llm_fallback` line
in the output (proves the ADR 0014 rescue ran), the `overall_score`,
and `review_status: "pending_review"`.

### t=0:55–1:10 — Render the report for the CCO

```bash
uv run python -m adv_lens.redline.cli docs/examples/sample-report.json --pdf
```

Open the resulting `docs/examples/sample-report.pdf` in the PDF viewer
pane. Show the score gauge, headline, category table, severity-coloured
finding cards. *Caption:* "The CCO reads a 4-page artifact, not a JSON
blob."

### t=1:10–1:30 — Record the CCO decision

In the terminal:

```bash
curl -X POST http://localhost:8000/report/decision \
    -H 'content-type: application/json' \
    -d '{
      "trace_id": "demo-...",
      "brochure_crd": "110181",
      "report_hash": "<copied from state>",
      "reviewer": "jane.cco@firm.example",
      "decision": "revise_requested",
      "rationale": "Confirm Items 11 and 12 spans before next amendment."
    }'
```

Show the 201 response with the audit row id. *Caption:* "One row in
`human_reviews`. Pinned to the report's SHA-256 so the approval
binds to the exact bytes the CCO read."

## Where the GIF / MP4 lands

- `docs/demo.gif` (or `docs/demo.mp4`) — referenced from the README
  "Sample output" section. Replaces the storyboard PNG link once the
  capture is recorded.
- 8 MB GIF or sub-30 MB MP4 keeps the README load-time sensible.

## Why a script, not a screencast-from-CI

A scripted screen capture means the recording matches the actual
operator-controlled flow — including the Anthropic spend (~$1 per take)
and the deliberate pause at the HITL gate. Automating this in CI would
either bypass the gate (defeating the demo's point) or cost real money
on every PR.

The storyboard PNG covers the case where a hiring manager skims the
README without watching the GIF — every claim the recording would make
is visible as text + image in the four panels.
