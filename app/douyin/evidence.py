from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from app.douyin import TARGET_METHOD
from app.douyin.envelope import EnvelopeDecodeError, inspect_frame
from app.douyin.recipient import (
    DecodedRecipientEvent,
    RecipientContract,
    RecipientDecodeError,
    decode_recipient_payload,
)

_ALLOWED_OUTPUT_ROOTS = (
    Path("userdata") / "protocol-probes",
    Path("captures"),
    Path("private-fixtures"),
)
_ALLOWED_WSS_SUFFIX = "douyin.com"
_MAX_QUERY_KEYS = 128
_MAX_QUERY_KEY_LENGTH = 80
_QUERY_KEY_RE = re.compile(rf"^[A-Za-z0-9_.~-]{{1,{_MAX_QUERY_KEY_LENGTH}}}$")


class EvidenceError(ValueError):
    """Raised when private evidence or approval data violates a safety boundary."""


@dataclass(frozen=True, slots=True)
class EvidenceLimits:
    max_frames: int = 500
    max_frame_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_duration_seconds: float = 900.0

    def validate(self) -> None:
        if not 1 <= self.max_frames <= 100_000:
            raise EvidenceError("max_frames 必须在 1–100000 之间")
        if not 1 <= self.max_frame_bytes <= 64 * 1024 * 1024:
            raise EvidenceError("max_frame_bytes 必须在 1–64MiB 之间")
        if not self.max_frame_bytes <= self.max_total_bytes <= 512 * 1024 * 1024:
            raise EvidenceError("max_total_bytes 必须不小于单帧上限且不超过 512MiB")
        if not 1 <= self.max_duration_seconds <= 3600:
            raise EvidenceError("max_duration_seconds 必须在 1–3600 秒之间")


@dataclass(frozen=True, slots=True)
class SanitizedWebSocket:
    host: str
    path_sha256: str
    query_keys: tuple[str, ...]
    url_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "path_sha256": self.path_sha256,
            "query_keys": list(self.query_keys),
            "url_sha256": self.url_sha256,
        }


@dataclass(frozen=True, slots=True)
class EvidenceFile:
    sequence: int
    kind: str
    relative_path: str
    sha256: str
    size_bytes: int
    received_at_ms: int
    received_monotonic_ns: int
    methods: tuple[str, ...] = ()
    envelope_msg_id_sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["methods"] = list(self.methods)
        return result


@dataclass(slots=True)
class EvidenceSummary:
    method_counts: Counter[str] = field(default_factory=Counter)
    target_decode_success_count: int = 0
    target_decode_failure_count: int = 0
    envelope_parse_error_count: int = 0
    recipient_hashes: set[str] = field(default_factory=set)
    empty_recipient_count: int = 0
    open_id_present_count: int = 0
    change_reasons: Counter[str] = field(default_factory=Counter)
    unknown_field_numbers: set[int] = field(default_factory=set)
    delays_ms: list[int] = field(default_factory=list)

    def add_event(self, event: DecodedRecipientEvent) -> None:
        self.target_decode_success_count += 1
        if event.recipient_key:
            self.recipient_hashes.add(hash_recipient_key(event.recipient_key))
        else:
            self.empty_recipient_count += 1
        if event.recipient_user_open_id:
            self.open_id_present_count += 1
        if event.change_reason_enum is not None:
            self.change_reasons[str(event.change_reason_enum)] += 1
        if event.delay_ms is not None:
            self.delays_ms.append(event.delay_ms)
        for item in event.unknown_fields:
            number = item.get("field")
            if isinstance(number, int):
                self.unknown_field_numbers.add(number)

    def to_public_dict(self) -> dict[str, object]:
        delays = sorted(self.delays_ms)
        p95_index = min(len(delays) - 1, max(0, (95 * len(delays) + 99) // 100 - 1))
        return {
            "target_decode_success_count": self.target_decode_success_count,
            "target_decode_failure_count": self.target_decode_failure_count,
            "unique_recipient_hash_count": len(self.recipient_hashes),
            "recipient_hashes": sorted(self.recipient_hashes),
            "empty_recipient_count": self.empty_recipient_count,
            "open_id_present_count": self.open_id_present_count,
            "change_reason_distribution": dict(sorted(self.change_reasons.items())),
            "unknown_target_field_numbers": sorted(self.unknown_field_numbers),
            "server_delay_ms": {
                "count": len(delays),
                "min": delays[0] if delays else None,
                "p95": delays[p95_index] if delays else None,
                "max": delays[-1] if delays else None,
            },
        }


def hash_recipient_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _absolute_without_resolving(path: Path, *, repository_root: Path) -> Path:
    expanded = path.expanduser()
    candidate = expanded if expanded.is_absolute() else repository_root / expanded
    return Path(os.path.abspath(candidate))


def _reject_symlink_components(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise EvidenceError(f"路径包含符号链接: {current}")
        if current == current.parent:
            return
        current = current.parent


def _validate_private_location(
    path: Path,
    *,
    repository_root: Path,
    allow_private_output: bool,
) -> None:
    allowed_in_repo = any(
        _inside(path, repository_root / relative) for relative in _ALLOWED_OUTPUT_ROOTS
    )
    if _inside(path, repository_root) and not allowed_in_repo:
        raise EvidenceError(
            "仓库内私人证据只允许写入 userdata/protocol-probes、captures "
            "或 private-fixtures"
        )
    if not _inside(path, repository_root) and not allow_private_output:
        raise EvidenceError("仓库外输出必须显式使用 --allow-private-output")


def _validate_private_parent(parent: Path) -> None:
    if parent.is_symlink():
        raise EvidenceError("输出目录父级不得是符号链接")
    if not parent.exists() or not parent.is_dir():
        raise EvidenceError("输出目录父级必须是已存在的普通目录")
    _reject_symlink_components(parent)
    if os.name != "nt":
        mode = stat.S_IMODE(parent.stat().st_mode)
        if mode & stat.S_IWOTH:
            raise EvidenceError("输出目录父级为世界可写目录，拒绝保存私人证据")


def validate_private_output(
    output_dir: Path,
    *,
    repository_root: Path,
    allow_private_output: bool = False,
) -> Path:
    repository_root = repository_root.resolve(strict=True)
    output = _absolute_without_resolving(output_dir, repository_root=repository_root)
    if output.exists() or output.is_symlink():
        raise EvidenceError("输出目录已存在或为符号链接，拒绝覆盖")
    _validate_private_parent(output.parent)
    _validate_private_location(
        output,
        repository_root=repository_root,
        allow_private_output=allow_private_output,
    )
    return output


def validate_private_file_output(
    output_path: Path,
    *,
    repository_root: Path,
    allow_private_output: bool = False,
) -> Path:
    repository_root = repository_root.resolve(strict=True)
    output = _absolute_without_resolving(output_path, repository_root=repository_root)
    if output.exists() or output.is_symlink():
        raise EvidenceError("fixture 输出已存在或为符号链接，拒绝覆盖")
    _validate_private_parent(output.parent)
    _validate_private_location(
        output,
        repository_root=repository_root,
        allow_private_output=allow_private_output,
    )
    return output


def sanitize_websocket_url(value: str) -> SanitizedWebSocket:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (ValueError, UnicodeError) as exc:
        raise EvidenceError("WebSocket URL 无效") from exc
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme != "wss" or not host:
        raise EvidenceError("只允许 wss:// WebSocket")
    if parsed.username is not None or parsed.password is not None:
        raise EvidenceError("WebSocket URL 不得包含凭据")
    if port not in (None, 443):
        raise EvidenceError("WebSocket URL 只允许默认 TLS 端口")
    if parsed.fragment:
        raise EvidenceError("WebSocket URL 不得包含 fragment")
    if not (host == _ALLOWED_WSS_SUFFIX or host.endswith(f".{_ALLOWED_WSS_SUFFIX}")):
        raise EvidenceError("WebSocket 主机不在抖音 allowlist")
    keys: list[str] = []
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        cleaned = key if _QUERY_KEY_RE.fullmatch(key) else "<invalid>"
        if cleaned not in keys:
            keys.append(cleaned)
        if len(keys) >= _MAX_QUERY_KEYS:
            break
    path = parsed.path or "/"
    return SanitizedWebSocket(
        host=host,
        path_sha256=_sha256_bytes(path.encode("utf-8")),
        query_keys=tuple(sorted(keys)),
        url_sha256=_sha256_bytes(value.encode("utf-8")),
    )


def _set_private_permissions(path: Path, *, directory: bool) -> None:
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        path.chmod(0o700 if directory else 0o600)


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    _set_private_permissions(path, directory=False)


class EvidenceBundle:
    def __init__(
        self,
        *,
        output_dir: Path,
        repository_root: Path,
        room_id: str,
        contract: RecipientContract,
        limits: EvidenceLimits | None = None,
        allow_private_output: bool = False,
    ) -> None:
        allowed_room_chars = (
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
        )
        if not room_id or any(char not in allowed_room_chars for char in room_id):
            raise EvidenceError("room_id 格式无效")
        self.limits = limits or EvidenceLimits()
        self.limits.validate()
        self.output_dir = validate_private_output(
            output_dir,
            repository_root=repository_root,
            allow_private_output=allow_private_output,
        )
        self.room_id = room_id
        self.contract = contract
        self.frames_dir = self.output_dir / "frames"
        self.targets_dir = self.output_dir / "target-payloads"
        self.frame_records: list[EvidenceFile] = []
        self.target_records: list[EvidenceFile] = []
        self.websockets: dict[str, SanitizedWebSocket] = {}
        self.summary = EvidenceSummary()
        self.total_bytes = 0
        self._prepared = False

    def prepare(self) -> None:
        if self._prepared:
            raise EvidenceError("evidence bundle 已初始化")
        self.output_dir.mkdir(parents=False, exist_ok=False, mode=0o700)
        self.frames_dir.mkdir(mode=0o700)
        self.targets_dir.mkdir(mode=0o700)
        _set_private_permissions(self.output_dir, directory=True)
        _set_private_permissions(self.frames_dir, directory=True)
        _set_private_permissions(self.targets_dir, directory=True)
        self._prepared = True

    def register_websocket(self, request_id: str, url: str) -> SanitizedWebSocket:
        if not request_id:
            raise EvidenceError("WebSocket request_id 不能为空")
        sanitized = sanitize_websocket_url(url)
        self.websockets[request_id] = sanitized
        return sanitized

    def _reserve(self, size: int) -> None:
        if not self._prepared:
            raise EvidenceError("evidence bundle 尚未初始化")
        if len(self.frame_records) >= self.limits.max_frames:
            raise EvidenceError("捕获 frame 数超过上限")
        if size > self.limits.max_frame_bytes:
            raise EvidenceError("单帧大小超过上限")
        if self.total_bytes + size > self.limits.max_total_bytes:
            raise EvidenceError("捕获总字节超过上限")
        self.total_bytes += size

    def _reserve_payload(self, size: int) -> None:
        if size > self.limits.max_frame_bytes:
            raise EvidenceError("target payload 大小超过单帧上限")
        if self.total_bytes + size > self.limits.max_total_bytes:
            raise EvidenceError("捕获总字节超过上限")
        self.total_bytes += size

    def capture_binary_frame(
        self,
        raw: bytes,
        *,
        received_at_ms: int,
        received_monotonic_ns: int,
    ) -> EvidenceFile:
        self._reserve(len(raw))
        sequence = len(self.frame_records) + 1
        frame_name = f"{sequence:06d}.bin"
        frame_path = self.frames_dir / frame_name
        _write_private(frame_path, raw)
        methods: list[str] = []
        try:
            inspected = inspect_frame(raw)
        except EnvelopeDecodeError:
            self.summary.envelope_parse_error_count += 1
        else:
            for index, message in enumerate(inspected.response.messages, start=1):
                method = message.method or "<empty>"
                methods.append(method)
                self.summary.method_counts[method] += 1
                if method != TARGET_METHOD:
                    continue
                payload_name = (
                    f"{sequence:06d}-{index:03d}-"
                    f"{_sha256_bytes(message.payload)[:16]}.bin"
                )
                payload_path = self.targets_dir / payload_name
                self._reserve_payload(len(message.payload))
                _write_private(payload_path, message.payload)
                envelope_hash = (
                    _sha256_bytes(message.msg_id.encode("utf-8")) if message.msg_id else None
                )
                target_record = EvidenceFile(
                    sequence=len(self.target_records) + 1,
                    kind="target-payload",
                    relative_path=payload_path.relative_to(self.output_dir).as_posix(),
                    sha256=_sha256_bytes(message.payload),
                    size_bytes=len(message.payload),
                    received_at_ms=received_at_ms,
                    received_monotonic_ns=received_monotonic_ns,
                    methods=(TARGET_METHOD,),
                    envelope_msg_id_sha256=envelope_hash,
                )
                self.target_records.append(target_record)
                try:
                    event = decode_recipient_payload(
                        message.payload,
                        contract=self.contract,
                        received_at_ms=received_at_ms,
                        received_monotonic_ns=received_monotonic_ns,
                        runtime_instance_id="private-evidence",
                        envelope_msg_id=message.msg_id,
                    )
                except RecipientDecodeError:
                    self.summary.target_decode_failure_count += 1
                else:
                    self.summary.add_event(event)
        record = EvidenceFile(
            sequence=sequence,
            kind="frame",
            relative_path=frame_path.relative_to(self.output_dir).as_posix(),
            sha256=_sha256_bytes(raw),
            size_bytes=len(raw),
            received_at_ms=received_at_ms,
            received_monotonic_ns=received_monotonic_ns,
            methods=tuple(methods),
        )
        self.frame_records.append(record)
        return record

    def finalize(
        self,
        *,
        started_at: str,
        ended_at: str,
        page_url_sha256: str,
        status: str,
        errors: list[str] | None = None,
        public_extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        manifest: dict[str, object] = {
            "schema_version": 1,
            "room_id": self.room_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "contract_sha256": self.contract.sha256,
            "contract_live_verified": self.contract.live_verified,
            "page_url_sha256": page_url_sha256,
            "limits": asdict(self.limits),
            "total_bytes": self.total_bytes,
            "frames": [item.to_dict() for item in self.frame_records],
            "target_payloads": [item.to_dict() for item in self.target_records],
        }
        manifest_bytes = _canonical_json_bytes(manifest)
        manifest_path = self.output_dir / "manifest.json"
        _write_private(manifest_path, manifest_bytes)
        manifest_sha = _sha256_bytes(manifest_bytes)
        websocket_rows = [
            item.to_dict()
            for item in sorted(
                set(self.websockets.values()),
                key=lambda item: (item.host, item.path_sha256, item.url_sha256),
            )
        ]
        public_report: dict[str, object] = {
            "schema_version": 1,
            "room_id": self.room_id,
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "manifest_sha256": manifest_sha,
            "contract_sha256": self.contract.sha256,
            "contract_live_verified": self.contract.live_verified,
            "websocket_count": len(websocket_rows),
            "websockets": websocket_rows,
            "frame_count": len(self.frame_records),
            "target_payload_count": len(self.target_records),
            "total_bytes": self.total_bytes,
            "method_counts": dict(sorted(self.summary.method_counts.items())),
            "envelope_parse_error_count": self.summary.envelope_parse_error_count,
            "field_report": self.summary.to_public_dict(),
            "errors": list(errors or [])[:20],
            "notes": [
                "私人 manifest/raw frame/target payload 仅保存在 Git 忽略的本机目录。",
                "public report 不包含 Cookie、完整 WSS、query value、raw payload "
                "或 recipient 明文。",
                "即使观察到目标 method，live_verified 仍须由独立人工审查 PR 更新。",
            ],
        }
        if public_extra:
            forbidden = set(public_report).intersection(public_extra)
            if forbidden:
                raise EvidenceError(f"public report 扩展字段冲突: {sorted(forbidden)}")
            public_report.update(public_extra)
        public_bytes = _canonical_json_bytes(public_report)
        public_path = self.output_dir / "public-report.json"
        _write_private(public_path, public_bytes)
        approval = {
            "schema_version": 1,
            "human_reviewed": False,
            "manifest_sha256": manifest_sha,
            "contract_sha256": self.contract.sha256,
            "approved_payloads": [],
            "recipient_aliases": {},
            "notes": [
                "人工审查后才可把 human_reviewed 改为 true，并显式列出 payload 相对路径。",
                "recipient_aliases 的 key 必须是 recipient_key SHA-256，value 为去标识别名。",
            ],
        }
        _write_private(self.output_dir / "approval-template.json", _canonical_json_bytes(approval))
        return public_report


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvidenceError(f"{label} 必须是普通文件")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{label} JSON 无效") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} 根节点必须是对象")
    return value


def export_approved_fixture(
    *,
    evidence_dir: Path,
    approval_path: Path,
    contract_path: Path,
    output_path: Path,
    fixture_name: str,
    repository_root: Path | None = None,
    allow_private_output: bool = False,
) -> dict[str, object]:
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise EvidenceError("evidence_dir 必须是普通目录")
    _reject_symlink_components(evidence_dir)
    evidence_dir = evidence_dir.resolve(strict=True)
    manifest_path = evidence_dir / "manifest.json"
    manifest = _load_json_object(manifest_path, "manifest")
    manifest_bytes = manifest_path.read_bytes()
    approval = _load_json_object(approval_path, "approval")
    contract = RecipientContract.load(contract_path)
    if approval.get("human_reviewed") is not True:
        raise EvidenceError("approval.human_reviewed 必须显式为 true")
    if approval.get("manifest_sha256") != _sha256_bytes(manifest_bytes):
        raise EvidenceError("approval manifest SHA-256 不匹配")
    if approval.get("contract_sha256") != contract.sha256:
        raise EvidenceError("approval contract SHA-256 不匹配")
    if manifest.get("contract_sha256") != contract.sha256:
        raise EvidenceError("evidence manifest contract SHA-256 不匹配")
    approved = approval.get("approved_payloads")
    aliases = approval.get("recipient_aliases")
    if not isinstance(approved, list) or not all(isinstance(item, str) for item in approved):
        raise EvidenceError("approved_payloads 必须是字符串数组")
    if not approved:
        raise EvidenceError("至少需要审批一个 target payload")
    if len(approved) != len(set(approved)):
        raise EvidenceError("approved_payloads 不得重复")
    alias_pattern = re.compile(r"^recipient-[0-9]{3,6}$")
    if not isinstance(aliases, dict) or not all(
        isinstance(key, str)
        and len(key) == 64
        and isinstance(value, str)
        and alias_pattern.fullmatch(value)
        for key, value in aliases.items()
    ):
        raise EvidenceError(
            "recipient_aliases 必须是 64 位 hash 到 recipient-001 形式别名的对象"
        )
    records = manifest.get("target_payloads")
    if not isinstance(records, list):
        raise EvidenceError("manifest.target_payloads 无效")
    by_path = {
        str(item.get("relative_path")): item
        for item in records
        if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
    }
    events: list[dict[str, object]] = []
    first_received: int | None = None
    for relative in approved:
        record = by_path.get(relative)
        if record is None:
            raise EvidenceError(f"审批的 payload 不在 manifest 中: {relative}")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise EvidenceError("审批 payload 路径越界或类型不安全")
        candidate = evidence_dir / relative_path
        if candidate.is_symlink() or not candidate.is_file():
            raise EvidenceError("审批 payload 路径越界或类型不安全")
        _reject_symlink_components(candidate.parent)
        path = candidate.resolve(strict=True)
        if not _inside(path, evidence_dir):
            raise EvidenceError("审批 payload 路径越界或类型不安全")
        payload = path.read_bytes()
        expected_sha = record.get("sha256")
        if not isinstance(expected_sha, str) or _sha256_bytes(payload) != expected_sha:
            raise EvidenceError(f"payload SHA-256 不匹配: {relative}")
        received_at_ms = record.get("received_at_ms")
        received_monotonic_ns = record.get("received_monotonic_ns")
        if not isinstance(received_at_ms, int) or not isinstance(received_monotonic_ns, int):
            raise EvidenceError("manifest payload 时间字段无效")
        event = decode_recipient_payload(
            payload,
            contract=contract,
            received_at_ms=received_at_ms,
            received_monotonic_ns=received_monotonic_ns,
            runtime_instance_id="approved-evidence",
            envelope_msg_id=None,
        )
        alias_key = None
        alias = None
        if event.recipient_key:
            alias_key = hash_recipient_key(event.recipient_key)
            alias = aliases.get(alias_key)
            if not isinstance(alias, str) or not alias:
                raise EvidenceError(f"recipient hash 未提供人工别名: {alias_key}")
        first_received = (
            received_at_ms
            if first_received is None
            else min(first_received, received_at_ms)
        )
        prefix = event.recipient_key.split(":", 1)[0] if event.recipient_key else None
        events.append(
            {
                "received_offset_ms": 0,
                "method": TARGET_METHOD,
                "payload_sha256": event.payload_hash,
                "msg_id_sha256": (
                    _sha256_bytes(event.msg_id.encode("utf-8")) if event.msg_id else None
                ),
                "recipient_alias": f"{prefix}:{alias}" if prefix and alias else None,
                "recipient_hash": alias_key,
                "change_reason_enum": event.change_reason_enum,
                "server_time_unit": event.server_time_unit,
                "unknown_field_numbers": sorted(
                    {
                        item["field"]
                        for item in event.unknown_fields
                        if isinstance(item.get("field"), int)
                    }
                ),
            }
        )
    assert first_received is not None
    for event, relative in zip(events, approved, strict=True):
        received = by_path[relative]["received_at_ms"]
        event["received_offset_ms"] = int(received) - first_received
    fixture: dict[str, object] = {
        "fixture_version": 1,
        "name": fixture_name,
        "synthetic": False,
        "live_verified": False,
        "human_reviewed": True,
        "contains_real_user_data": False,
        "target_method": TARGET_METHOD,
        "source_manifest_sha256": approval["manifest_sha256"],
        "contract_sha256": contract.sha256,
        "events": events,
        "notes": [
            "此 candidate fixture 已去标识，但 live_verified 仍为 false。",
            "修改 provisional contract 或确认真实语义必须由后续独立审查 PR 完成。",
        ],
    }
    if repository_root is not None:
        output_path = validate_private_file_output(
            output_path,
            repository_root=repository_root,
            allow_private_output=allow_private_output,
        )
    elif output_path.exists() or output_path.is_symlink():
        raise EvidenceError("fixture 输出已存在或为符号链接，拒绝覆盖")
    _write_private(output_path, _canonical_json_bytes(fixture))
    return fixture


__all__ = [
    "EvidenceBundle",
    "EvidenceError",
    "EvidenceLimits",
    "SanitizedWebSocket",
    "export_approved_fixture",
    "hash_recipient_key",
    "sanitize_websocket_url",
    "validate_private_file_output",
    "validate_private_output",
]
