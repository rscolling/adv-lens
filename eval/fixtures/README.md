# Golden-set fixtures

One JSON file per fixture. Directory = `section_type`.

## Labeling protocol

1. Pull source brochure from SEC IAPD. Record the IARD CRD number and the direct URL of the PDF version dated on the filing.
2. Copy the exact section text into `raw/<id>.txt` (gitignored — we keep the reference off-repo for size + provenance).
3. Hand-label `expected` per the section type's schema (see `eval/schemas.py`). Anchor the label on SEC primary sources:
   - Form ADV Part 2A General Instructions (plain-English §2, item-by-item guidance)
   - 2010 ADV Part 2 Adopting Release
   - Kitces / Multnomah Group operational guidance where the form is silent
4. Set `labeled_by` = initials + role + date ISO-8601.
5. If a label is judgment-dependent (material vs non-material disciplinary disclosure; dedup rule on conflicts), record reasoning in `notes`.

## Planned size

| section_type | target | current |
| --- | ---: | ---: |
| smoke | 1 | 1 |
| segmenter | 5 | 1 |
| fee | 20 | 5 |
| disciplinary | 15 | 5 |
| conflicts | 15 | 5 |
| redline | 10 | 2 |
| **total** | **66** | **19** |

## Fixture style — synthetic-clean vs realism-style

Two prose styles coexist in the corpus today. Each fixture's ``notes``
field calls out which style it is and what risk it targets:

- **Synthetic-clean** (`item_001`–`item_003` per section type). Short,
  unambiguous prose engineered to round-trip cleanly through the
  scorer. Useful for catching schema regressions and obvious prompt
  drift; will not catch the prompt-brittleness real brochures introduce.
- **Realism-style** (`item_004`+ per section type). Longer prose using
  the structural patterns common in large-RIA ADV brochures: multi-program
  cross-references, "in our sole discretion" hedging, paragraph
  numbering, bullet-list enumerations, multi-paragraph disciplinary
  narratives, BrokerCheck cross-references. Anonymous (fictional firm
  names like "Synthetic Capital" and "Cornerstone Trust") to avoid
  singling-out concerns. These close most of the prose-realism gap
  without bundling identifiable real-firm text.

Until the project runs against a fetched real IAPD brochure end-to-end,
neither style validates the IAPD fetcher path or PDF text extraction —
those are mechanical I/O concerns rather than the interesting risk.

## Review status

All items are solo-labeled against SEC primary sources. Subset review by a
practicing RIA CCO is planned post-MVP and will be noted in the README when
complete.
