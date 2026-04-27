from eval.runner import run


def test_smoke_fixture_passes() -> None:
    report = run(section_type="smoke")
    assert report.total >= 1
    assert report.passed == report.total
    assert report.mean_score == 1.0
    assert "smoke" in report.by_section
