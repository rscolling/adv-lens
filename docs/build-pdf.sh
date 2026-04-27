#!/usr/bin/env bash
#
# Render docs/user-manual.md → docs/user-manual.pdf via:
#   pandoc → standalone HTML → Chrome --headless --print-to-pdf
#
# Why this pipeline (not pandoc --pdf-engine=xelatex):
#   - LaTeX (xelatex/pdflatex) is a multi-GB install no one needs locally.
#   - wkhtmltopdf and weasyprint both need extra OS-level deps on Windows.
#   - Chrome ships with every dev box; pandoc 3.x is a single binary.
#   - The CSS in user-manual-print.css is plain HTML5 — printable everywhere.
#
# Run from repo root: bash docs/build-pdf.sh
set -euo pipefail

cd "$(dirname "$0")/.."

DOCS_DIR="docs"
SRC="${DOCS_DIR}/user-manual.md"
CSS="${DOCS_DIR}/user-manual-print.css"
HTML="${DOCS_DIR}/user-manual.html"
PDF="${DOCS_DIR}/user-manual.pdf"

# Pick a Chrome-class binary. Edge works too on Windows; both honour the
# same --headless --print-to-pdf flags.
CHROME=""
for cand in \
  "/c/Program Files/Google/Chrome/Application/chrome.exe" \
  "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
  "/usr/bin/google-chrome" \
  "/usr/bin/chromium"
do
  if [ -x "$cand" ]; then
    CHROME="$cand"
    break
  fi
done
if [ -z "$CHROME" ]; then
  echo "No Chrome/Edge binary found; cannot render PDF." >&2
  exit 1
fi

echo "→ pandoc: ${SRC} → ${HTML}"
pandoc "$SRC" \
  --standalone \
  --toc \
  --toc-depth=3 \
  --css="$(basename "$CSS")" \
  --metadata=lang:en \
  -f gfm+yaml_metadata_block \
  -o "$HTML"

# We pass --css with a relative path so the link is relative to the HTML
# file. Chrome resolves it correctly when given the absolute file:// URL.

echo "→ chrome: ${HTML} → ${PDF}"
# Chrome's --print-to-pdf wants an absolute path with native separators
# (it doesn't resolve relative paths against the working directory the
# same way bash does on Windows). Build both paths in Windows form.
ABS_HTML="$(cd "$(dirname "$HTML")" && pwd)/$(basename "$HTML")"
ABS_PDF="$(cd "$(dirname "$PDF")" && pwd)/$(basename "$PDF")"
# Strip the leading /c/ → C:/ for Windows-native paths.
WIN_HTML="$(echo "$ABS_HTML" | sed -E 's|^/([a-zA-Z])/|\1:/|')"
WIN_PDF="$(echo "$ABS_PDF"  | sed -E 's|^/([a-zA-Z])/|\1:/|')"
URL="file:///${WIN_HTML}"

"$CHROME" \
  --headless \
  --disable-gpu \
  --no-pdf-header-footer \
  --print-to-pdf="$WIN_PDF" \
  --virtual-time-budget=5000 \
  "$URL" 2>&1 | grep -v -E "^\[" || true

if [ -f "$PDF" ]; then
  SIZE=$(stat -c %s "$PDF" 2>/dev/null || stat -f %z "$PDF")
  echo "✓ ${PDF} (${SIZE} bytes)"
else
  echo "PDF generation failed" >&2
  exit 1
fi
