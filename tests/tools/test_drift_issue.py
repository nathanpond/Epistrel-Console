"""tools.drift_issue against a fake `gh`: one issue, no spam, self-closing, outages ignored."""

import json
from pathlib import Path
from typing import Any

import pytest

from tools import drift_issue
from tools.drift_issue import (
    DIFF_MARKER,
    FINGERPRINT,
    MAX_DIFF,
    TITLE,
    TRUNCATED,
    Context,
    find_issue,
    report,
    run,
)

CTX = Context("nathanpond/Epistrel-Console", "https://gh/run/1", "eng@sha256:aa", "m3")
DIFF = "Components changed:\n  schemas/HealthResponse\n"
WRITES = ("create", "comment", "close", "edit")


class FakeGh:
    def __init__(self, issues: list[dict[str, Any]] | None = None) -> None:
        self.issues = issues or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        return ""

    def writes(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "issue" and c[1] in WRITES]


def bot_issue(number: int = 7, diff: str = DIFF, **extra: Any) -> dict[str, Any]:
    issue = {
        "number": number,
        "title": TITLE,
        "body": report(diff, CTX, heading=True),
        "author": {"login": "app/github-actions", "is_bot": True},
        "comments": [],
    }
    issue.update(extra)
    return issue


def test_drift_without_an_issue_creates_one() -> None:
    gh = FakeGh()
    assert run(1, DIFF, CTX, gh) == 1
    (create,) = gh.writes()
    assert create[:2] == ["issue", "create"]
    assert create[create.index("--title") + 1] == TITLE
    labels = [create[i + 1] for i, a in enumerate(create) if a == "--label"]
    assert labels == ["needs-triage", "area:client"]
    body = create[create.index("--body") + 1]
    assert body.rstrip().splitlines()[-1] == FINGERPRINT
    assert "schemas/HealthResponse" in body
    assert "Engine: `m3` (`eng@sha256:aa`)" in body
    assert "Run: https://gh/run/1" in body
    assert "engine_schema --write --url <engine-url>" in body
    assert create[2:4] == ["-R", "nathanpond/Epistrel-Console"]


def test_same_diff_on_an_open_issue_does_not_comment() -> None:
    gh = FakeGh([bot_issue()])
    assert run(1, DIFF, CTX, gh) == 1
    assert gh.writes() == []


def test_changed_diff_comments_once_and_retriages() -> None:
    gh = FakeGh([bot_issue()])
    changed = DIFF + "Operations added:\n  GET /new\n"
    assert run(1, changed, CTX, gh) == 1
    comment, edit = gh.writes()
    assert comment[:3] == ["issue", "comment", "7"]
    text = comment[comment.index("--body") + 1]
    assert text.startswith(DIFF_MARKER)
    assert "GET /new" in text
    assert "## Engine API drift" not in text
    assert edit[:3] == ["issue", "edit", "7"]
    assert edit[-2:] == ["--add-label", "needs-triage"]


def test_newest_bot_comment_is_the_baseline() -> None:
    changed = DIFF + "Operations added:\n  GET /new\n"
    comment = {
        "author": {"login": "app/github-actions"},
        "body": report(changed, CTX, heading=False),
    }
    human = {"author": {"login": "someone"}, "body": DIFF_MARKER + "\n" + "x" * 10}
    gh = FakeGh([bot_issue(comments=[comment, human])])
    assert run(1, changed, CTX, gh) == 1
    assert gh.writes() == []


def test_a_changed_digest_alone_is_not_a_new_diff() -> None:
    gh = FakeGh([bot_issue()])
    other = Context(CTX.repo, CTX.run_url, "eng@sha256:ff", "m4")
    assert run(1, DIFF, other, gh) == 1
    assert gh.writes() == []


def test_in_sync_closes_the_open_issue() -> None:
    gh = FakeGh([bot_issue()])
    assert run(0, "", CTX, gh) == 0
    (close,) = gh.writes()
    assert close[:3] == ["issue", "close", "7"]
    assert close[close.index("--reason") + 1] == "completed"
    assert close[close.index("--comment") + 1] == (
        "Engine edge matches the recorded schema again (eng@sha256:aa)"
    )


def test_in_sync_without_an_issue_writes_nothing() -> None:
    gh = FakeGh()
    assert run(0, "", CTX, gh) == 0
    assert gh.writes() == []


@pytest.mark.parametrize("code", [2, 3, 4])
def test_unavailability_is_not_drift(code: int) -> None:
    gh = FakeGh([bot_issue()])
    assert run(code, DIFF, CTX, gh) == code
    assert gh.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"author": {"login": "nathanpond"}},
        {"title": "Flag Engine API drift daily with a single self-closing issue"},
        {"body": "Mentions " + FINGERPRINT + " in the middle.\n\nMore text."},
    ],
)
def test_lookalike_issues_never_match(change: dict[str, Any]) -> None:
    assert find_issue([bot_issue(**change)]) is None


def test_oldest_match_wins() -> None:
    assert find_issue([bot_issue(number=12), bot_issue(number=9)])["number"] == 9  # type: ignore[index]


def test_long_diff_is_truncated_but_hashed_whole() -> None:
    long_diff = "x" * (MAX_DIFF + 500)
    body = report(long_diff, CTX, heading=True)
    assert TRUNCATED in body
    assert long_diff not in body
    gh = FakeGh([bot_issue(diff=long_diff)])
    assert run(1, long_diff, CTX, gh) == 1
    assert gh.writes() == []


def test_main_reads_the_diff_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    diff_file = tmp_path / "diff.txt"
    diff_file.write_text(DIFF)
    gh = FakeGh()
    code = drift_issue.main(
        ["--exit-code", "1", "--diff-file", str(diff_file), "--engine-digest", "d"], gh=gh
    )
    assert code == 1
    (create,) = gh.writes()
    body = create[create.index("--body") + 1]
    assert "schemas/HealthResponse" in body
    assert "Run:" not in body
    assert "-R" not in create
