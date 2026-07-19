from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.media import RecorderProcessSpec, RecorderSupervisor, parse_segment_csv  # noqa: E402
from app.settings import Settings  # noqa: E402


async def run_smoke(output_dir: Path, *, duration_seconds: float = 3.0) -> dict[str, object]:
    if not 1 <= duration_seconds <= 30:
        raise ValueError("duration_seconds 必须在 1–30 之间")
    settings = Settings.load()
    ffmpeg = shutil.which(settings.ffmpeg_path) or (
        settings.ffmpeg_path if Path(settings.ffmpeg_path).is_file() else None
    )
    if ffmpeg is None:
        raise RuntimeError("未找到 FFmpeg")

    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError(f"输出目录已存在，拒绝覆盖: {output_dir}")
    media_dir = output_dir / "media"
    media_dir.mkdir(parents=True)
    segment_list = media_dir / "segments.csv"
    log_path = output_dir / "ffmpeg.log"
    output_pattern = media_dir / "%05d.mkv"
    argv = (
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=25",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000",
        "-t",
        str(duration_seconds),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "mpeg4",
        "-q:v",
        "5",
        "-c:a",
        "aac",
        "-f",
        "segment",
        "-segment_time",
        "1",
        "-reset_timestamps",
        "1",
        "-segment_list",
        str(segment_list),
        "-segment_list_type",
        "csv",
        "-progress",
        "pipe:1",
        str(output_pattern),
    )
    spec = RecorderProcessSpec(
        argv=argv,
        redacted_argv=argv,
        stderr_log_path=log_path,
        cwd=output_dir,
    )
    supervisor = RecorderSupervisor(spec)
    await supervisor.start()
    result = await supervisor.wait()
    segments = parse_segment_csv(segment_list)
    files = sorted(path.name for path in media_dir.glob("*.mkv"))
    if result.returncode != 0 or not files:
        raise RuntimeError(f"FFmpeg smoke 失败，退出码 {result.returncode}")
    return {
        "ok": True,
        "output_dir": str(output_dir),
        "returncode": result.returncode,
        "stop_stage": result.stop_stage,
        "last_progress": result.last_progress.to_dict() if result.last_progress else None,
        "segment_rows": len(segments),
        "media_files": files,
        "stderr_lines": result.stderr_lines,
    }


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(description="使用本地 lavfi 验证 FFmpeg Supervisor")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "userdata" / "ffmpeg-smoke" / stamp,
    )
    parser.add_argument("--duration", type=float, default=3.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run_smoke(args.output_dir, duration_seconds=args.duration))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
