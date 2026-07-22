from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release_tag_gate import ReleaseTagGateError, inspect_release_tag_gate


def _release_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app" / "douyin" / "contracts").mkdir(parents=True)
    (root / "packaging").mkdir()
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "app" / "__init__.py").write_text(
        '__version__ = "0.1.0"\n', encoding="utf-8"
    )
    (root / "packaging" / "release-lock.json").write_text(
        json.dumps({"release_version": "0.1.0"}), encoding="utf-8"
    )
    (root / "app" / "douyin" / "contracts" / "provisional_v1.json").write_text(
        json.dumps(
            {
                "target_method": "WebcastGroupLiveGiftRecipientRecommendMessage",
                "live_verified": False,
            }
        ),
        encoding="utf-8",
    )
    (root / "docs" / "releases" / "v0.1.0.md").write_text(
        "# v0.1.0\n", encoding="utf-8"
    )
    return root


def test_current_repository_is_eligible_for_v010_promotion() -> None:
    gate = inspect_release_tag_gate(Path.cwd())
    assert gate.version == "0.1.0"
    assert gate.tag == "v0.1.0"
    assert gate.live_verified is False
    assert "首个 Windows x64 可恢复发布" in gate.message
    assert "Issue #1" in gate.message


def test_gate_rejects_version_mismatch(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    (root / "app" / "__init__.py").write_text(
        '__version__ = "0.1.1"\n', encoding="utf-8"
    )
    with pytest.raises(ReleaseTagGateError, match="版本不一致"):
        inspect_release_tag_gate(root)


def test_gate_rejects_live_verified_true(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    contract = root / "app" / "douyin" / "contracts" / "provisional_v1.json"
    contract.write_text(
        json.dumps(
            {
                "target_method": "WebcastGroupLiveGiftRecipientRecommendMessage",
                "live_verified": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReleaseTagGateError, match="live_verified"):
        inspect_release_tag_gate(root)


def test_gate_requires_release_notes(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    (root / "docs" / "releases" / "v0.1.0.md").unlink()
    with pytest.raises(ReleaseTagGateError, match="Release notes"):
        inspect_release_tag_gate(root)
