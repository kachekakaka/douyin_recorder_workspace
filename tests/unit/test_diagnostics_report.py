from pathlib import Path

from tools.diagnostics_report import build_report


def test_diagnostics_report_does_not_expose_sensitive_fields(tmp_path: Path) -> None:
    report = build_report(tmp_path)

    assert report["safety"]["contains_cookie"] is False
    assert report["safety"]["contains_full_stream_url"] is False
    assert report["safety"]["contains_raw_payload"] is False
    assert "cookie" not in str(report).lower()
    assert "payload" not in str(report).lower()
