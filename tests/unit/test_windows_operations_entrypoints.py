from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_source_and_portable_operations_entrypoints_match_safety_contract() -> None:
    pairs = (
        ("diagnostics.bat", "packaging/windows/diagnostics.bat"),
        ("maintenance.bat", "packaging/windows/maintenance.bat"),
        ("operations.bat", "packaging/windows/operations.bat"),
    )
    for source_name, portable_name in pairs:
        source = _read(source_name)
        portable = _read(portable_name)
        for text in (source, portable):
            assert "DOUYIN_RECORDER_USERDATA_DIR" in text or "operations.bat" in source_name
            assert "DOUYIN_COOKIE" not in text
            assert "raw_payload" not in text
            assert "taskkill" not in text.casefold()
            for line in text.splitlines():
                command = line.strip().casefold()
                assert not command.startswith(("format ", "format.com "))


def test_timestamp_generation_is_safe_in_unicode_package_paths() -> None:
    for relative in (
        "diagnostics.bat",
        "maintenance.bat",
        "packaging/windows/diagnostics.bat",
        "packaging/windows/maintenance.bat",
    ):
        text = _read(relative)
        assert 'powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"' in text
        assert "from datetime import datetime" not in text
        assert '"%PY%" -c' not in text


def test_diagnostics_entrypoints_generate_redacted_json() -> None:
    for relative in ("diagnostics.bat", "packaging/windows/diagnostics.bat"):
        text = _read(relative)
        assert "tools\\diagnostics_report.py" in text
        assert "--root \"%CD%\"" in text
        assert "diagnostics-%STAMP%.json" in text
        assert "type \"%REPORT%\"" in text


def test_maintenance_entrypoints_require_explicit_stopped_confirmation() -> None:
    for relative in ("maintenance.bat", "packaging/windows/maintenance.bat"):
        text = _read(relative)
        assert "I_HAVE_STOPPED_THE_APP" in text
        assert "--apply --confirm-stopped" in text
        assert "--backup-dir \"%BACKUP_ROOT%\"" in text
        assert "tools\\database_maintenance.py" in text
        assert "goto :confirm" in text
        assert "VACUUM" not in text.upper()


def test_unified_operations_entrypoints_dispatch_only_known_commands() -> None:
    for relative in ("operations.bat", "packaging/windows/operations.bat"):
        text = _read(relative)
        assert "operations.bat diagnostics" in text
        assert "operations.bat backup" in text
        assert "operations.bat maintenance-plan" in text
        assert "operations.bat maintenance-apply I_HAVE_STOPPED_THE_APP" in text
        assert "call diagnostics.bat" in text
        assert "call backup.bat" in text
        assert "call maintenance.bat plan" in text
        assert "call maintenance.bat apply \"%~2\"" in text


def test_portable_build_and_verify_include_operations_toolchain() -> None:
    build = _read("scripts/release/build-windows-package.ps1")
    verify = _read("packaging/windows/verify.bat")
    for name in (
        "diagnostics.bat",
        "maintenance.bat",
        "operations.bat",
        "database_integrity_check.py",
        "database_maintenance.py",
        "diagnostics_report.py",
    ):
        assert name in build
    health = "scripts\\release\\health-smoke.ps1"
    diagnostics = "call operations.bat diagnostics"
    maintenance = "call operations.bat maintenance-plan"
    assert health in verify
    assert diagnostics in verify
    assert maintenance in verify
    assert verify.index(health) < verify.index(diagnostics) < verify.index(maintenance)
    assert (
        'set "DOUYIN_RECORDER_DATABASE_PATH=%VERIFY_DIR%\\health\\userdata\\health.db"'
        in verify
    )
