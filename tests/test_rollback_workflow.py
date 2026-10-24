"""Guards over rollback.yml (#39): dispatch from main only; nothing mutates before validation."""

import copy
from typing import Any

from tests.test_workflows import WORKFLOWS, jobs, load, triggers

ROLLBACK = WORKFLOWS / "rollback.yml"
MUTATIONS = ("imagetools create", "gh release edit")


def mutation_before_precheck(doc: dict[str, Any]) -> list[str]:
    """Steps that change a tag or a release and run before the `pre` step (or with no `pre`)."""
    steps = jobs(doc)["rollback"]["steps"]
    pre = next((i for i, s in enumerate(steps) if s.get("id") == "pre"), len(steps))
    return [
        str(s.get("name") or s.get("id"))
        for s in steps[: pre + 1]
        if any(m in str(s.get("run", "")) for m in MUTATIONS)
    ]


def test_rollback_is_dispatch_only_with_the_right_inputs() -> None:
    doc = load(ROLLBACK)
    on = triggers(doc)
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["good_version"]["required"] is True
    assert inputs["bad_version"]["required"] is False
    assert doc["concurrency"] == {"group": "release-latest", "cancel-in-progress": False}
    assert jobs(doc)["rollback"]["if"] == "github.ref == 'refs/heads/main'"
    assert doc["permissions"] == {"contents": "read"}
    assert jobs(doc)["rollback"]["permissions"] == {"contents": "write", "packages": "write"}


def test_prechecks_come_before_any_mutation() -> None:
    doc = load(ROLLBACK)
    steps = jobs(doc)["rollback"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    pre = next(i for i, s in enumerate(steps) if s.get("id") == "pre")
    first_create = next(i for i, r in enumerate(runs) if "imagetools create" in r)
    first_edit = next(i for i, r in enumerate(runs) if "gh release edit" in r)
    assert pre < first_create
    assert pre < first_edit
    assert mutation_before_precheck(doc) == []


def test_a_mutation_before_the_prechecks_fails_the_rule() -> None:
    doc = copy.deepcopy(load(ROLLBACK))
    steps = doc["jobs"]["rollback"]["steps"]
    steps.insert(0, {"name": "eager", "run": "docker buildx imagetools create -t x y"})
    assert mutation_before_precheck(doc) == ["eager"]


def test_prechecks_cover_the_refusals() -> None:
    pre = next(s for s in jobs(load(ROLLBACK))["rollback"]["steps"] if s.get("id") == "pre")
    run = pre["run"]
    assert "image tag not found" in run
    assert "in_progress|queued|waiting|pending|requested" in run
    assert "good_version and bad_version are the same" in run
    assert 'gh release view "v$GOOD"' in run
    assert 'gh release view "v$BAD"' in run
