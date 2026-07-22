from __future__ import annotations

from pathlib import Path

import pytest

from app.postprocess.executor import FFmpegPostprocessExecutor


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
