from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH_DIR = ROOT / "docs" / "architecture" / "architecture-baseline-v2.0"

REQUIRED = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "app/security.py",
    "THIRD_PARTY_NOTICES.md",
    "config/config.json.default",
    "config/runtime.env.default",
    "docs/GITHUB_WORKFLOW.md",
    "docs/IMPLEMENTATION_WINDOW_PROMPT.md",
    "docs/PRE_IMPLEMENTATION_REVIEW.md",
    "docs/P1A_IMPLEMENTATION_PLAN.md",
    "docs/architecture/architecture-baseline-v2.0.md",
    "docs/protocol/CAPTURE_RUNBOOK.md",
    "docs/protocol/P0_PROTOCOL_STATUS.md",
    ".github/workflows/ci.yml",
    ".github/workflows/live-preflight.yml",
    "pyproject.toml",
    "requirements/runtime.lock",
    "requirements/dev.lock",
    "tools/verify_source.py",
    "tools/create_recovery_assets.py",
    "tools/backup_runtime.py",
    "tools/douyin_room_preflight.py",
    "tools/douyin_browser_probe.py",
    "tools/ffmpeg_supervisor_smoke.py",
    "start.bat",
    "update.bat",
    "verify.bat",
    "backup.bat",
}
ALLOWED_RUNTIME = {
    "records/.gitkeep",
    "userdata/.gitkeep",
    "userdata/README.md",
}
FORBIDDEN_NAMES = {
    ".env",
    "bootstrap-token.txt",
    "config.json",
    "runtime.env",
    "secrets.json",
}
FORBIDDEN_PREFIXES = (
    "captures/",
    "private-fixtures/",
    "backups/",
    ".runtime/",
    ".venv/",
)
SECRET_RE = re.compile(
    r"(?i:(?:sessionid|sessionid_ss|ttwid))\s*=\s*['\"]?[A-Za-z0-9%._~-]{16,}"
    r"|DOUYIN_COOKIE\s*=\s*['\"]?[A-Za-z0-9%._~-]{16,}"
    r"|(?i:cookie)\s*[:=]\s*['\"][^'\"\r\n]{16,}['\"]"
)
SIGNED_WSS_RE = re.compile(r"wss://[^\s'\"<>]+\?(?:[^\s'\"<>]*)(?:signature|internal_ext)=")
USER_PATH_RE = re.compile(r"(?:[A-Za-z]:\\Users\\[^\\]+\\|/home/[^/]+/)")
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".txt",
    ".bat",
    ".sh",
    ".svg",
    ".toml",
    ".css",
    ".html",
    ".js",
    ".lock",
    ".default",
}


def tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return {item.decode("utf-8") for item in result.stdout.split(b"\0") if item}


def main() -> int:
    errors: list[str] = []
    tracked = tracked_files()

    for rel in sorted(REQUIRED):
        path = ROOT / rel
        if rel not in tracked or not path.is_file() or path.is_symlink():
            errors.append(f"缺少必需的普通文件: {rel}")

    chapters = sorted(ARCH_DIR.glob("[0-2][0-9]-*.md"))
    if len(chapters) != 24:
        errors.append(f"架构基线章节应为 24 个，实际为 {len(chapters)}")

    for rel in ("config/config.json.default", "app/douyin/contracts/provisional_v1.json"):
        try:
            value = json.loads((ROOT / rel).read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("根节点不是对象")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"JSON 文件无效 {rel}: {exc}")

    for svg in sorted((ARCH_DIR / "assets/media").glob("*.svg")):
        try:
            ET.parse(svg)
        except (OSError, ET.ParseError) as exc:
            errors.append(f"SVG 无效: {svg.relative_to(ROOT)}: {exc}")

    for rel in sorted(tracked):
        path = ROOT / rel
        if path.is_symlink():
            errors.append(f"仓库不得跟踪符号链接: {rel}")
            continue
        if path.name in FORBIDDEN_NAMES:
            errors.append(f"仓库跟踪了实际配置或秘密文件: {rel}")
        if any(rel.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            errors.append(f"仓库跟踪了运行时、抓包或备份输出: {rel}")
        if rel.startswith(("records/", "userdata/")) and rel not in ALLOWED_RUNTIME:
            errors.append(f"仓库跟踪了运行数据: {rel}")
        if not path.is_file():
            continue
        if path.stat().st_size > 10 * 1024 * 1024:
            errors.append(f"普通源码文件超过 10 MiB: {rel}")
            continue
        suffix = path.suffix.lower() or path.name.lower()
        if suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if rel != "tools/verify_repository_baseline.py" and SECRET_RE.search(text):
            errors.append(f"疑似包含真实抖音凭据: {rel}")
        if rel != "tools/verify_repository_baseline.py" and SIGNED_WSS_RE.search(text):
            errors.append(f"疑似包含完整签名 WSS URL: {rel}")
        if rel != "tools/verify_repository_baseline.py" and USER_PATH_RE.search(text):
            errors.append(f"疑似包含开发机用户绝对路径: {rel}")

    if errors:
        print("[失败] 仓库基线校验发现问题：")
        for item in errors:
            print(f"  - {item}")
        return 1

    print("[通过] 仓库结构、24 章架构、P1A 源码、配置、SVG 与秘密边界正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
