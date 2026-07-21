from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.media import (
    RecorderConfigurationError,
    RecorderProcessSpec,
    RecorderSupervisor,
    RecordingPlan,
    StreamInput,
    parse_segment_csv,
    progress_snapshot,
    redact_argv,
    sanitize_log_line,
)


def test_progress_and_segment_csv_parsing(tmp_path: Path) -> None:
    snapshot = progress_snapshot(
        {
            "frame": "25",
            "fps": "24.98",
            "total_size": "123456",
            "out_time_us": "1000000",
            "speed": "1.00x",
            "progress": "continue",
        },
        received_at_ms=10,
    )
    assert snapshot.frame == 25
    assert snapshot.fps == 24.98
    assert snapshot.out_time_us == 1_000_000

    csv_path = tmp_path / "segments.csv"
    csv_path.write_text("00000.mkv,0.000000,10.000000\n00001.mkv,10,20.5\n", encoding="utf-8")
    rows = parse_segment_csv(csv_path)
    assert [row.filename for row in rows] == ["00000.mkv", "00001.mkv"]
    assert rows[1].end_seconds == 20.5


def test_redaction_never_returns_cookie_or_query_values() -> None:
    stream = StreamInput(
        url="https://pull.example.douyincdn.com/live/a.flv?signature=SECRET&expire=9",
        protocol="flv",
        quality="origin",
        headers=(("Cookie", "ttwid=PRIVATE"), ("Referer", "https://live.douyin.com/")),
    )
    argv = (
        "ffmpeg",
        "-headers",
        stream.header_blob(),
        "-i",
        stream.url,
        "out.mkv",
    )
    redacted = " ".join(redact_argv(argv))
    assert "SECRET" not in redacted
    assert "PRIVATE" not in redacted
    assert "signature=<redacted>" in redacted
    assert "live/a.flv" not in redacted
    assert "<redacted-path>.flv" in redacted
    assert "Cookie: <redacted>" in sanitize_log_line("Cookie: ttwid=PRIVATE")


def test_recorder_supervisor_consumes_both_pipes_and_stops(tmp_path: Path) -> None:
    async def scenario() -> None:
        script = tmp_path / "fake_recorder.py"
        script.write_text(
            """
import signal
import sys
import time
running = True
def stop(*_args):
    global running
    running = False
if hasattr(signal, 'SIGINT'):
    signal.signal(signal.SIGINT, stop)
if hasattr(signal, 'SIGBREAK'):
    signal.signal(signal.SIGBREAK, stop)
print('frame=1', flush=True)
print('out_time_us=1000', flush=True)
print('progress=continue', flush=True)
print(
    'input https://pull.example.douyincdn.com/a.flv?' + 'signature=SECRET',
    file=sys.stderr,
    flush=True,
)
while running:
    time.sleep(0.05)
print('frame=2', flush=True)
print('out_time_us=2000', flush=True)
print('progress=end', flush=True)
""".strip()
            + "\n",
            encoding="utf-8",
        )
        spec = RecorderProcessSpec(
            argv=(sys.executable, "-S", str(script)),
            redacted_argv=(sys.executable, "-S", str(script)),
            stderr_log_path=tmp_path / "ffmpeg.log",
            cwd=tmp_path,
        )

        def failing_callback(_value: object) -> None:
            raise RuntimeError("synthetic callback failure")

        supervisor = RecorderSupervisor(
            spec,
            on_progress=failing_callback,
            on_stderr=failing_callback,
        )
        await supervisor.start()
        await asyncio.sleep(0.15)
        result = await supervisor.stop(graceful_timeout=2, terminate_timeout=1)
        assert result.returncode == 0
        assert result.last_progress is not None
        assert result.last_progress.progress == "end"
        assert result.stderr_lines == 1
        assert result.callback_error_count == 3
        log = spec.stderr_log_path.read_text(encoding="utf-8")
        assert "SECRET" not in log
        assert "signature=<redacted>" in log

    asyncio.run(scenario())


def test_recording_plan_rejects_unsafe_hosts_and_unknown_options(tmp_path: Path) -> None:
    with pytest.raises(RecorderConfigurationError, match="IP 字面量"):
        RecordingPlan(
            ffmpeg_path="ffmpeg",
            room_key="group-a",
            session_id="session-a",
            stream=StreamInput(
                url="http://8.8.8.8/live.flv",
                protocol="flv",
                quality="origin",
            ),
            output_root=tmp_path,
        )

    with pytest.raises(RecorderConfigurationError, match="CDN 范围"):
        RecordingPlan(
            ffmpeg_path="ffmpeg",
            room_key="group-a",
            session_id="session-a",
            stream=StreamInput(
                url="https://attacker.example/live.flv?token=SECRET",
                protocol="flv",
                quality="origin",
            ),
            output_root=tmp_path,
        )

    with pytest.raises(RecorderConfigurationError, match="不允许的 FFmpeg 输入选项"):
        RecordingPlan(
            ffmpeg_path="ffmpeg",
            room_key="group-a",
            session_id="session-a",
            stream=StreamInput(
                url="https://pull.example.douyincdn.com/live.flv",
                protocol="flv",
                quality="origin",
            ),
            output_root=tmp_path,
            extra_input_args=("-loglevel", "debug"),
        )


def test_recording_plan_rejects_path_segments_ports_and_invalid_stream_metadata(
    tmp_path: Path,
) -> None:
    safe_stream = StreamInput(
        url="https://pull.example.douyincdn.com/live/a.flv",
        protocol="flv",
        quality="origin",
    )
    for room_key, session_id in ((".", "session-a"), ("..", "session-a"), ("group-a", "..")):
        with pytest.raises(RecorderConfigurationError, match="路径段"):
            RecordingPlan(
                ffmpeg_path="ffmpeg",
                room_key=room_key,
                session_id=session_id,
                stream=safe_stream,
                output_root=tmp_path,
            )

    for url in (
        "https://pull.example.douyincdn.com:99999/live/a.flv",
        "https://pull.example.douyincdn.com:80/live/a.flv",
        "http://pull.example.douyincdn.com:443/live/a.flv",
    ):
        with pytest.raises(RecorderConfigurationError, match="端口"):
            RecordingPlan(
                ffmpeg_path="ffmpeg",
                room_key="group-a",
                session_id="session-a",
                stream=StreamInput(url=url, protocol="flv", quality="origin"),
                output_root=tmp_path,
            )

    overlong_url = "https://pull.example.douyincdn.com/" + ("a" * 16_384) + ".flv"
    with pytest.raises(RecorderConfigurationError, match="长度"):
        RecordingPlan(
            ffmpeg_path="ffmpeg",
            room_key="group-a",
            session_id="session-a",
            stream=StreamInput(url=overlong_url, protocol="flv", quality="origin"),
            output_root=tmp_path,
        )

    for url in (
        "https://@pull.example.douyincdn.com/live/a.flv",
        "https://pull.example.douyincdn.com/live/a.flv#fragment",
    ):
        with pytest.raises(RecorderConfigurationError):
            RecordingPlan(
                ffmpeg_path="ffmpeg",
                room_key="group-a",
                session_id="session-a",
                stream=StreamInput(url=url, protocol="flv", quality="origin"),
                output_root=tmp_path,
            )

    with pytest.raises(RecorderConfigurationError, match="协议"):
        RecordingPlan(
            ffmpeg_path="ffmpeg",
            room_key="group-a",
            session_id="session-a",
            stream=StreamInput(
                url="https://pull.example.douyincdn.com/live/a.flv",
                protocol="rtmp",
                quality="origin",
            ),
            output_root=tmp_path,
        )


def test_recording_plan_refuses_overwrite_and_dangerous_headers(tmp_path: Path) -> None:
    plan = RecordingPlan(
        ffmpeg_path="ffmpeg",
        room_key="group-a",
        session_id="session-a",
        stream=StreamInput(
            url="https://pull.example.douyincdn.com/live/a.flv?signature=SECRET",
            protocol="flv",
            quality="origin",
        ),
        output_root=tmp_path,
    )
    spec = plan.process_spec()
    assert "-n" in spec.argv
    assert "-y" not in spec.argv
    assert "SECRET" not in " ".join(spec.redacted_argv)

    unsafe = StreamInput(
        url="https://pull.example.douyincdn.com/live/a.flv",
        protocol="flv",
        quality="origin",
        headers=(("Host", "127.0.0.1"),),
    )
    with pytest.raises(RecorderConfigurationError, match="不允许"):
        unsafe.header_blob()


def test_recording_plan_rejects_existing_outputs_and_symlink_paths(tmp_path: Path) -> None:
    stream = StreamInput(
        url="https://pull.example.douyincdn.com/live/a.flv",
        protocol="flv",
        quality="origin",
    )
    plan = RecordingPlan(
        ffmpeg_path="ffmpeg",
        room_key="group-a",
        session_id="session-a",
        stream=stream,
        output_root=tmp_path,
    )
    plan.media_dir.mkdir(parents=True)
    (plan.media_dir / "00000.mkv").write_bytes(b"existing")
    with pytest.raises(RecorderConfigurationError, match="拒绝覆盖"):
        plan.process_spec()

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    linked_plan = RecordingPlan(
        ffmpeg_path="ffmpeg",
        room_key="group-b",
        session_id="session-b",
        stream=stream,
        output_root=linked_root,
    )
    with pytest.raises(RecorderConfigurationError, match="符号链接"):
        linked_plan.process_spec()
