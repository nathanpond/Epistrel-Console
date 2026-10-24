"""Open, update or close the single "Engine API drift" issue (engine-drift.yml, #40).

    uv run python -m tools.drift_issue --exit-code N [--diff-file F]
        [--engine-digest D] [--engine-version V]

``N`` is ``manage.py engine_schema --check``'s exit code. 1 (drift): create the issue, or comment
on the open one when the diff changed since it was last reported; exit 1 so the run is red.
0 (in sync): close an open drift issue with a note; exit 0. Anything else (the Engine could not be
read, a usage error) is not drift: no issue is touched and the code is passed through.

The issue is found by three things together: opened by github-actions, the exact title, and the
fingerprint as the body's last line, so the story issue that describes this tool never matches.
Diffs are compared by a sha256 stored in the body or comment (the visible diff may be truncated).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TITLE = "Engine API drift: edge differs from the recorded schema"
FINGERPRINT = "<!-- fingerprint: engine-api-drift|console/engine/openapi.json|engine -->"
DIFF_MARKER = "<!-- engine-drift-diff -->"
SHA_RE = re.compile(r"<!-- engine-drift-sha256: ([0-9a-f]{64}) -->")
BOTS = frozenset({"github-actions[bot]", "app/github-actions", "github-actions"})
LABELS = ("needs-triage", "area:client")
MAX_DIFF = 60_000
TRUNCATED = "… (truncated — full diff in the run's engine-drift-diff artifact)"

Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class Context:
    repo: str | None
    run_url: str | None
    engine_digest: str
    engine_version: str


def context_from_env(engine_digest: str, engine_version: str) -> Context:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY") or None
    run_id = os.environ.get("GITHUB_RUN_ID")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None
    return Context(repo, run_url, engine_digest, engine_version)


def digest_of(diff: str) -> str:
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _visible(diff: str) -> str:
    text = diff.rstrip("\n")
    return text if len(text) <= MAX_DIFF else f"{text[:MAX_DIFF]}\n{TRUNCATED}"


def report(diff: str, ctx: Context, *, heading: bool) -> str:
    """The issue body (``heading=True``) or a drift comment (``heading=False``)."""
    lines = ["## Engine API drift", ""] if heading else [DIFF_MARKER, ""]
    lines += [f"Engine: `{ctx.engine_version}` (`{ctx.engine_digest}`)"]
    if ctx.run_url:
        lines += [f"Run: {ctx.run_url}"]
    lines += [
        "",
        "### Difference",
        "",
        "```text",
        _visible(diff),
        "```",
        "",
        "### Next step",
        "",
        "Record the Engine's current schema in a PR, then adjust the client if needed:",
        "",
        "```bash",
        "uv run python manage.py engine_schema --write --url <engine-url>",
        "```",
        "",
        "`<engine-url>` is a running Engine `edge`, e.g. `http://127.0.0.1:8000` with the Engine "
        "repo's compose.",
        "",
        f"<!-- engine-drift-sha256: {digest_of(diff)} -->",
    ]
    if heading:
        lines += [FINGERPRINT]
    return "\n".join(lines) + "\n"


def _login(node: Any) -> str:
    author = node.get("author") or {}
    return str(author.get("login", ""))


def find_issue(issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The oldest open issue the drift check itself opened, or None."""
    matches = [
        issue
        for issue in issues
        if _login(issue) in BOTS
        and issue.get("title") == TITLE
        and (str(issue.get("body") or "").rstrip().splitlines() or [""])[-1] == FINGERPRINT
    ]
    return min(matches, key=lambda issue: int(issue["number"])) if matches else None


def last_reported_sha(issue: dict[str, Any]) -> str | None:
    """The sha256 of the newest bot drift comment, else the body's."""
    for comment in reversed(issue.get("comments") or []):
        body = str(comment.get("body") or "")
        if _login(comment) in BOTS and DIFF_MARKER in body:
            match = SHA_RE.search(body)
            if match:
                return match.group(1)
    match = SHA_RE.search(str(issue.get("body") or ""))
    return match.group(1) if match else None


def _repo_args(ctx: Context) -> list[str]:
    return ["-R", ctx.repo] if ctx.repo else []


def run(exit_code: int, diff: str, ctx: Context, gh: Runner) -> int:
    if exit_code not in (0, 1):
        return exit_code  # unavailability is not drift: touch nothing
    listing = gh(
        [
            "issue",
            "list",
            *_repo_args(ctx),
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "number,title,body,author,comments",
        ]
    )
    issue = find_issue(json.loads(listing or "[]"))
    if exit_code == 0:
        if issue is not None:
            gh(
                [
                    "issue",
                    "close",
                    str(issue["number"]),
                    *_repo_args(ctx),
                    "--reason",
                    "completed",
                    "--comment",
                    f"Engine edge matches the recorded schema again ({ctx.engine_digest})",
                ]
            )
        return 0
    if issue is None:
        labels = [arg for label in LABELS for arg in ("--label", label)]
        gh(
            [
                "issue",
                "create",
                *_repo_args(ctx),
                "--title",
                TITLE,
                *labels,
                "--body",
                report(diff, ctx, heading=True),
            ]
        )
        return 1
    if last_reported_sha(issue) != digest_of(diff):
        number = str(issue["number"])
        gh(
            [
                "issue",
                "comment",
                number,
                *_repo_args(ctx),
                "--body",
                report(diff, ctx, heading=False),
            ]
        )
        gh(["issue", "edit", number, *_repo_args(ctx), "--add-label", "needs-triage"])
    return 1


def gh_runner(args: list[str]) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise RuntimeError("the gh CLI is not installed")
    return subprocess.run(
        [gh, *args], check=True, capture_output=True, text=True, timeout=120
    ).stdout


def main(argv: Sequence[str] | None = None, gh: Runner = gh_runner) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.drift_issue")
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--diff-file", type=Path, default=None)
    parser.add_argument("--engine-digest", default="unknown")
    parser.add_argument("--engine-version", default="unknown")
    args = parser.parse_args(argv)
    diff = ""
    if args.diff_file is not None and args.exit_code == 1:
        diff = args.diff_file.read_text(encoding="utf-8")
    ctx = context_from_env(args.engine_digest or "unknown", args.engine_version or "unknown")
    return run(args.exit_code, diff, ctx, gh)


if __name__ == "__main__":
    sys.exit(main())
