from __future__ import annotations

import asyncio
import csv
import hashlib
import ipaddress
import os
import re
import signal
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

ProgressCallback = Callable[["ProgressSnapshot"], Awaitable[None] | None]
StderrCallback = Callable[[str], Awaitable[None] | None]

_SECRET_HEADER_RE = re.compile(
    r"(?i)(cookie|authorization|x[-_]?[a-z0-9_-]*(?:token|sign|signature))\s*:\s*[^\r\n]+"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SAFE_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_ALLOWED_EXTRA_INPUT_OPTIONS = {
    "-rw_timeout",
    "-timeout",
    "-reconnect",
    "-reconnect_streamed",
    "-reconnect_delay_max",
}
_ALLOWED_STREAM_SUFFIXES = (
    "douyincdn.com",
    "douyin.com",
    "bytecdn.cn",
    "byteimg.com",
    "amemv.com",
    "snssdk.com",
    "zijieapi.com",
    "bytedance.com",
)
_SAFE_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,80}$")
_FORBIDDEN_HEADER_NAMES = {"host", "connection", "content-length", "transfer-encoding"}
_ALLOWED_PROTOCOLS = {"flv", "hls"}
_ALLOWED_QUALITIES = {"origin", "uhd", "hd", "sd", "ld", "md", "unknown"}
_MAX_STREAM_URL_CHARS = 16_384


class RecorderConfigurationError(ValueError):
    """Raised when an FFmpeg recording plan is unsafe or invalid."""


def _reject_symlink_components(path: Path, *, stop_at: Path | None = None) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise RecorderConfigurationError(f"录制输出路径包含符号链接: {current}")
        if current == current.parent or (stop_at is not None and current == stop_at):
            return
        current = current.parent


def _prepare_recording_output(plan: RecordingPlan) -> None:
    output_root = plan.output_root.expanduser().absolute()
    _reject_symlink_components(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    root_resolved = output_root.resolve(strict=True)

    session_dir = output_root / plan.room_key / plan.session_id
    media_dir = session_dir / "media"
    _reject_symlink_components(session_dir, stop_at=output_root)
    _reject_symlink_components(media_dir, stop_at=output_root)
    media_dir.mkdir(parents=True, exist_ok=True)
    if session_dir.is_symlink() or media_dir.is_symlink():
        raise RecorderConfigurationError("录制 session 或媒体目录不得是符号链接")
    try:
        session_dir.resolve(strict=True).relative_to(root_resolved)
        media_dir.resolve(strict=True).relative_to(root_resolved)
    except ValueError as exc:
        raise RecorderConfigurationError("录制输出目录越界") from exc

    existing = [
        *media_dir.glob("*.mkv"),
        *media_dir.glob("*.ts"),
        *media_dir.glob("*.writing"),
    ]
    if (media_dir / "segments.csv").exists():
        existing.append(media_dir / "segments.csv")
    if existing:
        names = ", ".join(sorted(path.name for path in existing)[:8])
        raise RecorderConfigurationError(f"录制输出已存在，拒绝覆盖: {names}")


@dataclass(frozen=True, slots=True)
class StreamInput:
    url: str
    protocol: str
    quality: str
    headers: tuple[tuple[str, str], ...] = ()

    def header_blob(self) -> str:
        rows: list[str] = []
        for name, value in self.headers:
            if not _SAFE_HEADER_NAME_RE.fullmatch(name):
                raise RecorderConfigurationError(f"请求头名称无效: {name!r}")
            if name.casefold() in _FORBIDDEN_HEADER_NAMES:
                raise RecorderConfigurationError(f"请求头不允许由录制计划覆盖: {name}")
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise RecorderConfigurationError(f"请求头值包含控制字符: {name}")
            rows.append(f"{name}: {value}")
        return "\r\n".join(rows) + ("\r\n" if rows else "")


@dataclass(frozen=True, slots=True)
class RecordingPlan:
    ffmpeg_path: str
    room_key: str
    session_id: str
    stream: StreamInput
    output_root: Path
    segment_seconds: int = 600
    container: str = "mkv"
    copy_codec: bool = True
    extra_input_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (("room_key", self.room_key), ("session_id", self.session_id)):
            if not _SAFE_COMPONENT_RE.fullmatch(value) or value in {".", ".."}:
                raise RecorderConfigurationError(
                    f"{label} 只能包含字母、数字、点、下划线或横线，且不得是路径段"
                )
        if self.stream.protocol not in _ALLOWED_PROTOCOLS:
            raise RecorderConfigurationError("流协议只允许 flv/hls")
        if self.stream.quality not in _ALLOWED_QUALITIES:
            raise RecorderConfigurationError("流画质不在允许范围")
        if self.container not in {"mkv", "ts"}:
            raise RecorderConfigurationError("P1A 原始容器只允许 mkv/ts")
        if not 10 <= self.segment_seconds <= 86_400:
            raise RecorderConfigurationError("segment_seconds 必须在 10–86400 之间")
        if len(self.stream.url) > _MAX_STREAM_URL_CHARS:
            raise RecorderConfigurationError("流 URL 长度超过安全上限")
        if any(ord(char) < 32 or ord(char) == 127 for char in self.stream.url):
            raise RecorderConfigurationError("流 URL 包含控制字符")
        try:
            parsed = urlsplit(self.stream.url)
            port = parsed.port
            host = (parsed.hostname or "").casefold().rstrip(".")
        except ValueError as exc:
            raise RecorderConfigurationError("流 URL 格式或端口无效") from exc
        if parsed.scheme not in {"http", "https"} or not host:
            raise RecorderConfigurationError("录制输入必须是 http(s) 流 URL")
        if parsed.username is not None or parsed.password is not None:
            raise RecorderConfigurationError("流 URL 不得包含用户名或密码")
        expected_port = 80 if parsed.scheme == "http" else 443
        if port not in (None, expected_port):
            raise RecorderConfigurationError("流 URL 只允许协议默认端口")
        if parsed.fragment:
            raise RecorderConfigurationError("流 URL 不得包含 fragment")
        if host == "localhost" or host.endswith(".local"):
            raise RecorderConfigurationError("流 URL 不得指向本机或 .local 主机")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not any(
                host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_STREAM_SUFFIXES
            ):
                raise RecorderConfigurationError(
                    "流 URL 主机不在允许的抖音/字节 CDN 范围"
                ) from None
        else:
            raise RecorderConfigurationError("流 URL 不得使用 IP 字面量")
        if len(self.extra_input_args) % 2:
            raise RecorderConfigurationError("extra_input_args 必须为 option/value 成对参数")
        for option, value in zip(
            self.extra_input_args[0::2], self.extra_input_args[1::2], strict=True
        ):
            if option not in _ALLOWED_EXTRA_INPUT_OPTIONS:
                raise RecorderConfigurationError(f"不允许的 FFmpeg 输入选项: {option}")
            if (
                not value
                or value.startswith("-")
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise RecorderConfigurationError(f"FFmpeg 输入选项值无效: {option}")

    @property
    def session_dir(self) -> Path:
        return self.output_root / self.room_key / self.session_id

    @property
    def media_dir(self) -> Path:
        return self.session_dir / "media"

    @property
    def segment_list_path(self) -> Path:
        return self.media_dir / "segments.csv"

    @property
    def stderr_log_path(self) -> Path:
        return self.session_dir / "ffmpeg.log"

    @property
    def output_pattern(self) -> Path:
        return self.media_dir / f"%05d.{self.container}"

    def process_spec(self) -> RecorderProcessSpec:
        _prepare_recording_output(self)
        args: list[str] = [
            self.ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-n",
        ]
        header_blob = self.stream.header_blob()
        if header_blob:
            args.extend(("-headers", header_blob))
        args.extend(self.extra_input_args)
        args.extend(("-i", self.stream.url, "-map", "0"))
        args.extend(("-c", "copy") if self.copy_codec else ("-c:v", "libx264", "-c:a", "aac"))
        args.extend(
            (
                "-f",
                "segment",
                "-segment_time",
                str(self.segment_seconds),
                "-reset_timestamps",
                "1",
                "-segment_list",
                str(self.segment_list_path),
                "-segment_list_type",
                "csv",
                "-progress",
                "pipe:1",
                str(self.output_pattern),
            )
        )
        return RecorderProcessSpec(
            argv=tuple(args),
            redacted_argv=redact_argv(args),
            stderr_log_path=self.stderr_log_path,
            cwd=self.session_dir,
        )


@dataclass(frozen=True, slots=True)
class RecorderProcessSpec:
    argv: tuple[str, ...]
    redacted_argv: tuple[str, ...]
    stderr_log_path: Path
    cwd: Path
    env: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.argv:
            raise RecorderConfigurationError("录制进程 argv 不能为空")
        if len(self.argv) != len(self.redacted_argv):
            raise RecorderConfigurationError("redacted_argv 必须与 argv 等长")


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    received_at_ms: int
    frame: int | None = None
    fps: float | None = None
    total_size: int | None = None
    out_time_us: int | None = None
    speed: str | None = None
    progress: str = "continue"
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SegmentEntry:
    filename: str
    start_seconds: float | None
    end_seconds: float | None


@dataclass(frozen=True, slots=True)
class RecorderResult:
    started_at_ms: int
    ended_at_ms: int
    returncode: int
    stop_stage: str
    last_progress: ProgressSnapshot | None
    stderr_lines: int
    callback_error_count: int
    redacted_argv: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["last_progress"] = self.last_progress.to_dict() if self.last_progress else None
        return value


def redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<redacted-url>"
    if not parsed.scheme or not parsed.hostname:
        return "<redacted-url>"
    raw_path = parsed.path or "/"
    suffix = Path(raw_path).suffix.casefold()
    if suffix not in {".flv", ".m3u8"}:
        suffix = ""
    safe_path = f"/<redacted-path>{suffix}"
    query_keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    query = "&".join(f"{key}=<redacted>" for key in query_keys)
    return urlunsplit((parsed.scheme, parsed.hostname.casefold(), safe_path, query, ""))


def sanitize_log_line(line: str) -> str:
    sanitized = _SECRET_HEADER_RE.sub(lambda match: f"{match.group(1)}: <redacted>", line)
    return _URL_RE.sub(lambda match: redact_url(match.group(0)), sanitized)


def redact_argv(argv: Iterable[str]) -> tuple[str, ...]:
    values = list(argv)
    output: list[str] = []
    redact_next = False
    for index, value in enumerate(values):
        if redact_next:
            output.append("<redacted>")
            redact_next = False
            continue
        if value in {"-headers", "-cookies", "-authorization"}:
            output.append(value)
            redact_next = True
            continue
        if index > 0 and values[index - 1] == "-i":
            output.append(redact_url(value))
            continue
        output.append(sanitize_log_line(value))
    return tuple(output)


def _int_value(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _float_value(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def progress_snapshot(
    values: dict[str, str], *, received_at_ms: int | None = None
) -> ProgressSnapshot:
    out_time_us = _int_value(values.get("out_time_us"))
    if out_time_us is None:
        out_time_ms = _int_value(values.get("out_time_ms"))
        out_time_us = out_time_ms * 1000 if out_time_ms is not None else None
    return ProgressSnapshot(
        received_at_ms=received_at_ms or int(time.time() * 1000),
        frame=_int_value(values.get("frame")),
        fps=_float_value(values.get("fps")),
        total_size=_int_value(values.get("total_size")),
        out_time_us=out_time_us,
        speed=values.get("speed") or None,
        progress=values.get("progress") or "continue",
        raw=dict(values),
    )


def parse_segment_csv(path: Path) -> tuple[SegmentEntry, ...]:
    if not path.exists():
        return ()
    rows: list[SegmentEntry] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if not row:
                continue
            filename = row[0].strip()
            if not filename:
                continue
            rows.append(
                SegmentEntry(
                    filename=filename,
                    start_seconds=_float_value(row[1]) if len(row) > 1 else None,
                    end_seconds=_float_value(row[2]) if len(row) > 2 else None,
                )
            )
    return tuple(rows)


async def _maybe_call(callback: ProgressCallback | StderrCallback | None, value: object) -> bool:
    if callback is None:
        return True
    try:
        result = callback(value)  # type: ignore[arg-type]
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        # Observability hooks must never stop pipe consumption or deadlock FFmpeg.
        return False
    return True


class RecorderSupervisor:
    """Supervise one long-running recorder process without exposing secret argv values."""

    def __init__(
        self,
        spec: RecorderProcessSpec,
        *,
        on_progress: ProgressCallback | None = None,
        on_stderr: StderrCallback | None = None,
    ) -> None:
        self.spec = spec
        self.on_progress = on_progress
        self.on_stderr = on_stderr
        self.process: asyncio.subprocess.Process | None = None
        self.started_at_ms: int | None = None
        self.last_progress: ProgressSnapshot | None = None
        self.stderr_lines = 0
        self.callback_error_count = 0
        self._stdout_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._wait_lock = asyncio.Lock()
        self._stop_stage = "natural"

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("RecorderSupervisor 已经启动")
        self.spec.cwd.mkdir(parents=True, exist_ok=True)
        self.spec.stderr_log_path.parent.mkdir(parents=True, exist_ok=True)
        creationflags = 0
        start_new_session = os.name != "nt"
        if os.name == "nt":
            creationflags = getattr(os, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        self.started_at_ms = int(time.time() * 1000)
        self.process = await asyncio.create_subprocess_exec(
            *self.spec.argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.spec.cwd,
            env=self.spec.env,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
        self._stdout_task = asyncio.create_task(self._consume_progress(), name="recorder-progress")
        self._stderr_task = asyncio.create_task(self._consume_stderr(), name="recorder-stderr")

    async def wait(self) -> RecorderResult:
        async with self._wait_lock:
            process = self._require_process()
            returncode = await process.wait()
            await self._join_readers()
            return RecorderResult(
                started_at_ms=self.started_at_ms or int(time.time() * 1000),
                ended_at_ms=int(time.time() * 1000),
                returncode=returncode,
                stop_stage=self._stop_stage,
                last_progress=self.last_progress,
                stderr_lines=self.stderr_lines,
                callback_error_count=self.callback_error_count,
                redacted_argv=self.spec.redacted_argv,
            )

    async def stop(
        self,
        *,
        graceful_timeout: float = 10.0,
        terminate_timeout: float = 5.0,
    ) -> RecorderResult:
        if graceful_timeout <= 0 or terminate_timeout <= 0:
            raise ValueError("停止超时必须大于 0")
        process = self._require_process()
        if process.returncode is not None:
            return await self.wait()

        self._stop_stage = "graceful"
        self._signal_graceful(process)
        try:
            await asyncio.wait_for(process.wait(), timeout=graceful_timeout)
        except TimeoutError:
            self._stop_stage = "terminate"
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=terminate_timeout)
            except TimeoutError:
                self._stop_stage = "kill"
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        return await self.wait()

    def _signal_graceful(self, process: asyncio.subprocess.Process) -> None:
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(process.pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            with suppress(ProcessLookupError):
                process.terminate()

    async def _consume_progress(self) -> None:
        process = self._require_process()
        if process.stdout is None:
            return
        values: dict[str, str] = {}
        while raw_line := await process.stdout.readline():
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
            if key.strip() == "progress":
                snapshot = progress_snapshot(values)
                self.last_progress = snapshot
                if not await _maybe_call(self.on_progress, snapshot):
                    self.callback_error_count += 1
                values = {}

    async def _consume_stderr(self) -> None:
        process = self._require_process()
        if process.stderr is None:
            return
        with self.spec.stderr_log_path.open("a", encoding="utf-8", newline="\n") as handle:
            while raw_line := await process.stderr.readline():
                line = sanitize_log_line(raw_line.decode("utf-8", errors="replace").rstrip())
                self.stderr_lines += 1
                handle.write(line + "\n")
                handle.flush()
                if not await _maybe_call(self.on_stderr, line):
                    self.callback_error_count += 1

    async def _join_readers(self) -> None:
        tasks = [task for task in (self._stdout_task, self._stderr_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=False)

    def _require_process(self) -> asyncio.subprocess.Process:
        if self.process is None:
            raise RuntimeError("RecorderSupervisor 尚未启动")
        return self.process


def fingerprint_stream(stream: StreamInput) -> str:
    parsed = urlsplit(stream.url)
    stable = {
        "protocol": stream.protocol,
        "quality": stream.quality,
        "host": (parsed.hostname or "").casefold(),
        "path": parsed.path,
        "header_names": sorted(name.casefold() for name, _ in stream.headers),
    }
    payload = repr(stable).encode()
    return hashlib.sha256(payload).hexdigest()
