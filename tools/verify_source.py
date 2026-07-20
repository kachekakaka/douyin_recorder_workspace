from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.migrations import MIGRATIONS  # noqa: E402
from app.douyin import TARGET_METHOD  # noqa: E402
from app.douyin.live_page import inspect_live_page  # noqa: E402
from app.douyin.recipient import RecipientContract  # noqa: E402
from app.douyin.replay import run_fixture  # noqa: E402
from tools.verify_repository_baseline import main as verify_baseline  # noqa: E402

_REQUIRED = (
    "app/main.py",
    "app/security.py",
    "app/settings.py",
    "app/db/core.py",
    "app/db/migrations/__init__.py",
    "app/douyin/probe.py",
    "app/douyin/replay.py",
    "app/douyin/timeline.py",
    "app/douyin/live_page.py",
    "app/douyin/stream_resolver.py",
    "app/douyin/evidence.py",
    "app/rooms/models.py",
    "app/rooms/repository.py",
    "app/rooms/service.py",
    "app/media/ffmpeg.py",
    "app/api/rooms.py",
    "tools/douyin_wss_probe.py",
    "tools/douyin_room_preflight.py",
    "tools/douyin_browser_probe.py",
    "tools/douyin_network_probe.py",
    "tools/douyin_interactive_evidence.py",
    "tools/export_recipient_evidence_fixture.py",
    "tools/ffmpeg_supervisor_smoke.py",
    "tools/create_recovery_assets.py",
    "tools/backup_runtime.py",
    "tests/replay/fixtures/recipient-strict-unknown.synthetic.json",
    "tests/fixtures/douyin/live-page.synthetic.html",
    "requirements/runtime.lock",
    "requirements/dev.lock",
    "docs/P1A_IMPLEMENTATION_PLAN.md",
    "docs/P1C_IMPLEMENTATION_PLAN.md",
    "docs/P1C_IMPLEMENTATION_REPORT.md",
    "docs/protocol/P1C_INTERACTIVE_EVIDENCE_RUNBOOK.md",
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


def _verify_recipient_contract(errors: list[str]) -> None:
    try:
        contract = RecipientContract.load(
            ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json"
        )
        if contract.target_method != TARGET_METHOD:
            errors.append("协议 contract 目标 method 不一致")
        if contract.live_verified:
            errors.append("尚无经审查现场样本，provisional contract 不得标记 live_verified=true")
        replay_contract = RecipientContract.load(
            ROOT / "tests" / "replay" / "contracts" / "recipient.synthetic-v1.json"
        )
        if replay_contract.live_verified:
            errors.append("合成 replay contract 不得标记 live_verified=true")
        replay = run_fixture(
            ROOT / "tests" / "replay" / "fixtures" / "recipient-strict-unknown.synthetic.json",
            contract=replay_contract,
        )
        if not replay.fixture_synthetic or replay.fixture_live_verified:
            errors.append("recipient replay fixture 的 synthetic/live_verified 标志不安全")
        if replay.target_messages <= 0 or replay.target_decode_failures != 0:
            errors.append("recipient replay 未稳定解码目标消息")
        intervals = replay.reducer_snapshot.get("intervals")
        if not isinstance(intervals, list) or not any(
            isinstance(item, dict)
            and item.get("status") == "unknown"
            and item.get("reason") == "im_disconnected"
            for item in intervals
        ):
            errors.append("recipient replay 未覆盖 Unknown(im_disconnected)")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"协议 contract/replay 校验失败: {exc}")


def _verify_live_page_fixture(errors: list[str]) -> None:
    try:
        result = inspect_live_page(
            (ROOT / "tests" / "fixtures" / "douyin" / "live-page.synthetic.html").read_bytes(),
            room_url="73504089679",
            http_status=200,
            final_url="https://live.douyin.com/73504089679",
        )
        if len(result.candidates) != 3:
            errors.append(f"直播页 fixture 应解析 3 个流候选，实际 {len(result.candidates)}")
        public = json.dumps(result.snapshot.to_public_dict(), ensure_ascii=False, sort_keys=True)
        if "SECRET" in public or "PRIVATE" in public:
            errors.append("直播页公开结果泄露合成签名值")
        public_candidates = result.snapshot.to_public_dict()["stream_candidates"]
        if any("url" in item for item in public_candidates):
            errors.append("直播页公开候选包含完整 url 字段")
        if any("path" in item for item in public_candidates):
            errors.append("直播页公开候选包含原始 path 字段")
        if any(
            not isinstance(item.get("path_sha256"), str)
            or len(str(item.get("path_sha256"))) != 64
            for item in public_candidates
        ):
            errors.append("直播页公开候选缺少 path SHA-256")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"直播页 fixture 校验失败: {exc}")


def main() -> int:
    errors: list[str] = []
    if verify_baseline() != 0:
        errors.append("仓库基线校验失败")

    for rel in _REQUIRED:
        path = ROOT / rel
        if not path.is_file() or path.is_symlink():
            errors.append(f"缺少当前阶段必需普通文件: {rel}")

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

    versions = [migration.version for migration in MIGRATIONS]
    if versions != sorted(set(versions)) or not versions or versions[-1] < 2:
        errors.append(f"SQLite migration 版本无效: {versions}")

    _verify_recipient_contract(errors)
    _verify_live_page_fixture(errors)

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
        print("[失败] 当前阶段源码校验发现问题：")
        for item in errors:
            print(f"  - {item}")
        return 1
    print("[通过] P1A/P1B/P1C 房间、媒体、recipient 与证据安全边界正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
