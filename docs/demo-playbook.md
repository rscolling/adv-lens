# ADV-Lens demo playbook

A 60-90s screen recording of the full ADV-Lens flow against Brown
Advisory LLC, ending with a CCO decision recorded through the reviewer
UI ([ADR 0016](adr/0016-review-ui.md)). Designed to be captured in
one take by the operator (every step is deterministic given the cached
brochure + a real `ANTHROPIC_API_KEY`).

A still-frame approximation of what the recording covers is in
[`docs/images/demo-storyboard.png`](images/demo-storyboard.png) — 4
panels in a 2x2 grid (IAPD page, pipeline running, reviewer UI with
redline, HITL decision recorded).

## Recording setup

- Window: ~1440x900, OBS / ScreenToGif / Kap, output to MP4 or GIF
  (≤8 MB GIF for direct README embed).
- Browser pane (full-width works fine — the UI is the star).
- Repo root open in a terminal in the background to kick the pipeline
  off; the recording focuses on the browser.
- `.env` already configured with `ANTHROPIC_API_KEY` (and optionally
  `LANGFUSE_*`).
- Brown Advisory brochure already cached at
  `data/brochures/110181/1037550.pdf`.
- App stack already up: `docker compose up -d postgres qdrant` plus
  `uv run uvicorn adv_lens.app.main:app --reload` running on
  `localhost:8000`.
- Demo data seeded: `uv run python -m adv_lens.app.web.seed` (one-shot,
  idempotent — inserts **two** rows into `pipeline_runs`: the original
  Brown Advisory filed run plus a draft-shaped companion that flows
  through the upload code path with a synthetic `99`-prefixed CRD).
  Both visible in the dashboard before recording.

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

The recording is browser-centric; the terminal is a quick aside in
the middle. Two browser tabs (or one tab with back-button history):

1. `https://adviserinfo.sec.gov/firm/summary/110181` (the IAPD page).
2. `http://localhost:8000/review` (the local app — open *before*
   pressing Record so the page is settled).

### t=0:00–0:08 — IAPD firm summary

Foreground the IAPD tab. Show the firm name (BROWN ADVISORY), CRD
(110181), SEC# (801-38826), and the **PART 2 BROCHURES** button.
*Caption:* "Any SEC-registered RIA's brochure is here, identified by
CRD."

### t=0:08–0:20 — Pipeline run (terminal, brief)

Tab to a small terminal (or use a side-by-side split for ~10s) and
run:

```bash
uv run python -m adv_lens.app.graph.cli 110181 --vid 1037550 \
    --trace-id demo-$(date +%s)
```

Don't dwell — the goal is to show the pipeline runs end-to-end with
no hand-holding. Either let the JSON stream for ~10s or cut directly
to the result. *Caption:* "fetch → segment → 3 extractors in parallel
→ peer retrieval → Opus redline writer → HITL gate, ~60 s."

### t=0:20–0:32 — Reviewer UI list view

Foreground the browser at `http://localhost:8000/review`. The
dashboard shows: title "Brochure Scoring Tool", a 3-step workflow
strip explaining what the system does, then two side-by-side intake
forms ("Filed brochure" / "Draft brochure"), then the runs table.
*Caption (intro panel):* "A CCO can score a peer firm's filed
brochure or self-review their own draft before submitting it."

The runs table shows the seeded Brown Advisory row (filed) plus the
draft-shaped companion (synthetic `99`-prefixed CRD). Hover the table
row briefly to make the row-highlight visible, then click the filed
row to open the detail page.

### t=0:30–0:55 — Reviewer detail: redline + decision form

The detail page shows two panes: the redline iframe on the left
(score gauge `68`, category table, severity-coloured finding cards)
and the decision form on the right.

- Scroll the redline iframe so the headline and the first finding
  card are visible. *Caption:* "Same HTML the email/PDF path
  produces — read-only here."
- Move to the decision form. Click **Request revision**.
- Type a reviewer email (e.g. `jane.cco@firm.example`) and a
  short rationale: "Confirm Items 11/12 spans before next amendment."

### t=0:55–1:10 — Submit + audit row appears in place

Click **Record decision**. The decisions-panel partial swaps in
without a page reload — the new row is briefly highlighted
(``toast-fresh``), with the decision pill, reviewer, rationale, the
audit timestamp, and the first 16 hex chars of the report hash.
*Caption:* "One row in `human_reviews`, pinned to the SHA-256 of the
exact bytes the reviewer read."

### t=1:10–1:25 — Closer

Hover the report-hash chip in the decision-history panel
(*Caption:* "A re-run with new numbers gets a new hash and won't
satisfy this approval"), then cut.

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
