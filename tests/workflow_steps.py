"""Run one workflow step's ``run:`` script under bash, with ``gh`` and ``docker`` stubbed.

The guards in ``test_*workflow*.py`` read the YAML; this runs a step's shell the way the runner
does (``bash -e``, GitHub's default shell), so a test can check what the step *does*: its exit code,
its ``$GITHUB_OUTPUT``, its summary, and every ``gh``/``docker`` call it made. ``gh`` and ``docker``
are always stubbed: one a test leaves unanswered exits 99 instead of reaching a real registry or
release. ``python3`` is this interpreter with the repo importable, for ``python3 -m tools...``.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
STUBBED = ("gh", "docker")
# Each stub logs its argv as one record: fields split by \037, records ended by \036.
LOG_CALL = r"""#!/bin/bash
{ printf '%s' "$(basename "$0")"; for a in "$@"; do printf '\037%s' "$a"; done
  printf '\036'; } >> "$STUB_CALLS"
"""
UNANSWERED = 'echo "stub $(basename "$0"): no answer for: $*" >&2; exit 99\n'


@dataclass
class StepRun:
    rc: int
    output: str
    outputs: dict[str, str]
    summary: str
    calls: list[list[str]]

    def called(self, *prefix: str) -> list[list[str]]:
        """The calls whose argv starts with ``prefix`` (e.g. ``"gh", "release", "create"``)."""
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


def step(job: dict[str, Any], key: str) -> dict[str, Any]:
    """The step in ``job`` whose ``id`` or ``name`` is ``key``."""
    found: dict[str, Any] = next(s for s in job["steps"] if key in (s.get("id"), s.get("name")))
    return found


def run_step(
    script: str,
    workdir: Path,
    env: dict[str, str] | None = None,
    stubs: dict[str, str] | None = None,
) -> StepRun:
    """Run ``script`` in ``workdir``. ``stubs`` maps a command to the bash body answering it.

    A stub body sees the arguments as ``$@`` (its call is already logged) and should ``exit``;
    falling off the end is the "no answer" failure.
    """
    assert "${{" not in script, "the step uses an expression: pass its value through env"
    workdir.mkdir(parents=True, exist_ok=True)
    bin_dir = workdir / ".stub-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in {*STUBBED, *(stubs or {})}:
        body = (stubs or {}).get(name, "")
        path = bin_dir / name
        path.write_text(LOG_CALL + body + "\n" + UNANSWERED, encoding="utf-8")
        path.chmod(0o755)
    python3 = bin_dir / "python3"
    if not python3.exists():
        python3.symlink_to(sys.executable)
    calls, outputs, summary = (workdir / ".calls", workdir / ".output", workdir / ".summary")
    for f in (calls, outputs, summary):
        f.write_text("", encoding="utf-8")
    script_path = workdir / ".step.sh"
    script_path.write_text(script, encoding="utf-8")
    full_env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(workdir),
        "PYTHONPATH": str(REPO),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "STUB_CALLS": str(calls),
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": "nathanpond/Epistrel-Console",
        "IMAGE": "ghcr.io/nathanpond/epistrel-console",
        **(env or {}),
    }
    bash = shutil.which("bash")
    assert bash is not None
    done = subprocess.run(  # noqa: S603
        [bash, "-e", str(script_path)],
        cwd=workdir,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    records = calls.read_text(encoding="utf-8").split("\036")
    return StepRun(
        rc=done.returncode,
        output=done.stdout + done.stderr,
        outputs=dict(
            line.split("=", 1)
            for line in outputs.read_text(encoding="utf-8").splitlines()
            if "=" in line
        ),
        summary=summary.read_text(encoding="utf-8"),
        calls=[r.split("\037") for r in records if r],
    )
