"""IARD bulk Part 1 CSV loader tests."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from adv_lens.ingestion.iard import IARDBulkLoader


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_iard_loader_maps_aliased_columns(tmp_path: Path) -> None:
    csv_path = tmp_path / "ADV_Base_A_202604.csv"
    _write_csv(
        csv_path,
        rows=[
            {
                "1E1": "108000",
                "1A": "Wealth Partners LLC",
                "DateSubmitted": "2026-03-15",
                "1F1-State": "NY",
                "2A": "SEC Registered",
                "5F2a": "1500000000",
                "5F2b": "250000000",
                "5C1": "1200",
                "5A": "45",
                "11": "N",
            },
            {
                "1E1": "",  # blank CRD row is skipped
                "1A": "Ghost",
                "DateSubmitted": "",
                "1F1-State": "",
                "2A": "",
                "5F2a": "",
                "5F2b": "",
                "5C1": "",
                "5A": "",
                "11": "",
            },
        ],
        fieldnames=[
            "1E1",
            "1A",
            "DateSubmitted",
            "1F1-State",
            "2A",
            "5F2a",
            "5F2b",
            "5C1",
            "5A",
            "11",
        ],
    )

    rows = list(IARDBulkLoader(csv_path).iter_rows())
    assert len(rows) == 1

    row = rows[0]
    assert row.crd == "108000"
    assert row.firm_name == "Wealth Partners LLC"
    assert row.main_office_state == "NY"
    assert row.regulated_by == "SEC"
    assert row.aum_discretionary_usd == 1_500_000_000
    assert row.aum_nondiscretionary_usd == 250_000_000
    assert row.aum_total_usd == 1_750_000_000
    assert row.aum_band == "$1B-$10B"
    assert row.total_clients == 1200
    assert row.total_employees == 45
    assert row.has_disciplinary_history is False
    assert row.filing_date is not None and row.filing_date.isoformat() == "2026-03-15"


def test_iard_loader_aum_band_cutoffs(tmp_path: Path) -> None:
    csv_path = tmp_path / "bands.csv"
    _write_csv(
        csv_path,
        rows=[
            {"1E1": "1", "1A": "Micro", "5F2a": "50000000"},
            {"1E1": "2", "1A": "Mid", "5F2a": "500000000"},
            {"1E1": "3", "1A": "Large", "5F2a": "5000000000"},
            {"1E1": "4", "1A": "Mega", "5F2a": "50000000000"},
            {"1E1": "5", "1A": "Titan", "5F2a": "500000000000"},
        ],
        fieldnames=["1E1", "1A", "5F2a"],
    )
    bands = [r.aum_band for r in IARDBulkLoader(csv_path).iter_rows()]
    assert bands == ["<$100M", "$100M-$1B", "$1B-$10B", "$10B-$100B", ">$100B"]


def test_iard_loader_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        IARDBulkLoader(tmp_path / "nope.csv")
