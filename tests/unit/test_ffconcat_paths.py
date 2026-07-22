from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.postprocess.executor import FFmpegPostprocessExecutor, PostprocessExecutionError


def test_ffconcat_paths_are_relative_with_uri_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "records" / "room" / "session" / "media" / "00000.mkv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"media")
    work_dir = tmp_path / "userdata" / "postprocess-work" / "attempt" / "interval-1"
    work_dir.mkdir(parents=True)

    assert FFmpegPostprocessExecutor._ffconcat_path(
        source, base_dir=work_dir
    ) == "../../../../records/room/session/media/00000.mkv"

    def fail_relpath(*_args: object, **_kwargs: object) -> str:
        raise ValueError

    monkeypatch.setattr("app.postprocess.executor.os.path.relpath", fail_relpath)
    assert FFmpegPostprocessExecutor._ffconcat_path(
        source, base_dir=work_dir
    ) == source.as_uri()


def test_attempt_component_is_portable_and_deterministic() -> None:
    attempt_id = "0123456789abcdef:1"
    component = FFmpegPostprocessExecutor._attempt_component(attempt_id)
    assert component == hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
    assert len(component) == 64
    assert ":" not in component
    with pytest.raises(PostprocessExecutionError, match="attempt ID"):
        FFmpegPostprocessExecutor._attempt_component("")


def test_fsync_file_is_skipped_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "output.mkv"
    path.write_bytes(b"media")

    def fail_fsync(_descriptor: int) -> None:
        raise AssertionError("os.fsync must not run on Windows")

    monkeypatch.setattr("app.postprocess.executor.os.name", "nt")
    monkeypatch.setattr("app.postprocess.executor.os.fsync", fail_fsync)
    FFmpegPostprocessExecutor._fsync_file(path)
