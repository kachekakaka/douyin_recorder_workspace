from __future__ import annotations

from pathlib import Path


def test_completed_release_tracker_may_be_closed() -> None:
    workflow = Path(".github/workflows/release-promotion.yml").read_text(encoding="utf-8")

    confirm_step = workflow.split(
        "- name: Confirm exact current main and open protocol issue", maxsplit=1
    )[1].split("- name: Create or validate immutable annotated tag", maxsplit=1)[0]
    assert 'issues/1" --jq \'.state\'' in confirm_step
    assert "issues/18" not in confirm_step

    tag_step = workflow.split(
        "- name: Create or validate immutable annotated tag", maxsplit=1
    )[1].split("- name: Dispatch exact-tag Release workflow", maxsplit=1)[0]
    assert 'release_issue_state="$(gh api' in tag_step
    assert tag_step.count('test "$release_issue_state" = "open"') == 2
    assert 'state="published_immutable"' in tag_step
    assert 'echo "release_issue_state=$release_issue_state"' in tag_step
