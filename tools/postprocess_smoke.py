from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Database  # noqa: E402
from app.postprocess import ExportPlanner, PostprocessRepository, PostprocessService  # noqa: E402
from app.postprocess.executor import FFmpegPostprocessExecutor  # noqa: E402
from app.settings import Settings  # noqa: E402
from tools.recording_session_smoke import run_smoke as run_recording_smoke  # noqa: E402


class _DiagnosticExecutor:
    def __init__(self, delegate: FFmpegPostprocessExecutor, *, smoke_root: Path) -> None:
        self.delegate = delegate
        self.smoke_root = smoke_root
        self.last_error: str | None = None

    async def run_output(self, **kwargs):
        try:
            return await self.delegate.run_output(**kwargs)
        except Exception as exc:
            rendered = f"{type(exc).__name__}: {exc}"
            self.last_error = rendered.replace(str(self.smoke_root), "<smoke-root>")[:500]
            raise


async def _wait_for_job(repository: PostprocessRepository, job_id: str) -> object:
    for _ in range(600):
        job = await repository.get_job(job_id)
        if job.status in {"succeeded", "failed", "canceled"}:
            return job
        await asyncio.sleep(0.05)
    raise RuntimeError("postprocess smoke 等待任务超时")


def _probe(path: Path, ffprobe: str) -> dict[str, object]:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("ffprobe 无法读取 postprocess 输出")
    return json.loads(result.stdout)


async def run_smoke(output_dir: Path, *, duration_seconds: float = 2.0) -> dict[str, object]:
    if not 1 <= duration_seconds <= 30:
        raise ValueError("duration_seconds 必须在 1–30 之间")
    settings = Settings.load()
    ffmpeg = shutil.which(settings.ffmpeg_path) or settings.ffmpeg_path
    ffprobe = shutil.which(settings.ffprobe_path) or settings.ffprobe_path
    if not Path(ffmpeg).is_file() and shutil.which(ffmpeg) is None:
        raise RuntimeError("未找到 FFmpeg")
    if not Path(ffprobe).is_file() and shutil.which(ffprobe) is None:
        raise RuntimeError("未找到 ffprobe")

    output_dir = output_dir.expanduser().absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise RuntimeError(f"输出目录已存在或为符号链接，拒绝覆盖: {output_dir}")
    output_dir.mkdir(parents=True)
    source_dir = output_dir / "source-session"
    recording = await run_recording_smoke(
        source_dir,
        duration_seconds=duration_seconds,
    )

    database = Database(source_dir / "userdata" / "smoke.db")
    await database.initialize()
    repository = PostprocessRepository(database)
    diagnostic_executor = _DiagnosticExecutor(
        FFmpegPostprocessExecutor(
            ffmpeg_path=str(ffmpeg),
            records_dir=source_dir / "records",
            userdata_dir=source_dir / "userdata",
        ),
        smoke_root=output_dir,
    )
    service = PostprocessService(
        repository=repository,
        planner=ExportPlanner(database),
        executor=diagnostic_executor,
        runtime_instance_id="recording-smoke-runtime",
        enabled=True,
        max_attempts=2,
    )
    await service.start()
    try:
        created = await service.create_export(str(recording["session_id"]))
        job = await _wait_for_job(repository, created.id)
        if job.status != "succeeded" or not job.outputs:
            detail = diagnostic_executor.last_error or "none"
            raise RuntimeError(
                f"postprocess smoke 任务失败: {job.error_code or job.status}; "
                f"diagnostic={detail}"
            )
        outputs = []
        for item in job.outputs:
            final_path = source_dir / "records" / item.relative_path
            if (
                item.status != "succeeded"
                or not final_path.is_file()
                or final_path.is_symlink()
                or final_path.stat().st_size <= 0
                or final_path.with_suffix(final_path.suffix + ".writing").exists()
            ):
                raise RuntimeError("postprocess smoke 输出状态或文件不完整")
            probe = _probe(final_path, str(ffprobe))
            outputs.append(
                {
                    "relative_path": item.relative_path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                    "duration": float(probe["format"]["duration"]),
                    "stream_types": sorted(
                        str(stream["codec_type"]) for stream in probe.get("streams", [])
                    ),
                }
            )
        if any(value in json.dumps(outputs) for value in ("Cookie", "wsSecret", "signature")):
            raise RuntimeError("postprocess smoke 公开报告包含敏感键")
        return {
            "ok": True,
            "schema_version": await database.schema_version(),
            "source_segment_count": len(recording["media"]),
            "job_id": job.id,
            "job_status": job.status,
            "attempts": job.attempts,
            "output_count": len(outputs),
            "outputs": outputs,
            "contract_live_verified": recording["contract_live_verified"],
        }
    finally:
        await service.close()
        await database.close()


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="使用两段本地 lavfi MKV 验证 P3A 后处理任务闭环"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "userdata" / "postprocess-smoke" / stamp,
    )
    parser.add_argument("--duration", type=float, default=2.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = asyncio.run(run_smoke(args.output_dir, duration_seconds=args.duration))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
