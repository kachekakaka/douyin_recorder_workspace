from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path = ROOT, capture: bool = True) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout.strip() if capture else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_label(value: str) -> str:
    output = "".join(char if char.isalnum() or char in "._-" else "-" for char in value)
    return output.strip(".-") or "snapshot"


def create_assets(output_dir: Path, *, label: str, require_clean: bool = True) -> dict[str, object]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if require_clean and _run("git", "status", "--porcelain"):
        raise RuntimeError("工作区存在未提交修改；为避免备份不完整，已停止")

    commit = _run("git", "rev-parse", "HEAD")
    branch = _run("git", "branch", "--show-current") or "detached"
    safe_label = _safe_label(label)
    prefix = f"douyin-recorder-{safe_label}-{commit[:12]}"
    bundle = output_dir / f"{prefix}.bundle"
    source_zip = output_dir / f"{prefix}-source.zip"
    manifest = output_dir / f"{prefix}-manifest.json"
    checksums = output_dir / f"{prefix}-SHA256SUMS.txt"

    _run("git", "bundle", "create", str(bundle), "HEAD", "--all", capture=False)
    _run("git", "archive", "--format=zip", "-o", str(source_zip), "HEAD", capture=False)
    _run("git", "bundle", "verify", str(bundle))

    with tempfile.TemporaryDirectory(prefix="douyin-recovery-verify-") as temp:
        restored = Path(temp) / "restored"
        _run("git", "clone", "--quiet", str(bundle), str(restored), cwd=ROOT, capture=False)
        _run("git", "checkout", "--quiet", "--detach", commit, cwd=restored, capture=False)
        restored_head = _run("git", "rev-parse", "HEAD", cwd=restored)
        if restored_head != commit:
            raise RuntimeError(f"Git Bundle 恢复 HEAD 不一致: {restored_head} != {commit}")
        _run("git", "fsck", "--no-dangling", cwd=restored)
        subprocess.run(
            [sys.executable, "tools/verify_repository_baseline.py"],
            cwd=restored,
            check=True,
        )

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "branch": branch,
        "label": safe_label,
        "bundle": bundle.name,
        "source_zip": source_zip.name,
        "verification": "bundle cloned, git fsck passed, repository baseline passed",
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = [
        f"{_sha256(bundle)}  {bundle.name}",
        f"{_sha256(source_zip)}  {source_zip.name}",
        f"{_sha256(manifest)}  {manifest.name}",
    ]
    checksums.write_text("\n".join(rows) + "\n", encoding="utf-8")
    payload["manifest"] = manifest.name
    payload["checksums"] = checksums.name
    payload["output_dir"] = str(output_dir)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="创建并恢复验证 Git Bundle 与源码 ZIP")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "backups" / "source")
    parser.add_argument("--label", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--allow-dirty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = create_assets(
            args.output_dir,
            label=args.label,
            require_clean=not args.allow_dirty,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[失败] 创建恢复资产失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
