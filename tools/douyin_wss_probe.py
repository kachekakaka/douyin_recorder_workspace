from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin.probe import ProbeOptions, run_probe  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P0 单房间抖音 WSS method/raw-frame 验证工具（不打印凭据或签名 URL）"
    )
    parser.add_argument(
        "--websocket-url",
        default=os.getenv("DOUYIN_WSS_URL", ""),
        help="完整 wss:// URL；优先使用 URL 文件，避免命令历史泄露签名",
    )
    parser.add_argument(
        "--websocket-url-file",
        type=Path,
        default=Path(os.environ["DOUYIN_WSS_URL_FILE"])
        if os.getenv("DOUYIN_WSS_URL_FILE")
        else None,
        help="只含一行完整 WSS URL 的私有文件；禁止提交",
    )
    parser.add_argument("--room-url", default="", help="仅写入去掉 query 的直播间 URL")
    parser.add_argument("--origin", default="https://live.douyin.com")
    parser.add_argument("--cookie-file", type=Path, help="私有 Cookie 文本文件；禁止提交")
    parser.add_argument("--header-file", type=Path, help="私有请求头 JSON 文件；禁止提交")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--max-frames", type=int, default=500)
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    parser.add_argument(
        "--send-ack",
        action="store_true",
        help="显式启用 ACK；默认关闭，现场确认当前 envelope 行为后再使用",
    )
    parser.add_argument(
        "--send-heartbeat",
        action="store_true",
        help="显式启用应用层 heartbeat ping；默认关闭",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json",
    )
    return parser


def _load_url(args: argparse.Namespace) -> str:
    if args.websocket_url_file is not None:
        path = args.websocket_url_file
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"WSS URL 文件不存在或类型不安全: {path}")
        return path.read_text(encoding="utf-8").strip()
    return str(args.websocket_url or "").strip()


async def async_main() -> int:
    args = build_parser().parse_args()
    websocket_url = _load_url(args)
    if not websocket_url:
        raise SystemExit(
            "请通过 --websocket-url-file、DOUYIN_WSS_URL_FILE 或 DOUYIN_WSS_URL 提供完整 WSS 地址"
        )
    output = args.output_dir
    if output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = ROOT / "userdata" / "protocol-probes" / f"probe-{stamp}"
    report = await run_probe(
        ProbeOptions(
            websocket_url=websocket_url,
            output_dir=output,
            contract_path=args.contract,
            room_url=args.room_url,
            origin=args.origin,
            cookie_file=args.cookie_file,
            header_file=args.header_file,
            duration_seconds=args.duration,
            max_frames=args.max_frames,
            send_ack=args.send_ack,
            send_application_heartbeat=args.send_heartbeat,
            heartbeat_seconds=args.heartbeat_seconds,
        )
    )
    safe_summary = {
        "probe_id": report.probe_id,
        "transport_connected": report.transport_connected,
        "transport_frames_received": report.transport_frames_received,
        "transport_live_verified": report.transport_live_verified,
        "contract_live_verified": report.contract_live_verified,
        "websocket_host": report.websocket_host,
        "frame_count": report.frame_count,
        "method_counts": report.method_counts,
        "target_message_count": report.target_message_count,
        "target_decode_success_count": report.target_decode_success_count,
        "target_decode_failure_count": report.target_decode_failure_count,
        "output_dir": str(output),
    }
    print(json.dumps(safe_summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
