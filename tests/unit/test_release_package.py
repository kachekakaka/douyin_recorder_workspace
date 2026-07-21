from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tools.release_package import (
    ReleaseError,
    create_deterministic_zip,
    load_release_lock,
    validate_package_tree,
    validate_version,
    verify_manifest,
    write_manifest,
)


def _package(root: Path) -> Path:
    files = {
        "start.bat": "@echo off\n",
        "verify.bat": "@echo off\n",
        "backup.bat": "@echo off\n",
        "README.md": "release\n",
        "THIRD_PARTY_NOTICES.md": "notices\n",
        "app/__init__.py": '__version__ = "0.1.0"\n',
        "app/__main__.py": "pass\n",
        "app/douyin/contracts/provisional_v1.json": json.dumps(
            {"live_verified": False}
        ),
        "config/config.json.default": "{}\n",
        "config/runtime.env.default": "# empty\n",
        "requirements/runtime.lock": "example==1.0\n",
        "runtime/python/python.exe": "python",
        "runtime/ffmpeg/bin/ffmpeg.exe": "ffmpeg",
        "runtime/ffmpeg/bin/ffprobe.exe": "ffprobe",
        "python-dependencies.json": (
            '{"schema_version":1,"dependencies":'
            '[{"name":"demo","version":"1"}]}\n'
        ),
        "packaging/release-lock.json": "{}\n",
        "scripts/release/health-smoke.ps1": "Write-Host ok\n",
        "tools/release_package.py": "pass\n",
        "tools/ffmpeg_supervisor_smoke.py": "pass\n",
        "tools/recording_session_smoke.py": "pass\n",
        "tools/postprocess_smoke.py": "pass\n",
        "licenses/Gyan-FFmpeg-Build-NOTICE.md": "Gyan FFmpeg build notice\n",
        "licenses/FFmpeg-NOTICE.md": "GPL\n",
        "licenses/ffmpeg/LICENSE.txt": "GPL\n",
        "payload.txt": "hello\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def test_release_lock_and_project_version_are_pinned() -> None:
    lock = load_release_lock(Path("packaging/release-lock.json"))
    assert validate_version(Path.cwd(), lock, tag="v0.1.0") == "0.1.0"
    assert "/download/latest/" not in lock["ffmpeg"]["asset_url"]
    with pytest.raises(ReleaseError, match="tag"):
        validate_version(Path.cwd(), lock, tag="v0.1.1")


def test_package_manifest_and_zip_are_deterministic(tmp_path: Path) -> None:
    package = _package(tmp_path / "package")
    lock = Path("packaging/release-lock.json")
    write_manifest(
        package,
        version="0.1.0",
        source_commit="a" * 40,
        lock_path=lock,
    )
    manifest = verify_manifest(package)
    assert manifest["version"] == "0.1.0"
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    create_deterministic_zip(package, first)
    create_deterministic_zip(package, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert "windows-manifest.json" in archive.namelist()


def test_package_refuses_runtime_state_secrets_and_symlinks(tmp_path: Path) -> None:
    package = _package(tmp_path / "package")
    forbidden = package / "config" / "config.json"
    forbidden.write_text('{"cookie":"secret"}', encoding="utf-8")
    with pytest.raises(ReleaseError, match="禁止文件"):
        validate_package_tree(package, require_metadata=False)
    forbidden.unlink()
    (package / "userdata").mkdir()
    (package / "userdata" / "state.sqlite").write_bytes(b"sqlite")
    with pytest.raises(ReleaseError, match="禁止文件"):
        validate_package_tree(package, require_metadata=False)


def test_package_refuses_live_verified_true(tmp_path: Path) -> None:
    package = _package(tmp_path / "package")
    contract = package / "app/douyin/contracts/provisional_v1.json"
    contract.write_text('{"live_verified":true}', encoding="utf-8")
    with pytest.raises(ReleaseError, match="live_verified"):
        validate_package_tree(package, require_metadata=False)


def test_release_workflow_and_portable_scripts_keep_required_gates() -> None:
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    build = Path("scripts/release/build-windows-package.ps1").read_text(encoding="utf-8")
    portable_start = Path("packaging/windows/start.bat").read_text(encoding="utf-8")
    portable_verify = Path("packaging/windows/verify.bat").read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "--verify-tag" in workflow
    assert "verify-windows-package.ps1" in workflow
    assert "source.bundle" in workflow
    assert "ffmpeg.asset_sha256" in build
    assert "ffmpeg.checksums_url" not in build
    assert "config\\config.json.default" in build
    assert "config\\config.json\"" not in build
    assert "runtime\\python\\python.exe" in portable_start
    assert "prepare-python.bat" not in portable_start
    assert "health-smoke.ps1" in portable_verify
    assert "postprocess_smoke.py" in portable_verify


def test_dependency_manifest_copies_license_files(tmp_path: Path) -> None:
    from tools.release_package import write_dependency_manifest

    site = tmp_path / "site-packages"
    dist = site / "demo-1.2.3.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: demo\nVersion: 1.2.3\nLicense-Expression: MIT\n",
        encoding="utf-8",
    )
    (dist / "LICENSE").write_text("MIT license\n", encoding="utf-8")
    (dist / "RECORD").write_text(
        "demo-1.2.3.dist-info/METADATA,,\n"
        "demo-1.2.3.dist-info/LICENSE,,\n"
        "demo-1.2.3.dist-info/RECORD,,\n",
        encoding="utf-8",
    )
    output = tmp_path / "python-dependencies.json"
    payload = write_dependency_manifest(site, output, tmp_path / "licenses/python")
    assert payload["dependencies"][0]["name"] == "demo"
    assert payload["dependencies"][0]["license_expression"] == "MIT"
    assert (tmp_path / "licenses/python/demo/LICENSE").is_file()
