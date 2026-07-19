from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.douyin.live_page import (  # noqa: E402
    DouyinLivePageClient,
    LivePageError,
    inspect_live_page,
    normalize_room_reference,
)


def inspect_response(response: httpx.Response, *, requested_room_id: str) -> dict[str, object]:
    reference = normalize_room_reference(requested_room_id)
    result = inspect_live_page(
        response.content,
        room_url=reference.room_url,
        http_status=response.status_code,
        final_url=str(response.url),
    )
    return result.snapshot.to_public_dict()


async def run_preflight_async(room_id: str, *, timeout_seconds: float = 25.0) -> dict[str, object]:
    client = DouyinLivePageClient(timeout_seconds=timeout_seconds)
    try:
        result = await client.check(room_id)
        return result.snapshot.to_public_dict()
    finally:
        await client.close()


def run_preflight(room_id: str, *, timeout_seconds: float = 25.0) -> dict[str, object]:
    return asyncio.run(run_preflight_async(room_id, timeout_seconds=timeout_seconds))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抖音直播页安全预检（不保存正文或凭据）")
    parser.add_argument("--room-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=25.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run_preflight(args.room_id.strip(), timeout_seconds=args.timeout)
    except (ValueError, LivePageError) as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
