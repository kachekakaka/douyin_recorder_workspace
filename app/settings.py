from __future__ import annotations

import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.paths import ROOT, PathBoundaryError, RuntimePaths

_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_MAX_ENV_BYTES = 64 * 1024


class SettingsError(ValueError):
    """Raised when a configuration value is invalid."""


def _absolute_runtime_path(value: str | os.PathLike[str], *, root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    # os.path.abspath normalizes the lexical path without following symlinks.
    return Path(os.path.abspath(path))


def _load_runtime_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise SettingsError(f"runtime.env 类型不安全: {path}")
    if path.stat().st_size > _MAX_ENV_BYTES:
        raise SettingsError("runtime.env 超过 64 KiB 上限")
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise SettingsError(f"runtime.env 第 {number} 行缺少 '='")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY_RE.fullmatch(key):
            raise SettingsError(f"runtime.env 第 {number} 行变量名无效: {key!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if "\x00" in value or "\r" in value or "\n" in value:
            raise SettingsError(f"runtime.env 第 {number} 行值无效")
        values[key] = value
    return values


def _deep_merge_defaults(current: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    result = dict(current)
    for key, default in defaults.items():
        if key not in result:
            result[key] = default
        elif isinstance(default, dict) and isinstance(result[key], dict):
            result[key] = _deep_merge_defaults(result[key], default)
    return result


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


def sync_config(template_path: Path, actual_path: Path) -> dict[str, Any]:
    """Create the actual config or append newly introduced default keys safely."""

    if template_path.is_symlink():
        raise SettingsError(f"拒绝读取符号链接配置模板: {template_path}")
    try:
        defaults = json.loads(template_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsError(f"缺少配置模板: {template_path}") from exc
    except json.JSONDecodeError as exc:
        raise SettingsError(f"配置模板 JSON 无效: {exc}") from exc
    if not isinstance(defaults, dict):
        raise SettingsError("配置模板根节点必须是对象")

    if actual_path.is_symlink():
        raise SettingsError(f"拒绝写入符号链接配置: {actual_path}")

    if not actual_path.exists():
        _atomic_write_json(actual_path, defaults)
        return defaults

    try:
        current = json.loads(actual_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SettingsError(f"实际配置 JSON 损坏，未自动覆盖: {exc}") from exc
    if not isinstance(current, dict):
        raise SettingsError("实际配置根节点必须是对象")

    merged = _deep_merge_defaults(current, defaults)
    if merged != current:
        backup = actual_path.with_suffix(actual_path.suffix + ".bak")
        if not backup.exists():
            backup.write_bytes(actual_path.read_bytes())
        _atomic_write_json(actual_path, merged)
    return merged


def _int_value(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{name} 必须是整数") from exc
    if not minimum <= number <= maximum:
        raise SettingsError(f"{name} 必须在 {minimum}–{maximum} 之间")
    return number


def _bool_value(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized not in {"1", "0", "true", "false", "yes", "no"}:
        raise SettingsError(f"{name} 必须是 true/false")
    return normalized in {"1", "true", "yes"}


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class Settings:
    paths: RuntimePaths
    host: str
    port: int
    auth_required: bool
    ffmpeg_path: str
    ffprobe_path: str
    config_path: Path
    protocol_contract_path: Path
    room_manager_enabled: bool
    poll_jitter_seconds: int
    offline_confirmations: int
    max_parallel_checks: int

    @property
    def public_url(self) -> str:
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}/"

    @classmethod
    def load(
        cls,
        *,
        root: Path = ROOT,
        paths: RuntimePaths | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Settings:
        process_env = dict(os.environ if environ is None else environ)
        root = root.resolve()
        base = paths or RuntimePaths.defaults(root)
        initial_config_dir = _absolute_runtime_path(
            process_env.get("DOUYIN_RECORDER_CONFIG_DIR", base.config_dir), root=root
        )
        file_env = _load_runtime_env(initial_config_dir / "runtime.env")
        if "DOUYIN_RECORDER_CONFIG_DIR" in file_env:
            file_config_dir = _absolute_runtime_path(
                file_env["DOUYIN_RECORDER_CONFIG_DIR"], root=root
            )
            if file_config_dir != initial_config_dir:
                raise SettingsError(
                    "config/runtime.env 不能把 DOUYIN_RECORDER_CONFIG_DIR 指向另一个目录"
                )
        env = {**file_env, **process_env}
        config_dir = initial_config_dir
        userdata_dir = _absolute_runtime_path(
            env.get("DOUYIN_RECORDER_USERDATA_DIR", base.userdata_dir), root=root
        )
        records_dir = _absolute_runtime_path(
            env.get("DOUYIN_RECORDER_RECORDS_DIR", base.records_dir), root=root
        )
        database_path = _absolute_runtime_path(
            env.get("DOUYIN_RECORDER_DATABASE_PATH", userdata_dir / "douyin_recorder.db"),
            root=root,
        )
        resolved_paths = replace(
            base,
            root=root,
            config_dir=config_dir,
            userdata_dir=userdata_dir,
            records_dir=records_dir,
            database_path=database_path,
        )
        try:
            resolved_paths.ensure()
        except PathBoundaryError as exc:
            raise SettingsError(str(exc)) from exc

        template_path = resolved_paths.config_dir / "config.json.default"
        source_template = root / "config" / "config.json.default"
        if (
            not template_path.exists()
            and source_template.exists()
            and template_path != source_template
        ):
            template_path.write_bytes(source_template.read_bytes())
        config_path = resolved_paths.config_dir / "config.json"
        raw = sync_config(template_path, config_path)
        server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
        poll = raw.get("poll") if isinstance(raw.get("poll"), dict) else {}

        host = env.get("DOUYIN_RECORDER_HOST", str(server.get("host", "127.0.0.1"))).strip()
        if not host or any(char.isspace() for char in host):
            raise SettingsError("server.host 不能为空或包含空白")
        port = _int_value(
            env.get("DOUYIN_RECORDER_PORT", server.get("port", 3399)),
            "server.port",
            minimum=1,
            maximum=65535,
        )
        auth_required = _bool_value(server.get("auth_required", False), "server.auth_required")
        if "DOUYIN_RECORDER_AUTH_REQUIRED" in env:
            auth_required = _bool_value(
                env["DOUYIN_RECORDER_AUTH_REQUIRED"], "DOUYIN_RECORDER_AUTH_REQUIRED"
            )

        if auth_required:
            raise SettingsError("P0 尚未实现管理员认证；server.auth_required 必须保持 false")

        # Authentication is intentionally not part of P0. Refuse an accidental LAN/public bind.
        if not _is_loopback_host(host):
            raise SettingsError("P0 尚未实现管理员认证，只允许绑定 127.0.0.1、::1 或 localhost")

        room_manager_enabled = _bool_value(
            poll.get("enabled", False),
            "poll.enabled",
        )
        poll_jitter_seconds = _int_value(
            poll.get("jitter_seconds", 0),
            "poll.jitter_seconds",
            minimum=0,
            maximum=300,
        )
        offline_confirmations = _int_value(
            poll.get("offline_confirmations", 3),
            "poll.offline_confirmations",
            minimum=1,
            maximum=20,
        )
        max_parallel_checks = _int_value(
            poll.get("max_parallel_checks", 10),
            "poll.max_parallel_checks",
            minimum=1,
            maximum=100,
        )

        contract_path = Path(
            env.get(
                "DOUYIN_RECORDER_PROTOCOL_CONTRACT",
                root / "app" / "douyin" / "contracts" / "provisional_v1.json",
            )
        )
        contract_path = _absolute_runtime_path(contract_path, root=root)

        return cls(
            paths=resolved_paths,
            host=host,
            port=port,
            auth_required=auth_required,
            ffmpeg_path=env.get("DOUYIN_RECORDER_FFMPEG", "ffmpeg").strip() or "ffmpeg",
            ffprobe_path=env.get("DOUYIN_RECORDER_FFPROBE", "ffprobe").strip() or "ffprobe",
            config_path=config_path,
            protocol_contract_path=contract_path,
            room_manager_enabled=room_manager_enabled,
            poll_jitter_seconds=poll_jitter_seconds,
            offline_confirmations=offline_confirmations,
            max_parallel_checks=max_parallel_checks,
        )
