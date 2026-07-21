from __future__ import annotations

import asyncio
import hashlib
import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from app.postprocess.models import ExportOutputPlan


class PostprocessExecutionError(RuntimeError):
    """Raised when a postprocess output violates a boundary or FFmpeg fails."""


@dataclass(frozen=True, slots=True)
class OutputExecutionResult:
    returncode: int
    stop_stage: str
    canceled: bool
    size_bytes: int | None = None
    sha256: str | None = None


class FFmpegPostprocessExecutor:
    def __init__(
        self,
        *,
        ffmpeg_path: str,
        records_dir: Path,
        userdata_dir: Path,
        graceful_timeout: float = 3.0,
        terminate_timeout: float = 3.0,
    ) -> None:
        self.ffmpeg_path = ffmpeg_path
        self.records_dir = records_dir
        self.userdata_dir = userdata_dir
        self.graceful_timeout = graceful_timeout
        self.terminate_timeout = terminate_timeout

    async def run_output(
        self,
        *,
        job_id: str,
        attempt_id: str,
        output: ExportOutputPlan,
        cancel_event: asyncio.Event,
        allow_existing_final: bool = False,
    ) -> OutputExecutionResult:
        records_root = self._safe_root(self.records_dir, label="records")
        work_root = self._safe_root(self.userdata_dir, label="userdata") / "postprocess-work"
        work_root.mkdir(parents=True, exist_ok=True)
        if work_root.is_symlink():
            raise PostprocessExecutionError("postprocess work 目录不得是符号链接")
        work_dir = work_root / attempt_id / f"interval-{output.interval_id}"
        if work_dir.exists() or work_dir.is_symlink():
            raise PostprocessExecutionError("postprocess attempt 工作目录已存在")
        work_dir.mkdir(parents=True)

        final_path = self._resolve_output(records_root, output.relative_path)
        writing_path = final_path.with_suffix(final_path.suffix + ".writing")
        if final_path.is_symlink():
            raise PostprocessExecutionError("导出目标不得是符号链接")
        if final_path.exists():
            if not allow_existing_final or not final_path.is_file():
                raise PostprocessExecutionError("导出目标已存在，拒绝覆盖")
            if writing_path.exists() or writing_path.is_symlink():
                raise PostprocessExecutionError("既有导出旁存在 writing 文件")
            size_bytes = final_path.stat().st_size
            if size_bytes <= 0:
                raise PostprocessExecutionError("既有导出文件为空")
            with suppress(OSError):
                work_dir.rmdir()
            with suppress(OSError):
                work_dir.parent.rmdir()
            return OutputExecutionResult(
                returncode=0,
                stop_stage="recovered-existing",
                canceled=False,
                size_bytes=size_bytes,
                sha256=self._sha256_file(final_path),
            )
        if writing_path.exists() or writing_path.is_symlink():
            raise PostprocessExecutionError("导出 writing 文件已存在，拒绝覆盖")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlink_components(final_path.parent, records_root)

        concat_path = work_dir / "sources.ffconcat"
        source_paths: list[Path] = []
        for source in output.sources:
            candidate = self._resolve_source(records_root, source.relative_path)
            source_paths.append(candidate)
        concat_path.write_text(
            "ffconcat version 1.0\n"
            + "".join(f"file '{self._ffconcat_path(path)}'\n" for path in source_paths),
            encoding="utf-8",
            newline="\n",
        )

        argv = (
            self.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-n",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-ss",
            f"{output.trim_start_seconds:.6f}",
            "-t",
            f"{output.duration_seconds:.6f}",
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "matroska",
            str(writing_path),
        )
        creationflags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creationflags = getattr(asyncio.subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creationflags == 0:
                creationflags = 0x00000200
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
        stdout_task = asyncio.create_task(self._drain(process.stdout))
        stderr_task = asyncio.create_task(self._drain(process.stderr))
        wait_task = asyncio.create_task(process.wait())
        cancel_task = asyncio.create_task(cancel_event.wait())
        stop_stage = "natural"
        canceled = False
        try:
            done, _ = await asyncio.wait(
                {wait_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancel_task in done and cancel_event.is_set() and not wait_task.done():
                canceled = True
                stop_stage = await self._stop_process(process)
            returncode = await wait_task
            await asyncio.gather(stdout_task, stderr_task)
            if canceled:
                with suppress(FileNotFoundError):
                    writing_path.unlink()
                return OutputExecutionResult(returncode, stop_stage, True)
            if returncode != 0:
                with suppress(FileNotFoundError):
                    writing_path.unlink()
                return OutputExecutionResult(returncode, stop_stage, False)
            if not writing_path.is_file() or writing_path.is_symlink():
                raise PostprocessExecutionError("FFmpeg 未生成安全 writing 文件")
            with writing_path.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(writing_path, final_path)
            self._fsync_directory(final_path.parent)
            size_bytes = final_path.stat().st_size
            if size_bytes <= 0:
                raise PostprocessExecutionError("导出文件为空")
            return OutputExecutionResult(
                returncode=returncode,
                stop_stage=stop_stage,
                canceled=False,
                size_bytes=size_bytes,
                sha256=self._sha256_file(final_path),
            )
        finally:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            for task in (stdout_task, stderr_task, wait_task):
                if not task.done():
                    task.cancel()
            with suppress(OSError):
                concat_path.unlink()
            with suppress(OSError):
                work_dir.rmdir()
            with suppress(OSError):
                work_dir.parent.rmdir()

    async def _stop_process(self, process: asyncio.subprocess.Process) -> str:
        if process.returncode is not None:
            return "natural"
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(process.pid, signal.SIGINT)
        except (OSError, ProcessLookupError):
            with suppress(ProcessLookupError):
                process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self.graceful_timeout)
            return "graceful"
        except TimeoutError:
            pass
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=self.terminate_timeout)
            return "terminate"
        except TimeoutError:
            pass
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        return "kill"

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while await stream.read(64 * 1024):
            pass

    @staticmethod
    def _safe_root(path: Path, *, label: str) -> Path:
        absolute = path.expanduser().absolute()
        if absolute.is_symlink() or not absolute.is_dir():
            raise PostprocessExecutionError(f"{label} 根目录不存在或为符号链接")
        return absolute.resolve(strict=True)

    @classmethod
    def _resolve_source(cls, root: Path, relative_path: str) -> Path:
        if "\x00" in relative_path or "\r" in relative_path or "\n" in relative_path:
            raise PostprocessExecutionError("媒体相对路径包含非法字符")
        candidate = root / Path(relative_path)
        cls._reject_symlink_components(candidate, root)
        if candidate.is_symlink() or not candidate.is_file():
            raise PostprocessExecutionError("源媒体不存在或类型不安全")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PostprocessExecutionError("源媒体路径越界") from exc
        return resolved

    @classmethod
    def _resolve_output(cls, root: Path, relative_path: str) -> Path:
        path = Path(relative_path)
        if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".mkv":
            raise PostprocessExecutionError("导出相对路径无效")
        candidate = root / path
        cls._reject_symlink_components(candidate.parent, root)
        try:
            candidate.absolute().relative_to(root)
        except ValueError as exc:
            raise PostprocessExecutionError("导出路径越界") from exc
        return candidate.absolute()

    @staticmethod
    def _reject_symlink_components(path: Path, root: Path) -> None:
        current = path
        while True:
            if current.exists() and current.is_symlink():
                raise PostprocessExecutionError("路径经过符号链接")
            if current == root:
                return
            if current == current.parent:
                raise PostprocessExecutionError("路径不属于允许根目录")
            current = current.parent

    @staticmethod
    def _ffconcat_path(path: Path) -> str:
        value = path.as_posix()
        if "'" in value or "\n" in value or "\r" in value:
            raise PostprocessExecutionError("源媒体路径不能安全写入 concat 清单")
        return value

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
