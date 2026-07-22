from __future__ import annotations

import argparse
import ast
import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

TARGET_METHOD = "WebcastGroupLiveGiftRecipientRecommendMessage"


class ReleaseTagGateError(ValueError):
    """Raised when the repository is not eligible for release-tag promotion."""


@dataclass(frozen=True)
class ReleaseTagGate:
    version: str
    tag: str
    message: str
    release_notes: str
    live_verified: bool
    target_method: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseTagGateError(f"无法读取 JSON：{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseTagGateError(f"JSON 根节点必须为对象：{path}")
    return payload


def _project_version(path: Path) -> str:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        version = payload["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReleaseTagGateError(f"无法读取项目版本：{path}: {exc}") from exc
    if not isinstance(version, str) or not version.strip():
        raise ReleaseTagGateError("pyproject.toml project.version 必须为非空字符串")
    return version.strip()


def _application_version(path: Path) -> str:
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ReleaseTagGateError(f"无法读取应用版本：{path}: {exc}") from exc
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        has_version_target = any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
        if not has_version_target:
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str) and value.strip():
            return value.strip()
        break
    raise ReleaseTagGateError("app.__version__ 必须为非空字符串常量")


def inspect_release_tag_gate(root: Path) -> ReleaseTagGate:
    root = root.resolve()
    project_version = _project_version(root / "pyproject.toml")
    application_version = _application_version(root / "app" / "__init__.py")
    release_lock = _read_json(root / "packaging" / "release-lock.json")
    contract = _read_json(root / "app" / "douyin" / "contracts" / "provisional_v1.json")

    lock_version = release_lock.get("release_version")
    if not isinstance(lock_version, str) or not lock_version.strip():
        raise ReleaseTagGateError("release-lock release_version 必须为非空字符串")
    versions = {
        "pyproject.toml": project_version,
        "app.__version__": application_version,
        "release-lock": lock_version.strip(),
    }
    if len(set(versions.values())) != 1:
        rendered = ", ".join(f"{name}={value}" for name, value in versions.items())
        raise ReleaseTagGateError(f"发布版本不一致：{rendered}")

    live_verified = contract.get("live_verified")
    if live_verified is not False:
        raise ReleaseTagGateError("正式发布前 live_verified 必须严格为 false")
    target_method = contract.get("target_method")
    if target_method != TARGET_METHOD:
        raise ReleaseTagGateError(
            f"target_method 必须为 {TARGET_METHOD}，实际为 {target_method!r}"
        )

    version = project_version
    tag = f"v{version}"
    release_notes = f"docs/releases/{tag}.md"
    notes_path = root / release_notes
    if not notes_path.is_file() or notes_path.is_symlink() or notes_path.stat().st_size == 0:
        raise ReleaseTagGateError(f"缺少非空 Release notes：{release_notes}")

    first_release = "首个 Windows x64 可恢复发布" if version == "0.1.0" else "Windows x64 可恢复发布"
    message = "\n".join(
        (
            f"Douyin Recorder {tag}",
            "",
            f"{first_release}。",
            "包含多房间自动录制、recipient 时间线和后处理导出。",
            "live_verified=false。",
            "真实 recipient 协议证据继续由 Issue #1 跟踪。",
        )
    )
    return ReleaseTagGate(
        version=version,
        tag=tag,
        message=message,
        release_notes=release_notes,
        live_verified=False,
        target_method=TARGET_METHOD,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the immutable release-tag promotion gate."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        payload = asdict(inspect_release_tag_gate(args.root))
    except ReleaseTagGateError as exc:
        parser.error(str(exc))

    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
