from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

from starlette.requests import Request

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


@dataclass(frozen=True, slots=True)
class RequestBoundaryViolation:
    status_code: int
    code: str
    message: str


def _host_and_port(value: str) -> tuple[str, int | None] | None:
    raw = value.strip()
    if not raw or _has_control(raw):
        return None
    try:
        parsed = urlsplit(f"//{raw}", allow_fragments=False)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed.hostname.casefold().rstrip("."), port


def _is_loopback_host(host: str) -> bool:
    normalized = host.casefold().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _origin_parts(value: str) -> SplitResult | None:
    if _has_control(value):
        return None
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in _DEFAULT_PORTS
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed


def _referer_parts(value: str) -> SplitResult | None:
    if _has_control(value):
        return None
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in _DEFAULT_PORTS
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return parsed


def _same_origin(
    parsed: SplitResult,
    *,
    request_scheme: str,
    request_host: str,
    request_port: int | None,
) -> bool:
    origin_host = (parsed.hostname or "").casefold().rstrip(".")
    if not _is_loopback_host(origin_host) or origin_host != request_host:
        return False
    origin_port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    effective_request_port = request_port or _DEFAULT_PORTS.get(request_scheme)
    return parsed.scheme == request_scheme and origin_port == effective_request_port


def validate_request_boundary(request: Request) -> RequestBoundaryViolation | None:
    """Protect the unauthenticated loopback-only P1A API from DNS rebinding and CSRF."""

    host_values = request.headers.getlist("host")
    host_parts = _host_and_port(host_values[0]) if len(host_values) == 1 else None
    if host_parts is None or not _is_loopback_host(host_parts[0]):
        return RequestBoundaryViolation(
            status_code=400,
            code="invalid_host",
            message="P1A 只接受 localhost 或回环 IP 的 Host 请求头",
        )

    if request.method.upper() not in _STATE_CHANGING_METHODS:
        return None

    request_host, request_port = host_parts
    request_scheme = request.url.scheme.casefold()
    origin_values = request.headers.getlist("origin")
    if len(origin_values) > 1:
        return RequestBoundaryViolation(
            status_code=403,
            code="cross_origin_write",
            message="写操作包含多个 Origin 请求头，已拒绝",
        )
    origin = origin_values[0] if origin_values else ""
    if origin:
        parsed = _origin_parts(origin)
        if parsed is None or not _same_origin(
            parsed,
            request_scheme=request_scheme,
            request_host=request_host,
            request_port=request_port,
        ):
            return RequestBoundaryViolation(
                status_code=403,
                code="cross_origin_write",
                message="写操作只允许同源回环页面或无浏览器 Origin 的本机客户端",
            )
        return None

    referer_values = request.headers.getlist("referer")
    if len(referer_values) > 1:
        return RequestBoundaryViolation(
            status_code=403,
            code="cross_origin_write",
            message="写操作包含多个 Referer 请求头，已拒绝",
        )
    referer = referer_values[0] if referer_values else ""
    if referer:
        parsed = _referer_parts(referer)
        if parsed is None or not _same_origin(
            parsed,
            request_scheme=request_scheme,
            request_host=request_host,
            request_port=request_port,
        ):
            return RequestBoundaryViolation(
                status_code=403,
                code="cross_origin_write",
                message="写操作的 Referer 必须与当前回环地址同源",
            )
        return None

    if request.headers.get("sec-fetch-site", "").strip().casefold() in {
        "cross-site",
        "same-site",
    }:
        return RequestBoundaryViolation(
            status_code=403,
            code="cross_origin_write",
            message="浏览器跨站写操作已拒绝",
        )
    return None
