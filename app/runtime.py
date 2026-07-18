from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ToolStatus:
    name: str
    configured: str
    executable: str | None
    ready: bool
    version: str
    error: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _resolve_executable(configured: str) -> str | None:
    candidate = Path(configured).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        return str(candidate.resolve()) if candidate.is_file() else None
    return shutil.which(configured)


async def check_tool(name: str, configured: str, *, timeout: float = 5.0) -> ToolStatus:
    executable = _resolve_executable(configured)
    if executable is None:
        return ToolStatus(name, configured, None, False, "", "未找到可执行文件")
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C"},
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.communicate()
        return ToolStatus(name, configured, executable, False, "", f"{timeout:g} 秒内未响应")
    except OSError as exc:
        return ToolStatus(name, configured, executable, False, "", str(exc))
    text = (stdout or stderr).decode("utf-8", errors="replace").strip()
    first_line = text.splitlines()[0][:300] if text else ""
    if process.returncode != 0:
        return ToolStatus(
            name,
            configured,
            executable,
            False,
            first_line,
            f"退出码 {process.returncode}",
        )
    return ToolStatus(name, configured, executable, True, first_line, "")
