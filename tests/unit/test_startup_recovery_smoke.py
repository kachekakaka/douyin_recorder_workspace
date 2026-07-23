from __future__ import annotations

import json

import pytest

from tools.startup_recovery_smoke import run_startup_recovery_smoke


def test_startup_recovery_smoke_repeats_idempotently() -> None:
    report = run_startup_recovery_smoke(cycles=10)

    assert report == {
        "smoke_version": 1,
        "cycles": 10,
        "recovered_recording_sessions": 10,
        "recovered_postprocess_jobs": 10,
        "idempotent_recovery_passes": 10,
        "schema_version": 6,
        "uses_network": False,
        "live_verified": False,
        "passed": True,
    }
    rendered = json.dumps(report, sort_keys=True)
    assert "raw_payload" not in rendered
    assert "wss://" not in rendered


def test_startup_recovery_smoke_rejects_unbounded_cycles() -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        run_startup_recovery_smoke(cycles=101)
