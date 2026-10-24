"""Guards over release.yml (#38) and the release-tags ruleset."""

import copy
import json
from typing import Any

from tests.test_workflows import WORKFLOWS, gate_bypasses, jobs, load, triggers, write_permissions

RELEASE = WORKFLOWS / "release.yml"


def test_release_runs_on_v_tags_without_cancelling() -> None:
    doc = load(RELEASE)
    assert triggers(doc) == {"push": {"tags": ["v*"]}}
    assert doc["concurrency"] == {"group": "release-${{ github.ref }}", "cancel-in-progress": False}
    assert doc["permissions"] == {"contents": "read"}


def test_release_runs_the_full_gate_against_the_matching_engine() -> None:
    doc = load(RELEASE)
    assert gate_bypasses(doc) == []
    ci = jobs(doc)["ci"]
    assert ci["with"]["engine_image"] == "${{ needs.validate.outputs.engine_image }}"
    # Complement: a release never turns the live Engine smoke off.
    assert "live_smoke" not in ci["with"]
    assert set(jobs(doc)["publish"]["needs"]) == {"validate", "ci"}


def test_a_release_without_the_gate_fails_the_rule() -> None:
    doc = copy.deepcopy(load(RELEASE))
    doc["jobs"]["publish"]["needs"] = ["validate"]
    doc["jobs"]["publish"]["if"] = "always()"
    assert gate_bypasses(doc) == [
        "publish does not need ci",
        "publish does not require ci to succeed",
    ]


def test_only_publish_writes() -> None:
    doc = load(RELEASE)
    publish = jobs(doc)["publish"]
    assert publish["permissions"] == {"contents": "write", "packages": "write"}
    others = {name: job for name, job in jobs(doc).items() if name != "publish"}
    assert write_permissions({"jobs": others}) == []


def test_publish_shares_the_rollback_concurrency_group() -> None:
    assert jobs(load(RELEASE))["publish"]["concurrency"] == {
        "group": "release-latest",
        "cancel-in-progress": False,
    }


def test_validate_outputs_the_engine_image() -> None:
    validate = jobs(load(RELEASE))["validate"]
    assert validate["outputs"]["engine_image"] == "${{ steps.validate.outputs.engine_image }}"


def test_release_tags_ruleset_is_admin_only() -> None:
    doc: dict[str, Any] = json.loads(
        (WORKFLOWS.parent / "rulesets" / "release-tags.json").read_text()
    )
    assert doc["name"] == "release-tags"
    assert doc["target"] == "tag"
    assert doc["enforcement"] == "active"
    assert doc["conditions"]["ref_name"] == {"include": ["refs/tags/v*"], "exclude": []}
    assert sorted(rule["type"] for rule in doc["rules"]) == ["creation", "deletion", "update"]
    assert doc["bypass_actors"] == [
        {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]
