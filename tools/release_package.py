from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".tmp",
    ".pyc",
    ".pyo",
}
_FORBIDDEN_COMPONENTS = {
    ".git",
    ".github",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "captures",
    "private-fixtures",
    "backups",
    "build",
    "dist",
}
_FORBIDDEN_FILENAMES = {
    ".env",
    "config.json",
    "runtime.env",
    "secrets.json",
    "bootstrap-token.txt",
}
_REQUIRED_PAYLOAD_FILES = {
    "start.bat",
    "verify.bat",
    "backup.bat",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "app/__init__.py",
    "app/__main__.py",
    "app/douyin/contracts/provisional_v1.json",
    "config/config.json.default",
    "config/runtime.env.default",
    "requirements/runtime.lock",
    "runtime/python/python.exe",
    "runtime/ffmpeg/bin/ffmpeg.exe",
    "runtime/ffmpeg/bin/ffprobe.exe",
    "python-dependencies.json",
    "packaging/release-lock.json",
    "scripts/release/health-smoke.ps1",
    "tools/release_package.py",
    "tools/ffmpeg_supervisor_smoke.py",
    "tools/recording_session_smoke.py",
    "tools/postprocess_smoke.py",
    "licenses/Gyan-FFmpeg-Build-NOTICE.md",
    "licenses/FFmpeg-NOTICE.md",
}
_MANIFEST_EXCLUDED = {"windows-manifest.json", "windows-SHA256SUMS.txt"}


class ReleaseError(ValueError):
    """Raised when a release input or package violates a deterministic boundary."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"JSON 无效: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseError(f"JSON 根节点必须是对象: {path}")
    return value


def load_release_lock(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if value.get("schema_version") != 1:
        raise ReleaseError("release lock schema_version 必须为 1")
    version = value.get("release_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError("release_version 必须是稳定三段版本")
    for key in ("python", "ffmpeg", "assets"):
        if not isinstance(value.get(key), dict):
            raise ReleaseError(f"release lock 缺少 {key}")
    python = value["python"]
    ffmpeg = value["ffmpeg"]
    for label, item in (
        ("python.sha256", python.get("sha256")),
        ("ffmpeg.asset_sha256", ffmpeg.get("asset_sha256")),
    ):
        if not isinstance(item, str) or not _HASH_RE.fullmatch(item):
            raise ReleaseError(f"{label} 必须是小写 SHA-256")
    for label, url in (
        ("python.url", python.get("url")),
        ("ffmpeg.asset_url", ffmpeg.get("asset_url")),
    ):
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ReleaseError(f"{label} 必须是 HTTPS URL")
    tag = ffmpeg.get("tag")
    if not isinstance(tag, str) or f"/download/{tag}/" not in ffmpeg["asset_url"]:
        raise ReleaseError("FFmpeg asset URL 必须固定到 release tag")
    return value


def project_versions(root: Path) -> tuple[str, str]:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = str(pyproject["project"]["version"])
    namespace: dict[str, object] = {}
    exec((root / "app" / "__init__.py").read_text(encoding="utf-8"), namespace)
    app_version = str(namespace["__version__"])
    return project, app_version


def validate_version(root: Path, lock: dict[str, Any], *, tag: str | None = None) -> str:
    expected = str(lock["release_version"])
    project, app_version = project_versions(root)
    if project != expected or app_version != expected:
        raise ReleaseError(
            f"版本不一致: lock={expected}, pyproject={project}, app={app_version}"
        )
    if tag is not None and tag != f"v{expected}":
        raise ReleaseError(f"tag 必须是 v{expected}: {tag}")
    return expected


def _relative_files(root: Path) -> list[Path]:
    output: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReleaseError(f"包内禁止符号链接: {path}")
        if path.is_file():
            output.append(path.relative_to(root))
    return output


def _is_forbidden(relative: Path) -> bool:
    lowered = [part.casefold() for part in relative.parts]
    name = relative.name.casefold()
    if any(part in _FORBIDDEN_COMPONENTS for part in lowered):
        return True
    if name in _FORBIDDEN_FILENAMES:
        return True
    if "cookie" in name or "sessionid" in name:
        return True
    rendered = relative.as_posix().casefold()
    return any(rendered.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES)


def validate_package_tree(root: Path, *, require_metadata: bool = True) -> list[Path]:
    root = root.resolve(strict=True)
    files = _relative_files(root)
    forbidden = [path.as_posix() for path in files if _is_forbidden(path)]
    if forbidden:
        raise ReleaseError("包中发现禁止文件: " + ", ".join(forbidden[:20]))
    present = {path.as_posix() for path in files}
    required = set(_REQUIRED_PAYLOAD_FILES)
    if require_metadata:
        required.update(_MANIFEST_EXCLUDED)
    missing = sorted(required - present)
    if missing:
        raise ReleaseError("包缺少必需文件: " + ", ".join(missing))
    contract = _read_json(root / "app/douyin/contracts/provisional_v1.json")
    if contract.get("live_verified") is not False:
        raise ReleaseError("Release 必须保持 live_verified=false")
    dependencies = _read_json(root / "python-dependencies.json")
    if not isinstance(dependencies.get("dependencies"), list) or not dependencies["dependencies"]:
        raise ReleaseError("Python 依赖清单不能为空")
    if not any(path.as_posix().startswith("licenses/ffmpeg/") for path in files):
        raise ReleaseError("包缺少 FFmpeg archive 许可证文件")
    return files


def build_manifest(
    root: Path,
    *,
    version: str,
    source_commit: str,
    lock_sha256: str,
) -> dict[str, Any]:
    files = validate_package_tree(root, require_metadata=False)
    rows = []
    for relative in files:
        name = relative.as_posix()
        if name in _MANIFEST_EXCLUDED:
            continue
        path = root / relative
        rows.append({"path": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return {
        "schema_version": 1,
        "version": version,
        "platform": "windows-x64",
        "source_commit": source_commit,
        "release_lock_sha256": lock_sha256,
        "files": rows,
    }


def write_manifest(
    root: Path,
    *,
    version: str,
    source_commit: str,
    lock_path: Path,
) -> Path:
    manifest = build_manifest(
        root,
        version=version,
        source_commit=source_commit,
        lock_sha256=sha256_file(lock_path),
    )
    path = root / "windows-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = []
    for relative in _relative_files(root):
        if relative.as_posix() == "windows-SHA256SUMS.txt":
            continue
        rows.append(f"{sha256_file(root / relative)}  {relative.as_posix()}")
    (root / "windows-SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="ascii")
    return path


def verify_manifest(root: Path) -> dict[str, Any]:
    validate_package_tree(root)
    manifest = _read_json(root / "windows-manifest.json")
    if manifest.get("schema_version") != 1:
        raise ReleaseError("windows manifest schema 无效")
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise ReleaseError("windows manifest files 无效")
    expected_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ReleaseError("windows manifest row 无效")
        name = row.get("path")
        pure_name = PurePosixPath(name) if isinstance(name, str) else None
        if pure_name is None or pure_name.is_absolute() or ".." in pure_name.parts:
            raise ReleaseError("windows manifest path 无效")
        path = root / Path(*pure_name.parts)
        if not path.is_file() or path.is_symlink():
            raise ReleaseError(f"manifest 文件缺失或类型不安全: {name}")
        if path.stat().st_size != row.get("bytes") or sha256_file(path) != row.get("sha256"):
            raise ReleaseError(f"manifest 校验失败: {name}")
        expected_paths.add(name)
    actual = {
        path.as_posix()
        for path in _relative_files(root)
        if path.as_posix() not in _MANIFEST_EXCLUDED
    }
    if actual != expected_paths:
        raise ReleaseError("manifest 文件集合与包内容不一致")
    checksum_path = root / "windows-SHA256SUMS.txt"
    for number, line in enumerate(checksum_path.read_text(encoding="ascii").splitlines(), start=1):
        if not line:
            continue
        try:
            expected, name = line.split("  ", 1)
        except ValueError as exc:
            raise ReleaseError(f"SHA256SUMS 第 {number} 行无效") from exc
        if not _HASH_RE.fullmatch(expected):
            raise ReleaseError(f"SHA256SUMS 第 {number} 行 hash 无效")
        pure_name = PurePosixPath(name)
        if pure_name.is_absolute() or ".." in pure_name.parts:
            raise ReleaseError(f"SHA256SUMS 第 {number} 行路径无效")
        path = root / Path(*pure_name.parts)
        if not path.is_file() or sha256_file(path) != expected:
            raise ReleaseError(f"SHA256SUMS 校验失败: {name}")
    return manifest


def create_deterministic_zip(root: Path, output: Path) -> None:
    verify_manifest(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ReleaseError(f"拒绝覆盖已有 ZIP: {output}")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in _relative_files(root):
            path = root / relative
            info = zipfile.ZipInfo(relative.as_posix(), date_time=_FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def write_dependency_manifest(
    site_packages: Path,
    output: Path,
    license_root: Path,
) -> dict[str, Any]:
    site_packages = site_packages.resolve(strict=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    license_root.mkdir(parents=True, exist_ok=True)
    distributions = []
    for distribution in sorted(
        importlib.metadata.distributions(path=[str(site_packages)]),
        key=lambda item: (item.metadata.get("Name", "").casefold(), item.version),
    ):
        name = (
            distribution.metadata.get("Name")
            or distribution.metadata.get("Summary")
            or "unknown"
        )
        normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "unknown"
        copied: list[str] = []
        for file in distribution.files or ():
            filename = PurePosixPath(str(file)).name.casefold()
            if not filename.startswith(("license", "copying", "notice", "authors")):
                continue
            source = distribution.locate_file(file)
            if not source.is_file() or source.is_symlink():
                continue
            target = license_root / normalized / PurePosixPath(str(file)).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target.relative_to(license_root.parent).as_posix())
        distributions.append(
            {
                "name": name,
                "version": distribution.version,
                "license_expression": distribution.metadata.get("License-Expression", ""),
                "license": distribution.metadata.get("License", ""),
                "home_page": distribution.metadata.get("Home-page", ""),
                "license_files": sorted(set(copied)),
            }
        )
    if not distributions:
        raise ReleaseError("未在 site-packages 中发现依赖")
    payload = {"schema_version": 1, "dependencies": distributions}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def write_source_manifest(root: Path, output: Path, *, commit: str) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    rows = []
    for name in sorted(names):
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseError(f"tracked source path 类型不安全: {name}")
        rows.append(
            {
                "path": PurePosixPath(name).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    payload = {"schema_version": 1, "commit": commit, "files": rows}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P4A deterministic package manifest and verification"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-lock")
    validate.add_argument("--lock", type=Path, default=ROOT / "packaging/release-lock.json")
    validate.add_argument("--root", type=Path, default=ROOT)
    validate.add_argument("--tag")
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--package-root", type=Path, required=True)
    manifest.add_argument("--version", required=True)
    manifest.add_argument("--source-commit", required=True)
    manifest.add_argument("--lock", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--package-root", type=Path, required=True)
    archive = sub.add_parser("zip")
    archive.add_argument("--package-root", type=Path, required=True)
    archive.add_argument("--output", type=Path, required=True)
    deps = sub.add_parser("dependencies")
    deps.add_argument("--site-packages", type=Path, required=True)
    deps.add_argument("--output", type=Path, required=True)
    deps.add_argument("--license-root", type=Path, required=True)
    source = sub.add_parser("source-manifest")
    source.add_argument("--root", type=Path, default=ROOT)
    source.add_argument("--output", type=Path, required=True)
    source.add_argument("--commit", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "validate-lock":
            lock = load_release_lock(args.lock)
            version = validate_version(args.root, lock, tag=args.tag)
            print(json.dumps({"ok": True, "version": version}, ensure_ascii=False))
        elif args.command == "manifest":
            path = write_manifest(
                args.package_root,
                version=args.version,
                source_commit=args.source_commit,
                lock_path=args.lock,
            )
            print(path)
        elif args.command == "verify":
            print(json.dumps(verify_manifest(args.package_root), ensure_ascii=False))
        elif args.command == "zip":
            create_deterministic_zip(args.package_root, args.output)
            print(args.output)
        elif args.command == "dependencies":
            write_dependency_manifest(args.site_packages, args.output, args.license_root)
            print(args.output)
        elif args.command == "source-manifest":
            write_source_manifest(args.root, args.output, commit=args.commit)
            print(args.output)
        else:  # pragma: no cover
            raise ReleaseError("未知命令")
    except (OSError, ReleaseError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
