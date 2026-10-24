"""Guards over rollback.yml (#39): dispatch from main only; nothing mutates before validation."""

import copy
import re
from pathlib import Path
from typing import Any

import pytest

from tests.test_workflows import WORKFLOWS, jobs, load, steps, triggers
from tests.workflow_steps import StepRun, run_step, step

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
    assert "image tag not found, or the registry refused" in run
    assert "in_progress|queued|waiting|pending|requested" in run
    assert "good_version and bad_version are the same" in run
    assert 'gh release view "v$GOOD"' in run
    assert 'gh release view "v$BAD"' in run


# --- what a rollback does, and never does (#107) ---------------------------------------------

# Word-bounded: "docker build" must not match "docker buildx imagetools".
BUILDS = re.compile(r"docker/build-push-action@|\bdocker build\b|\bbuildx build\b|\bdocker push\b")
GOOD_DIGEST, BAD_DIGEST, OLD_DIGEST = "sha256:good", "sha256:bad", "sha256:old"


def rollback_job() -> dict[str, Any]:
    job: dict[str, Any] = copy.deepcopy(jobs(load(ROLLBACK))["rollback"])
    return job


def rebuild_steps(doc: dict[str, Any]) -> list[str]:
    """Steps that build or push an image: a rollback only re-points tags at existing ones."""
    return [
        str(s.get("name") or s.get("id") or s.get("uses"))
        for _, s in steps(doc)
        if BUILDS.search(str(s.get("uses", "")) + "\n" + str(s.get("run", "")))
    ]


def test_rollback_never_rebuilds() -> None:
    assert rebuild_steps(load(ROLLBACK)) == []


@pytest.mark.parametrize(
    "added",
    [
        {"name": "rebuild", "uses": "docker/build-push-action@v7"},
        {"name": "rebuild", "run": "docker buildx build --push -t x ."},
        {"name": "rebuild", "run": "docker push x"},
    ],
)
def test_rebuild_rule_bites(added: dict[str, Any]) -> None:
    doc = copy.deepcopy(load(ROLLBACK))
    doc["jobs"]["rollback"]["steps"].append(added)
    assert rebuild_steps(doc) == ["rebuild"]


PROMOTE = "Promote the good release, demote the bad one"
GH_STUB = """
case "$1 $2" in
  "release view") printf 'Bad notes\\n'; exit 0 ;;
  "release edit") exit 0 ;;
esac
"""


def promote(tmp_path: Path, script: str, bad: str = "0.1.1") -> StepRun:
    env = {"GOOD": "0.1.0", "BAD": bad}
    return run_step(script, tmp_path, env=env, stubs={"gh": GH_STUB})


def demote_problems(result: StepRun) -> list[str]:
    """Why the bad release could still be (or become) latest after the step."""
    edits = result.called("gh", "release", "edit", "v0.1.1")
    if len(edits) != 1:
        return [f"{len(edits)} edits of the bad release"]
    problems = [
        f"bad release not {f}" for f in ("--prerelease", "--latest=false") if f not in edits[0]
    ]
    return problems


def test_promote_makes_good_latest_and_demotes_bad(tmp_path: Path) -> None:
    result = promote(tmp_path, step(rollback_job(), PROMOTE)["run"])
    assert result.rc == 0, result.output
    good = result.called("gh", "release", "edit", "v0.1.0")
    assert good == [
        ["gh", "release", "edit", "v0.1.0", "--repo", "nathanpond/Epistrel-Console",
         "--latest", "--prerelease=false"],
    ]  # fmt: skip
    assert demote_problems(result) == []
    note = (tmp_path / "bad-body.md").read_text()
    assert "use v0.1.0." in note
    assert "Bad notes" in note


def test_without_a_bad_version_only_good_is_edited(tmp_path: Path) -> None:
    result = promote(tmp_path, step(rollback_job(), PROMOTE)["run"], bad="")
    assert result.rc == 0, result.output
    assert [c[3] for c in result.called("gh", "release", "edit")] == ["v0.1.0"]


def test_demote_rule_bites(tmp_path: Path) -> None:
    script = step(rollback_job(), PROMOTE)["run"]
    assert script.count("--prerelease --latest=false") == 1
    result = promote(tmp_path, script.replace("--prerelease --latest=false", "--prerelease"))
    assert demote_problems(result) == ["bad release not --latest=false"]


RETAG = "Re-point floating tags"


DENIED = "ERROR: unexpected status from HEAD request: 401 Unauthorized"


def docker_stub(tags: dict[str, str], denied: tuple[str, ...] = ()) -> str:
    """``docker buildx imagetools``: ``inspect`` answers from ``tags``, refuses the ``denied``
    tags with a 401 and any other with "not found"; ``create`` succeeds."""
    cases = "".join(f'    "$IMAGE:{t}") echo "{DENIED}" >&2; exit 1 ;;\n' for t in denied)
    cases += "".join(f'    "$IMAGE:{t}") echo "{d}"; exit 0 ;;\n' for t, d in tags.items())
    return f"""
if [ "$1 $2 $3" = "buildx imagetools inspect" ]; then
  case "$4" in
{cases}  esac
  echo "ERROR: $4: not found" >&2; exit 1
fi
[ "$1 $2 $3" = "buildx imagetools create" ] && exit 0
"""


def run_retag(
    tmp_path: Path, good: str, bad: str, tags: dict[str, str], denied: tuple[str, ...] = ()
) -> StepRun:
    env = {"GOOD": good, "BAD": bad, "GOOD_DIGEST": GOOD_DIGEST, "BAD_DIGEST": BAD_DIGEST}
    return run_step(step(rollback_job(), RETAG)["run"], tmp_path, env=env,
                    stubs={"docker": docker_stub(tags, denied)})  # fmt: skip


def retag(tmp_path: Path, good: str, bad: str, tags: dict[str, str]) -> list[str]:
    """The tags the step re-pointed at the good digest."""
    result = run_retag(tmp_path, good, bad, tags)
    assert result.rc == 0, result.output
    moved = []
    for call in result.called("docker", "buildx", "imagetools", "create"):
        assert call[-1] == f"ghcr.io/nathanpond/epistrel-console@{GOOD_DIGEST}"
        moved.append(call[call.index("-t") + 1].rsplit(":", 1)[1])
    assert result.outputs["moved"].split() == moved
    return moved


@pytest.mark.parametrize(
    ("good", "bad", "tags", "moved"),
    [
        # Same minor line: latest, X.Y and X all pointed at the bad image, all move back.
        ("1.2.4", "1.2.5", {"latest": BAD_DIGEST, "1.2": BAD_DIGEST, "1": BAD_DIGEST},
         ["latest", "1.2", "1"]),
        # Bad 1.3.0, good 1.2.5: `1` moves, `1.2` stays even if it pointed at the bad image,
        # and `1.3` (bad's own line) is never touched.
        ("1.2.5", "1.3.0", {"latest": BAD_DIGEST, "1.2": BAD_DIGEST, "1.3": BAD_DIGEST,
                            "1": BAD_DIGEST}, ["latest", "1"]),
        # Different major: only latest.
        ("1.9.0", "2.0.0", {"latest": BAD_DIGEST, "1.9": BAD_DIGEST, "1": BAD_DIGEST},
         ["latest"]),
        # A floating tag that is not on the bad image stays.
        ("1.2.4", "1.2.5", {"latest": BAD_DIGEST, "1.2": OLD_DIGEST, "1": OLD_DIGEST},
         ["latest"]),
        # No bad version: only latest.
        ("1.2.4", "", {"latest": BAD_DIGEST, "1.2": BAD_DIGEST, "1": BAD_DIGEST}, ["latest"]),
    ],
)  # fmt: skip
def test_floating_tags_move_only_within_the_good_line(
    tmp_path: Path, good: str, bad: str, tags: dict[str, str], moved: list[str]
) -> None:
    assert retag(tmp_path, good, bad, tags) == moved


def pre_gh(running: int = 0, missing: str = "") -> str:
    """``gh``: ``running`` release runs unfinished; every release exists except ``missing``."""
    return f"""
case "$1 $2" in
  "run list") echo {running}; exit 0 ;;
  "release view") [ "$3" = "{missing or "-"}" ] && exit 1; exit 0 ;;
esac
"""


PRE_GH = pre_gh()


def precheck(
    tmp_path: Path, good: str, bad: str = "", docker: str | None = None, gh: str = PRE_GH
) -> StepRun:
    stubs = {"gh": gh, "docker": docker or docker_stub({good: GOOD_DIGEST, bad: BAD_DIGEST})}
    pre = step(rollback_job(), "pre")["run"]
    return run_step(pre, tmp_path, env={"GOOD": good, "BAD": bad}, stubs=stubs)


def test_prechecks_pass_for_bare_versions(tmp_path: Path) -> None:
    result = precheck(tmp_path, "0.1.0", "0.1.1")
    assert result.rc == 0, result.output
    assert result.outputs == {"good_digest": GOOD_DIGEST, "bad_digest": BAD_DIGEST}


@pytest.mark.parametrize(
    ("good", "bad", "why"),
    [
        ("0.1.0\nx", "", "good_version must be bare X.Y.Z"),
        ("x\n0.1.0", "", "good_version must be bare X.Y.Z"),
        ("v0.1.0", "", "good_version must be bare X.Y.Z"),
        ("0.1.0-rc.1", "", "good_version must be bare X.Y.Z"),
        ("0.1.0", "0.1.1\nx", "bad_version must be bare X.Y.Z"),
        ("0.1.0", "0.1.0", "are the same"),
    ],
)
def test_prechecks_refuse_bad_versions_for_their_shape(
    tmp_path: Path, good: str, bad: str, why: str
) -> None:
    result = precheck(tmp_path, good, bad)
    assert result.rc == 1
    assert why in result.output
    # Refused at validation: nothing was asked of the registry or GitHub.
    assert result.calls == []


def test_an_inspect_failure_reports_the_registrys_message(tmp_path: Path) -> None:
    denied = 'echo "ERROR: unexpected status from HEAD request: 401 Unauthorized" >&2; exit 1'
    result = precheck(tmp_path, "0.1.0", docker=denied)
    assert result.rc == 1
    assert "401 Unauthorized" in result.output
    assert result.called("gh", "release", "edit") == []


# --- every pre-check refusal, the bad image's inspect, the post-condition (#114, #115) -------

READS = (
    ("gh", "run", "list"),
    ("gh", "release", "view"),
    ("docker", "buildx", "imagetools", "inspect"),
)


def only_reads(result: StepRun) -> list[list[str]]:
    """The calls that are not one of the pre-check's reads (a refusal must make none)."""
    return [c for c in result.calls if not any(c[: len(r)] == list(r) for r in READS)]


@pytest.mark.parametrize(
    ("gh", "why", "last"),
    [
        (pre_gh(running=1), "a release run is not finished yet (1)", ["gh", "run", "list"]),
        (pre_gh(missing="v0.1.0"), "release v0.1.0 does not exist", ["gh", "release", "view"]),
        (pre_gh(missing="v0.1.1"), "release v0.1.1 does not exist", ["gh", "release", "view"]),
    ],
    ids=["release run in progress", "good release missing", "bad release missing"],
)
def test_prechecks_refuse_and_stop(tmp_path: Path, gh: str, why: str, last: list[str]) -> None:
    result = precheck(tmp_path, "0.1.0", "0.1.1", gh=gh)
    assert result.rc == 1
    assert why in result.output
    assert "nothing changed" in result.output
    assert result.outputs == {}
    assert only_reads(result) == []
    # The refusing call is the last one: nothing further was asked.
    assert result.calls[-1][: len(last)] == last


def test_a_missing_bad_image_has_nothing_to_unfloat(tmp_path: Path) -> None:
    result = precheck(tmp_path, "0.1.0", "0.1.1", docker=docker_stub({"0.1.0": GOOD_DIGEST}))
    assert result.rc == 0, result.output
    assert result.outputs == {"good_digest": GOOD_DIGEST, "bad_digest": ""}


def test_a_refused_inspect_of_the_bad_image_stops_the_rollback(tmp_path: Path) -> None:
    docker = docker_stub({"0.1.0": GOOD_DIGEST}, denied=("0.1.1",))
    result = precheck(tmp_path, "0.1.0", "0.1.1", docker=docker)
    assert result.rc == 1
    assert "cannot inspect ghcr.io/nathanpond/epistrel-console:0.1.1" in result.output
    assert "401 Unauthorized" in result.output
    assert result.outputs == {}


def test_a_refused_inspect_of_a_floating_tag_moves_nothing(tmp_path: Path) -> None:
    result = run_retag(tmp_path, "1.2.4", "1.2.5", {"latest": BAD_DIGEST, "1": BAD_DIGEST},
                       denied=("1.2",))  # fmt: skip
    assert result.rc == 1
    assert "cannot inspect ghcr.io/nathanpond/epistrel-console:1.2" in result.output
    assert "401 Unauthorized" in result.output
    assert result.called("docker", "buildx", "imagetools", "create") == []


def test_a_missing_floating_tag_reads_as_none(tmp_path: Path) -> None:
    result = run_retag(tmp_path, "1.2.4", "1.2.5", {"latest": BAD_DIGEST, "1": BAD_DIGEST})
    assert result.rc == 0, result.output
    assert result.outputs["moved"].split() == ["latest", "1"]
    assert result.summary == (
        "## Image tags\n\n| tag | before | after |\n|---|---|---|\n"
        f"| `latest` | `{BAD_DIGEST}` | `{GOOD_DIGEST}` |\n"
        "| `1.2` | `(none)` | unchanged |\n"
        f"| `1` | `{BAD_DIGEST}` | `{GOOD_DIGEST}` |\n"
    )


def test_the_bad_release_is_demoted_with_exactly_these_flags(tmp_path: Path) -> None:
    result = promote(tmp_path, step(rollback_job(), PROMOTE)["run"])
    assert result.rc == 0, result.output
    assert result.called("gh", "release", "edit", "v0.1.1") == [
        ["gh", "release", "edit", "v0.1.1", "--repo", "nathanpond/Epistrel-Console",
         "--prerelease", "--latest=false", "--notes-file", "bad-body.md"],
    ]  # fmt: skip
    assert result.summary == "- v0.1.0 marked latest\n- v0.1.1 demoted to pre-release\n"


POST = "Post-condition"


def post_condition(tmp_path: Path, tags: dict[str, str], script: str | None = None) -> StepRun:
    env = {"GOOD_DIGEST": GOOD_DIGEST, "MOVED": " latest 1"}
    script = script or step(rollback_job(), POST)["run"]
    return run_step(script, tmp_path, env=env, stubs={"docker": docker_stub(tags)})


def test_the_post_condition_checks_every_moved_tag(tmp_path: Path) -> None:
    ok = post_condition(tmp_path / "ok", {"latest": GOOD_DIGEST, "1": GOOD_DIGEST})
    assert ok.rc == 0, ok.output
    assert ok.summary == f"- every re-pointed tag resolves to {GOOD_DIGEST}\n"
    wrong = post_condition(tmp_path / "wrong", {"latest": GOOD_DIGEST, "1": BAD_DIGEST})
    assert wrong.rc == 1
    assert f"epistrel-console:1 resolves to {BAD_DIGEST}, expected {GOOD_DIGEST}" in wrong.output


def test_post_condition_rule_bites(tmp_path: Path) -> None:
    script = step(rollback_job(), POST)["run"]
    old = '[ "$now" = "$GOOD_DIGEST" ] ||'
    assert script.count(old) == 1
    tags = {"latest": GOOD_DIGEST, "1": BAD_DIGEST}
    assert post_condition(tmp_path, tags, script.replace(old, "true ||")).rc == 0
