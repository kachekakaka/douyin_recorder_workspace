from __future__ import annotations

import json
from pathlib import Path

from app.douyin.recipient import RecipientContract
from app.douyin.replay import run_fixture
from app.paths import ROOT

CONTRACT = ROOT / "tests" / "replay" / "contracts" / "recipient.synthetic-v1.json"
FIXTURE = ROOT / "tests" / "replay" / "fixtures" / "recipient-strict-unknown.synthetic.json"


def test_strict_unknown_fixture_is_deterministic_and_not_live_claimed(tmp_path: Path) -> None:
    contract = RecipientContract.load(CONTRACT)
    first = run_fixture(FIXTURE, contract=contract)
    second = run_fixture(FIXTURE, contract=contract)

    assert first.to_dict() == second.to_dict()
    assert contract.live_verified is False
    assert first.fixture_synthetic is True
    assert first.fixture_live_verified is False
    assert first.target_messages > 0
    assert first.target_decode_failures == 0
    assert first.report["duplicate_frame_count"] >= 1
    assert first.report["transport_disconnects"] >= 1

    output = tmp_path / "report.json"
    output.write_text(
        json.dumps(first.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["reducer"]["intervals"][-1]["ended_at_ms"] is not None
