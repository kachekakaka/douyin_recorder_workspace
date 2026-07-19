from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ARCHIVE_SHA256 = "602d83b2b8305f22ce750082e3ab1fb26369209471fcd7520c1108d4d26cf62a"
BASE64_LENGTH = 35_200
BASE64_BLOBS = (
    "5435f5b0f434fd069ed0adbf1063d554c8ed90de",
    "8c3ee6d40896a1082d8f8ac8a3f13ec0f2bc3086",
    "8c1c4ee7efc865571869685a51b4e74df057d4d2",
    "46673b4b94e2fc23343c9a095680ebb9b8eca58d",
)
HEX_BLOBS = (
    "cc8b3ceec5f5170cdae5cfc15e4aaa77205316f5",
    "f027f3a5e3e36a639fd68e8634896ff0cbeaa032",
    "457c6689876590cba3352fa98c72a9fc77d3f902",
    "51403dd2fb5655fe36abdbd833cc975863dd01b2",
    "9157cf4b8e5c2e63fd723157c41d96903cdb6437",
    "268c359e9b0eec2cc4c2e462c59af16c4442da2f",
    "5cbefbb745f068afd5da63762ba682f7fe89e858",
    "5ec524bac98f03282463e2fcab380c16adf257b9",
    "2ec336f03459cb07f134d3e772008a72aed911ac",
    "27438dfbc7e7bb9273c16bb7b773ffd97c68b8db",
    "cd2172cd7b76ce1163682040f01bbf0396b197ce",
    "763e49a68dec55ca5f84520fbd2aaf94f6c7a64d",
    "8dccc23244ceb9152f8538a342b6277f3fca48f8",
    "71a98d205b8b54120d56237e8cb3ddc687a24811",
    "6042a452602f24847d6c3b0467fa453e1c9908b4",
    "27f626b8a6c6179464960440a7129877b6141815",
    "fa91e1b7246af7a154fe4689e92ef6f20fa890cd",
    "ce1716ca35e213ca34e91d619d3d7739595923c1",
    "946b1920151d75554802eac2b55bbbac3e82dbc4",
    "a8cb6808209b112cf8db2e83e70ae105c86e16d5",
    "b1ccf853809aa2be8b185ac7f1bf168d5bbcf372",
    "4ac977bdffdb4241021b515168c71d36d3546a2c",
    "ac11d3f9052f9edb4741b5c9dbcbe8f12a401e8f",
    "9e28ce4f55ece152ea261d46e80ec931932de5f6",
    "91f97d41ddb104b65ed48790b02af5335f57418c",
    "bdfd9b62d16142c8b7e543a7bc908bd3ef195355",
    "57998319d3037859f737f66c7b953f5824cd0a02",
    "e20de8ef7c6301a4277a5910f3d9993b77c62575",
    "dec9b5ec61fa6b8b04c038bb4f60adca598fabe8",
)
FILE_BLOBS = {
    "app/douyin/live_page.py": "aef5352083b8d27857a2d8fd5b09296279b4389d",
    "app/douyin/stream_resolver.py": "2cb690e73806a7da352840c73a0e007b1f76132c",
    "app/rooms/service.py": "787ed309e1a0451b38b6c5e078921aaa23637c2b",
    "app/state.py": "28908ca5953563b24b6d4cab305839fcd526e91d",
    "tests/integration/test_room_api.py": "d364ea017411aa6a82c411808c619a25f0428634",
    "tests/unit/test_douyin_network_probe.py": "2c46f4f03bb495afa9c4ba3489ca4a00e5f50671",
    "tests/unit/test_live_page.py": "fb9dbb434e0d733fc1ad34694f3a88bc15f4ff9c",
    "tests/unit/test_stream_resolver.py": "0a090fde2210270ee2b370ba78d9e8ae8d365422",
    "tools/douyin_network_probe.py": "cff6add4eb96a81b80b04fffaf784f4d7e76ac7a",
    "tools/verify_source.py": "a8c86d41bcf3295425a36a041134592feb6c7532",
}
TEMPORARY_PATHS = {
    ".github/p1a-resolver-materialize.py",
    ".github/workflows/ci.yml",
    ".github/workflows/p1a-resolver-assemble-once.yml",
}


def command(*args: str, text: bool = False) -> bytes | str:
    return subprocess.check_output(args, text=text)


def download_blob(sha: str) -> bytes:
    repository = os.environ["GITHUB_REPOSITORY"]
    encoded = command(
        "gh",
        "api",
        f"repos/{repository}/git/blobs/{sha}",
        "--jq",
        ".content",
        text=True,
    )
    assert isinstance(encoded, str)
    return base64.b64decode("".join(encoded.split()), validate=True)


def reconstruct_archive() -> bytes:
    payload = bytearray()
    for sha in BASE64_BLOBS:
        payload.extend(download_blob(sha))
    for sha in HEX_BLOBS:
        payload.extend(bytes.fromhex(download_blob(sha).decode("ascii")))
    if len(payload) != BASE64_LENGTH:
        raise SystemExit(f"unexpected base64 payload length: {len(payload)}")
    archive = base64.b64decode(bytes(payload), validate=True)
    digest = hashlib.sha256(archive).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise SystemExit(f"archive SHA-256 mismatch: {digest}")
    return archive


def extract_archive(archive: bytes) -> None:
    root = Path.cwd().resolve()
    expected = set(FILE_BLOBS)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = bundle.getmembers()
        names = [member.name for member in members]
        if len(names) != len(expected) or set(names) != expected:
            raise SystemExit(f"unexpected payload paths: {sorted(names)}")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise SystemExit(f"unsafe payload member: {member.name}")
            target = (root / Path(*path.parts)).resolve()
            if root not in target.parents:
                raise SystemExit(f"payload escaped repository: {member.name}")
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit(f"payload member unreadable: {member.name}")
            data = source.read()
            if len(data) != member.size:
                raise SystemExit(f"payload member truncated: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def verify_files() -> None:
    for path, expected in FILE_BLOBS.items():
        actual = command("git", "hash-object", path, text=True).strip()
        assert isinstance(actual, str)
        if actual != expected:
            raise SystemExit(f"blob mismatch: {path} expected={expected} actual={actual}")


def restore_final_tree() -> None:
    ci_bytes = command("git", "show", "HEAD^:.github/workflows/ci.yml")
    assert isinstance(ci_bytes, bytes)
    Path(".github/workflows/ci.yml").write_bytes(ci_bytes)
    Path(".github/workflows/p1a-resolver-assemble-once.yml").unlink()
    Path(__file__).unlink()

    contract = json.loads(Path("app/douyin/contracts/provisional_v1.json").read_text())
    if contract.get("live_verified") is not False:
        raise SystemExit("provisional contract must remain live_verified=false")

    expected = set(FILE_BLOBS) | TEMPORARY_PATHS
    actual_text = command("git", "diff", "--name-only", text=True)
    assert isinstance(actual_text, str)
    actual = set(actual_text.splitlines())
    if actual != expected:
        raise SystemExit(f"unexpected worktree diff: {sorted(actual)}")
    subprocess.run(["git", "diff", "--check"], check=True)


def main() -> None:
    expected_head = os.environ["EXPECTED_HEAD"]
    current_head = command("git", "rev-parse", "HEAD", text=True).strip()
    assert isinstance(current_head, str)
    if current_head != expected_head:
        raise SystemExit(f"branch moved before materialization: {current_head}")
    archive = reconstruct_archive()
    extract_archive(archive)
    verify_files()
    restore_final_tree()
    print("verified P1A resolver milestone materialized")


if __name__ == "__main__":
    main()
