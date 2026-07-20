from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import pytest
import websockets

from app.douyin import TARGET_METHOD
from app.douyin.envelope import build_message, build_push_frame, build_response
from app.douyin.evidence import (
    EvidenceBundle,
    EvidenceError,
    EvidenceLimits,
    export_approved_fixture,
    hash_recipient_key,
    sanitize_websocket_url,
    validate_private_file_output,
    validate_private_output,
)
from app.douyin.protobuf_wire import (
    encode_bytes_field,
    encode_varint_field,
)
from app.douyin.recipient import RecipientContract
from app.paths import ROOT
from tools.douyin_interactive_evidence import (
    run_interactive_probe,
    validate_devtools_url,
)

CONTRACT = ROOT / "app" / "douyin" / "contracts" / "provisional_v1.json"
REAL_LIKE_ID = "90071992547409931"


def _target_payload(*, user_id: int = int(REAL_LIKE_ID), reason: int = 4) -> bytes:
    common = encode_varint_field(2, 123456) + encode_varint_field(4, 1_700_000_000)
    return (
        encode_bytes_field(1, common)
        + encode_varint_field(2, user_id)
        + encode_varint_field(3, reason)
    )


def _target_frame() -> bytes:
    message = build_message(TARGET_METHOD, _target_payload(), msg_id=123456)
    return build_push_frame(build_response([message]), log_id=11, gzip_payload=True)


def test_private_output_and_websocket_boundaries(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    allowed_parent = repo / "userdata" / "protocol-probes"
    allowed_parent.mkdir(parents=True)
    allowed = validate_private_output(
        allowed_parent / "probe-a",
        repository_root=repo,
    )
    assert allowed == (allowed_parent / "probe-a").resolve()

    (repo / "docs").mkdir()
    with pytest.raises(EvidenceError, match="仓库内私人证据"):
        validate_private_output(repo / "docs" / "probe", repository_root=repo)

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(EvidenceError, match="allow-private-output"):
        validate_private_output(outside / "probe", repository_root=repo)
    assert validate_private_output(
        outside / "probe",
        repository_root=repo,
        allow_private_output=True,
    ) == (outside / "probe").resolve()

    private_fixture_parent = repo / "private-fixtures"
    private_fixture_parent.mkdir()
    assert validate_private_file_output(
        private_fixture_parent / "candidate.json",
        repository_root=repo,
    ) == (private_fixture_parent / "candidate.json").resolve()
    with pytest.raises(EvidenceError, match="仓库内私人证据"):
        validate_private_file_output(
            repo / "docs" / "candidate.json",
            repository_root=repo,
        )

    existing = allowed_parent / "existing"
    existing.mkdir()
    with pytest.raises(EvidenceError, match="已存在"):
        validate_private_output(existing, repository_root=repo)

    symlink = allowed_parent / "symlink"
    try:
        symlink.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(EvidenceError):
            validate_private_output(symlink, repository_root=repo)

    nested_real = allowed_parent / "nested-real"
    nested_real.mkdir()
    nested_link = allowed_parent / "nested-link"
    try:
        nested_link.symlink_to(nested_real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(EvidenceError, match="符号链接"):
            validate_private_output(
                nested_link / "probe",
                repository_root=repo,
            )

    if os.name != "nt":
        world = tmp_path / "world"
        world.mkdir(mode=0o777)
        world.chmod(0o777)
        with pytest.raises(EvidenceError, match="世界可写"):
            validate_private_output(
                world / "probe",
                repository_root=repo,
                allow_private_output=True,
            )

    safe = sanitize_websocket_url(
        "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/?"
        "room_id=PRIVATE&signature=SECRET&room_id=SECOND"
    )
    rendered = json.dumps(safe.to_dict(), sort_keys=True)
    assert safe.host == "webcast5-ws-web-lf.douyin.com"
    assert safe.query_keys == ("room_id", "signature")
    invalid_key = sanitize_websocket_url(
        "wss://webcast5-ws-web-lf.douyin.com/path?%0A=secret"
    )
    assert invalid_key.query_keys == ("<invalid>",)
    assert "PRIVATE" not in rendered
    assert "SECRET" not in rendered
    assert "/webcast/" not in rendered
    for unsafe in (
        "ws://webcast5-ws-web-lf.douyin.com/path",
        "wss://user:pass@webcast5-ws-web-lf.douyin.com/path",
        "wss://127.0.0.1/path",
        "wss://example.com/path",
        "wss://webcast5-ws-web-lf.douyin.com:444/path",
    ):
        with pytest.raises(EvidenceError):
            sanitize_websocket_url(unsafe)


def test_evidence_bundle_public_report_and_human_approval(tmp_path: Path) -> None:
    contract = RecipientContract.load(CONTRACT)
    output = tmp_path / "private" / "probe"
    output.parent.mkdir()
    bundle = EvidenceBundle(
        output_dir=output,
        repository_root=ROOT,
        room_id="79907888978",
        contract=contract,
        limits=EvidenceLimits(
            max_frames=2,
            max_frame_bytes=1024 * 1024,
            max_total_bytes=4 * 1024 * 1024,
        ),
        allow_private_output=True,
    )
    bundle.prepare()
    bundle.register_websocket(
        "request-1",
        "wss://webcast5-ws-web-lf.douyin.com/PRIVATE-PATH?"
        "signature=SECRET",
    )
    bundle.capture_binary_frame(
        _target_frame(),
        received_at_ms=1_700_000_000_123,
        received_monotonic_ns=123_000,
    )
    report = bundle.finalize(
        started_at="2026-07-20T00:00:00+00:00",
        ended_at="2026-07-20T00:00:01+00:00",
        page_url_sha256="a" * 64,
        status="target-observed",
    )
    rendered = json.dumps(report, sort_keys=True)
    assert report["target_payload_count"] == 1
    assert report["field_report"]["unique_recipient_hash_count"] == 1
    assert REAL_LIKE_ID not in rendered
    assert "SECRET" not in rendered
    assert "PRIVATE-PATH" not in rendered
    assert "target-payloads" not in rendered
    assert (output / "frames" / "000001.bin").is_file()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    target = manifest["target_payloads"][0]
    recipient_hash = hash_recipient_key(f"uid:{REAL_LIKE_ID}")
    approval = json.loads((output / "approval-template.json").read_text(encoding="utf-8"))
    approval.update(
        human_reviewed=True,
        approved_payloads=[target["relative_path"]],
        recipient_aliases={recipient_hash: "recipient-001"},
    )
    approval_path = output / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    fixture_parent = tmp_path / "fixture-output"
    fixture_parent.mkdir()
    fixture_path = fixture_parent / "approved.fixture.json"
    fixture = export_approved_fixture(
        evidence_dir=output,
        approval_path=approval_path,
        contract_path=CONTRACT,
        output_path=fixture_path,
        fixture_name="human-reviewed-candidate",
        repository_root=ROOT,
        allow_private_output=True,
    )
    fixture_rendered = json.dumps(fixture, sort_keys=True)
    assert fixture["human_reviewed"] is True
    assert fixture["live_verified"] is False
    assert fixture["contains_real_user_data"] is False
    assert fixture["events"][0]["recipient_alias"] == "uid:recipient-001"
    assert REAL_LIKE_ID not in fixture_rendered
    assert "SECRET" not in fixture_rendered
    assert base64.b64encode(_target_payload()).decode() not in fixture_rendered

    bad = dict(approval)
    bad["manifest_sha256"] = "0" * 64
    bad_path = output / "bad-approval.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(EvidenceError, match="manifest SHA"):
        export_approved_fixture(
            evidence_dir=output,
            approval_path=bad_path,
            contract_path=CONTRACT,
            output_path=fixture_parent / "bad.fixture.json",
            fixture_name="bad",
            repository_root=ROOT,
            allow_private_output=True,
        )

    unsafe_alias = dict(approval)
    unsafe_alias["recipient_aliases"] = {recipient_hash: REAL_LIKE_ID}
    unsafe_alias_path = output / "unsafe-alias.json"
    unsafe_alias_path.write_text(json.dumps(unsafe_alias), encoding="utf-8")
    with pytest.raises(EvidenceError, match="recipient-001"):
        export_approved_fixture(
            evidence_dir=output,
            approval_path=unsafe_alias_path,
            contract_path=CONTRACT,
            output_path=fixture_parent / "unsafe.fixture.json",
            fixture_name="unsafe",
            repository_root=ROOT,
            allow_private_output=True,
        )


def test_evidence_bundle_enforces_count_and_size(tmp_path: Path) -> None:
    contract = RecipientContract.load(CONTRACT)
    output = tmp_path / "private" / "probe"
    output.parent.mkdir()
    bundle = EvidenceBundle(
        output_dir=output,
        repository_root=ROOT,
        room_id="79907888978",
        contract=contract,
        limits=EvidenceLimits(max_frames=1, max_frame_bytes=100, max_total_bytes=200),
        allow_private_output=True,
    )
    bundle.prepare()
    with pytest.raises(EvidenceError, match="单帧"):
        bundle.capture_binary_frame(
            b"x" * 101,
            received_at_ms=1,
            received_monotonic_ns=1,
        )


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:9222",
        "http://localhost:9222",
        "http://127.0.0.1",
        "http://user:pass@127.0.0.1:9222",
        "http://127.0.0.1:9222/json",
    ],
)
def test_devtools_url_rejects_non_loopback_or_ambiguous_values(value: str) -> None:
    with pytest.raises(EvidenceError):
        validate_devtools_url(value)


def test_interactive_probe_with_synthetic_cdp_server(tmp_path: Path) -> None:
    async def scenario() -> None:
        frame = _target_frame()
        port_holder: list[int] = []

        async def handler(connection) -> None:
            async for raw in connection:
                command = json.loads(raw)
                await connection.send(json.dumps({"id": command["id"], "result": {}}))
                if command["method"] == "Page.enable":
                    await connection.send(
                        json.dumps(
                            {
                                "method": "Page.frameNavigated",
                                "params": {
                                    "frame": {
                                        "id": "root",
                                        "url": "https://live.douyin.com/79907888978",
                                    }
                                },
                            }
                        )
                    )
                    await connection.send(
                        json.dumps(
                            {
                                "method": "Network.webSocketCreated",
                                "params": {
                                    "requestId": "wss-1",
                                    "url": (
                                        "wss://webcast5-ws-web-lf.douyin.com/PRIVATE?"
                                        "room_id=SECRET"
                                    ),
                                },
                            }
                        )
                    )
                    await connection.send(
                        json.dumps(
                            {
                                "method": "Network.webSocketFrameReceived",
                                "params": {
                                    "requestId": "wss-1",
                                    "response": {
                                        "opcode": 2,
                                        "payloadData": base64.b64encode(frame).decode(),
                                    },
                                },
                            }
                        )
                    )

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            port_holder.append(port)
            targets = [
                {
                    "type": "page",
                    "url": "https://live.douyin.com/79907888978",
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/test",
                }
            ]
            output = tmp_path / "evidence"
            report = await run_interactive_probe(
                room_id="79907888978",
                devtools_url=f"http://127.0.0.1:{port}",
                output_dir=output,
                duration_seconds=1,
                contract_path=CONTRACT,
                repository_root=ROOT,
                allow_private_output=True,
                limits=EvidenceLimits(
                    max_frames=4,
                    max_frame_bytes=1024 * 1024,
                    max_total_bytes=4 * 1024 * 1024,
                    max_duration_seconds=2,
                ),
                target_fetcher=lambda _url: targets,
            )
            rendered = json.dumps(report, sort_keys=True)
            assert report["status"] == "target-observed"
            assert report["frame_count"] == 1
            assert report["target_payload_count"] == 1
            assert REAL_LIKE_ID not in rendered
            assert "PRIVATE" not in rendered
            assert "SECRET" not in rendered
            assert (output / "manifest.json").is_file()
            assert port_holder == [port]

    asyncio.run(scenario())
