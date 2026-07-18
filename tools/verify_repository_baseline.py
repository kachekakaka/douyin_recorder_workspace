from __future__ import annotations

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARCH_DIR = ROOT / "docs" / "architecture" / "architecture-baseline-v2.0"

REQUIRED = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "config/config.json.default",
    "docs/GITHUB_WORKFLOW.md",
    "docs/IMPLEMENTATION_WINDOW_PROMPT.md",
    "docs/PRE_IMPLEMENTATION_REVIEW.md",
    "docs/architecture/architecture-baseline-v2.0.md",
    ".github/workflows/ci.yml",
}
ALLOWED_RUNTIME = {
    "records/.gitkeep",
    "userdata/.gitkeep",
    "userdata/README.md",
}
FORBIDDEN_NAMES = {".env", "bootstrap-token.txt", "config.json", "secrets.json"}
SECRET_RE = re.compile(
    r"(?i)(?:sessionid|ttwid|cookie)\s*[=:]\s*['\"]?[A-Za-z0-9%._~-]{16,}"
)
USER_PATH_RE = re.compile(r"(?:[A-Za-z]:\\Users\\[^\\]+\\|/home/[^/]+/)")
TEXT_SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".txt", ".bat", ".sh", ".svg"}


def tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return {
        item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    }


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

    try:
        json.loads((ROOT / "config/config.json.default").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"默认 JSON 配置无效: {exc}")

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
        if rel.startswith("records/") or rel.startswith("userdata/"):
            if rel not in ALLOWED_RUNTIME:
                errors.append(f"仓库跟踪了运行数据: {rel}")
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if rel != "tools/verify_repository_baseline.py" and SECRET_RE.search(text):
            errors.append(f"疑似包含真实抖音凭据: {rel}")
        if rel != "tools/verify_repository_baseline.py" and USER_PATH_RE.search(text):
            errors.append(f"疑似包含开发机用户绝对路径: {rel}")

    if errors:
        print("[失败] 仓库基线校验发现问题：")
        for item in errors:
            print(f"  - {item}")
        return 1

    print("[通过] 仓库元数据、24 章架构基线、配置模板、SVG 与秘密边界正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
