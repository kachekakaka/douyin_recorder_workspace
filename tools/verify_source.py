from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin import TARGET_METHOD  # noqa: E402
from app.douyin.recipient import RecipientContract  # noqa: E402
from app.douyin.replay import run_fixture  # noqa: E402
from tools.verify_repository_baseline import main as verify_baseline  # noqa: E402

_REQUIRED_P0 = (
    "app/main.py",
    "app/settings.py",
    "app/db/core.py",
    "app/db/migrations/__init__.py",
    "app/douyin/probe.py",
    "app/douyin/replay.py",
    "app/douyin/timeline.py",
    "tools/douyin_wss_probe.py",
    "tools/douyin_room_preflight.py",
    "tools/douyin_browser_probe.py",
    "tools/create_recovery_assets.py",
    "tools/backup_runtime.py",
    "tests/replay/contracts/recipient.synthetic-v1.json",
    "tests/replay/fixtures/recipient-strict-unknown.synthetic.json",
    "requirements/runtime.lock",
    "requirements/dev.lock",
    "start.bat",
    "update.bat",
    "verify.bat",
    "backup.bat",
)
_FORBIDDEN_SOURCE_PATTERNS = {
    "OCR library": re.compile(r"(?i)\b(?:pytesseract|easyocr)\b"),
    "face recognition": re.compile(r"(?i)\bface_recognition\b"),
    "speech inference": re.compile(r"(?i)\b(?:openai-whisper|faster_whisper)\b"),
    "unsafe subprocess shell": re.compile(r"shell\s*=\s*True"),
}
_CODE_SUFFIXES = {".py", ".js", ".html", ".css"}


def _lock_packages(path: Path) -> list[str]:
    output: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-r ")):
            continue
        name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip().casefold()
        if name:
            output.append(name)
    return output


def main() -> int:
    errors: list[str] = []
    if verify_baseline() != 0:
        errors.append("仓库基线校验失败")

    for rel in _REQUIRED_P0:
        path = ROOT / rel
        if not path.is_file() or path.is_symlink():
            errors.append(f"缺少 P0 必需普通文件: {rel}")

    runtime_packages = _lock_packages(ROOT / "requirements" / "runtime.lock")
    dev_packages = _lock_packages(ROOT / "requirements" / "dev.lock")
    if len(runtime_packages) != len(set(runtime_packages)):
        errors.append("requirements/runtime.lock 包名重复")
    if len(dev_packages) != len(set(dev_packages)):
        errors.append("requirements/dev.lock 包名重复")
    for package in ("fastapi", "uvicorn", "aiosqlite", "httpx", "websockets", "protobuf"):
        if package not in runtime_packages:
            errors.append(f"runtime.lock 缺少 {package}")
    for package in ("pytest", "ruff"):
        if package not in dev_packages:
            errors.append(f"dev.lock 缺少 {package}")

    try:
        provisional_contract = RecipientContract.load(
            ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json"
        )
        if provisional_contract.target_method != TARGET_METHOD:
            errors.append("协议 contract 目标 method 不一致")
        if provisional_contract.live_verified:
            errors.append("尚无经审查现场样本，provisional contract 不得标记 live_verified=true")

        synthetic_contract = RecipientContract.load(
            ROOT / "tests" / "replay" / "contracts" / "recipient.synthetic-v1.json"
        )
        if synthetic_contract.target_method != TARGET_METHOD:
            errors.append("合成 replay contract 目标 method 不一致")
        if synthetic_contract.live_verified:
            errors.append("合成 replay contract 不得标记 live_verified=true")
        replay = run_fixture(
            ROOT / "tests" / "replay" / "fixtures" / "recipient-strict-unknown.synthetic.json",
            contract=synthetic_contract,
        )
        if not replay.fixture_synthetic or replay.fixture_live_verified:
            errors.append("P0 replay fixture 的 synthetic/live_verified 标志不安全")
        if replay.target_messages <= 0 or replay.target_decode_failures != 0:
            errors.append("P0 replay 未稳定解码目标消息")
        intervals = replay.reducer_snapshot.get("intervals")
        if not isinstance(intervals, list) or not any(
            isinstance(item, dict)
            and item.get("status") == "unknown"
            and item.get("reason") == "im_disconnected"
            for item in intervals
        ):
            errors.append("P0 replay 未覆盖 Unknown(im_disconnected)")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"协议 contract/replay 校验失败: {exc}")

    for root_name in ("app", "tools", "web"):
        root = ROOT / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _CODE_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel == "tools/verify_source.py":
                continue
            for label, pattern in _FORBIDDEN_SOURCE_PATTERNS.items():
                if pattern.search(text):
                    errors.append(f"{rel} 命中禁止模式 {label}")

    if errors:
        print("[失败] P0 源码校验发现问题：")
        for item in errors:
            print(f"  - {item}")
        return 1
    print("[通过] P0 工程骨架、依赖锁、严格单一信号 replay 与安全边界正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
