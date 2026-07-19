from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

MAX_HEADER_NAME_CHARS = 64
MAX_HEADER_VALUE_CHARS = 4096

_ALLOWED_INPUT_HEADERS = {
    "accept",
    "accept-language",
    "authorization",
    "cache-control",
    "cookie",
    "origin",
    "pragma",
    "referer",
    "user-agent",
}
_ALLOWED_EXTRA_INPUT_ARGS = {
    "-rw_timeout": (1, 300_000_000),
    "-timeout": (1, 300_000_000),
    "-reconnect": (0, 1),
    "-reconnect_streamed": (0, 1),
    "-reconnect_delay_max": (0, 300),
}
_ALLOWED_STREAM_HOST_SUFFIXES = (
    "douyincdn.com",
    "douyin.com",
    "bytecdn.cn",
    "byteimg.com",
    "amemv.com",
    "snssdk.com",
    "zijieapi.com",
    "bytedance.com",
)
_SECRET_HEADER_LINE_RE = re.compile(
    r"(?im)^(\s*(cookie|authorization|x-signature|x-sign|ttwid)\s*:\s*).*$",
)
_SECRET_PAIR_RE = re.compile(
    r"(?i)(cookie|authorization|ttwid|signature|sign|token|auth|credential|access_key|secret)(\s*[=:]\s*)([^\s;,\r\n]+)",
)
_URL_TOKEN_RE = re.compile(r"(?i)https?://[^\s\"']+")


class RecorderConfigurationError(ValueError):
    """Raised before a sensitive or unsafe FFmpeg plan can be spawned."""


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _validate_stream_url(url: str) -> None:
    if len(url) > 16_384 or any(ord(char) < 32 or ord(char) == 127 for char in url):
        raise RecorderConfigurationError("FFmpeg 输入 URL 包含控制字符或过长")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError as exc:
        raise RecorderConfigurationError("FFmpeg 输入 URL 格式无效") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise RecorderConfigurationError("FFmpeg 输入仅允许 HTTP/HTTPS 流媒体 URL")
    expected_port = 80 if parsed.scheme == "http" else 443
    if parsed.username is not None or parsed.password is not None or port not in (None, expected_port):
        raise RecorderConfigurationError("FFmpeg 输入 URL 不得包含凭据或异常端口")
    if _is_ip_literal(host):
        raise RecorderConfigurationError("FFmpeg 输入不允许使用 IP 字面量")
    if host == "localhost" or host.endswith(".local"):
        raise RecorderConfigurationError("FFmpeg 输入拒绝本机或 .local 地址")
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_STREAM_HOST_SUFFIXES):
        raise RecorderConfigurationError("FFmpeg 输入不是受信任抖音或字节系 CDN 域名")


def _validate_headers(headers: tuple[tuple[str, str], ...]) -> None:
    seen: set[str] = set()
    for name, value in headers:
        normalized = name.strip().casefold()
        if (
            not normalized
            or len(name.encode("utf-8", errors="ignore")) > MAX_HEADER_NAME_CHARS
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
            or normalized not in _ALLOWED_INPUT_HEADERS
            or normalized in seen
        ):
            raise RecorderConfigurationError(f"不允许或产生了重复的输入头名：{name}")
        seen.add(normalized)
        if (
            len(value.encode("utf-8", errors="ignore")) > MAX_HEADER_VALUE_CHARS
            or any(ord(char) < 32 and char != "\t" for char in value)
            or any(char in "\r\n" for char in value)
        ):
            raise RecorderConfigurationError(f"输入头值包含控制字符或过长：{name}")


def _format_headers(headers: tuple[tuple[str, str], ...]) -> str:
    if not headers:
        return ""
    _validate_headers(headers)
    return "".join(f"{name}: {value}\r\n" for name, value in headers)


def _validate_extra_input_args(args: tuple[str, ...]) -> None:
    if len(args) % 2:
        raise RecorderConfigurationError("extra_input_args 必须是 option/value 成对列表")
    for index in range(0, len(args), 2):
        option = args[index]
        raw_value = args[index + 1]
        allowed_range = _ALLOWED_EXTRA_INPUT_ARGS.get(option)
        if allowed_range is None:
            raise RecorderConfigurationError(f"不允许的 FFmpeg 输入参数：{option}")
        lower, upper = allowed_range
        if not re.fullmatch(r"[0-9]{1,10}", raw_value):
            raise RecorderConfigurationError(f"参数值必须是受控的十进制整数：{option}")
        value = int(raw_value)
        if not lower <= value <= upper:
            raise RecorderConfigurationError(f"参数超出允许范围：{option}")


def _prepare_output_dir(media_dir: Path, *, segment_csv_path: Path) -> None:
    media_dir.mkdir(parents=True, exist_ok=True)
    existing = list(media_dir.glob("*.mkv")) + list(media_dir.glob("*.writing"))
    if segment_csv_path.exists():
        existing.append(segment_csv_path)
    if existing:
        names = ", ".join(path.name for path in existing[:8])
        raise RecorderConfigurationError(f"输出目录已存在，拒绝覆盖：{names}")


def _sanitize_url_token(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return "<redacted-url>"
    if not host:
        return "<redacted-url>"
    authority = host if port is None else f"{host}:{port}"
    keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    query = ""
    if keys:
        query = "?" + "&".join(f"{key}=<redacted>" for key in keys)
    return f"{parsed.scheme}://{authority}/<redacted-path>{query}"


def sanitize_log_line(line: str) -> str:
    cleaned = _SECRET_HEADER_LINE_RE.sub(r"\1<redacted>", line)
    cleaned = _SECRET_PAIR_RE.sub(r"\1\2<redacted>", cleaned)
    cleaned = _URL_TOKEN_RE.sub(_sanitize_url_token, cleaned)
    return cleaned[:16_384]


def redact_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sanitize_log_line(item) for item in argv)
