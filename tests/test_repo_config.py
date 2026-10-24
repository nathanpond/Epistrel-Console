"""Repo settings the gate relies on: strict mypy, Dependabot's ecosystems, the main ruleset."""

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ECOSYSTEMS = {"uv", "github-actions", "docker"}


def dependabot_problems(doc: dict[str, Any]) -> list[str]:
    problems = []
    if doc.get("version") != 2:
        problems.append("version is not 2")
    updates = doc.get("updates") or []
    found = [u.get("package-ecosystem") for u in updates]
    if sorted(found) != sorted(ECOSYSTEMS):
        problems.append(f"ecosystems {sorted(found)} != {sorted(ECOSYSTEMS)}")
    for update in updates:
        name = update.get("package-ecosystem")
        if update.get("directory") != "/":
            problems.append(f"{name}: directory is not /")
        if (update.get("schedule") or {}).get("interval") != "weekly":
            problems.append(f"{name}: interval is not weekly")
    return problems


def ruleset_problems(doc: dict[str, Any]) -> list[str]:
    problems = []
    if doc.get("enforcement") != "active":
        problems.append("not active")
    rules = {rule["type"]: rule.get("parameters", {}) for rule in doc.get("rules", [])}
    if "pull_request" not in rules:
        problems.append("no pull_request rule")
    checks = rules.get("required_status_checks")
    if checks is None:
        return [*problems, "no required_status_checks rule"]
    if checks.get("strict_required_status_checks_policy") is not True:
        problems.append("not strict")
    if {"context": "gate", "integration_id": 15368} not in checks.get("required_status_checks", []):
        problems.append("gate (GitHub Actions, 15368) is not required")
    return problems


def test_mypy_is_strict() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert pyproject["tool"]["mypy"]["strict"] is True


def test_dependabot_covers_uv_actions_and_docker() -> None:
    doc = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    assert dependabot_problems(doc) == []


def test_dependabot_missing_docker_fails() -> None:
    doc = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    doc["updates"] = [u for u in doc["updates"] if u["package-ecosystem"] != "docker"]
    assert dependabot_problems(doc) == [
        "ecosystems ['github-actions', 'uv'] != ['docker', 'github-actions', 'uv']"
    ]


def test_dependabot_daily_fails() -> None:
    doc = yaml.safe_load((REPO_ROOT / ".github" / "dependabot.yml").read_text())
    doc["updates"][0]["schedule"]["interval"] = "daily"
    assert len(dependabot_problems(doc)) == 1


def load_ruleset() -> dict[str, Any]:
    path = REPO_ROOT / ".github" / "rulesets" / "main-pr-required.json"
    doc: dict[str, Any] = json.loads(path.read_text())
    return doc


def test_main_ruleset_requires_gate() -> None:
    doc = load_ruleset()
    assert doc["name"] == "main-pr-required"
    assert doc["target"] == "branch"
    assert doc["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert ruleset_problems(doc) == []
    # PUT/POST input shape: no server-side fields.
    assert set(doc) == {"name", "target", "enforcement", "bypass_actors", "conditions", "rules"}


def test_ruleset_without_gate_fails() -> None:
    doc = copy.deepcopy(load_ruleset())
    for rule in doc["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [{"context": "lint"}]
            rule["parameters"]["strict_required_status_checks_policy"] = False
    assert ruleset_problems(doc) == ["not strict", "gate (GitHub Actions, 15368) is not required"]


def test_ruleset_without_pull_request_rule_fails() -> None:
    doc = copy.deepcopy(load_ruleset())
    doc["rules"] = [rule for rule in doc["rules"] if rule["type"] != "pull_request"]
    assert ruleset_problems(doc) == ["no pull_request rule"]


def test_inactive_ruleset_fails() -> None:
    for enforcement in ("evaluate", "disabled"):
        doc = copy.deepcopy(load_ruleset())
        doc["enforcement"] = enforcement
        assert ruleset_problems(doc) == ["not active"]


def test_ruleset_without_status_checks_fails() -> None:
    doc = copy.deepcopy(load_ruleset())
    doc["rules"] = [r for r in doc["rules"] if r["type"] != "required_status_checks"]
    assert ruleset_problems(doc) == ["no required_status_checks rule"]


def test_readme_names_the_pinned_python() -> None:
    python = (REPO_ROOT / ".python-version").read_text().strip()
    readme = (REPO_ROOT / "README.md").read_text()
    assert f"Python {python} (pinned in `.python-version`" in readme
