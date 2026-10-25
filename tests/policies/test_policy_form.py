"""The shared policy forms (``console/forms/policy.py``, #47 and #54)."""

from typing import Any

import pytest

from console.engine.models import Policy
from console.forms.policy import (
    POLICY_ERROR_PATHS,
    STORY_RULE_TYPES,
    PolicyContentForm,
    entry_lines,
    policy_initial,
)
from tests.policies.helpers import MINORS, TEEN_POLICY, policy


def content(**changes: Any) -> dict[str, Any]:
    return {
        "name": "n",
        "applies_to": ["text_output"],
        "rule_type": "wordlist",
        "words": "a",
        "on_uncertain": "",
        "age_condition": "",
        "message": "m",
        **changes,
    }


def test_entry_lines_trims_drops_blanks_and_duplicates() -> None:
    assert entry_lines("  a \n\n b\na\n\t\nc  ") == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ("rule", "rule_type"),
        ("rule.type", "rule_type"),
        ("rule.wordlist.words", "words"),
        ("rule.wordlist.words[3]", "words"),
        ("rule.regex.patterns[0]", "patterns"),
        ("rule.model_assisted.prompt", "prompt"),
        ("rule.model_assisted.on_uncertain", "on_uncertain"),
        ("rule.entity_list.names[1]", "names"),
        ("rule.words", "words"),
        ("applies_to[0]", "applies_to"),
        ("scope_id", "scope"),
    ],
)
def test_error_paths_land_on_the_input_shown(path: str, field: str) -> None:
    assert POLICY_ERROR_PATHS[path] == field
    assert path in POLICY_ERROR_PATHS


def test_unknown_error_paths_are_not_aliased() -> None:
    assert "rule.wordlist.extra" not in POLICY_ERROR_PATHS
    assert POLICY_ERROR_PATHS.get("name", "name") == "name"


def test_model_assisted_defaults_on_uncertain_to_violation() -> None:
    form = PolicyContentForm(content(rule_type="model_assisted", prompt="p", words=""))
    assert form.is_valid(), form.errors
    assert form.rule() == {"type": "model_assisted", "prompt": "p", "on_uncertain": "violation"}
    assert form.kind() == "model_assisted"


def test_only_the_chosen_rule_input_is_validated() -> None:
    form = PolicyContentForm(content(patterns="(unclosed", prompt=""))
    assert form.is_valid(), form.errors
    assert form.content()["rule"] == {"type": "wordlist", "words": ["a"]}


def test_story_rule_types_offer_entity_list_with_names_for_54() -> None:
    form = PolicyContentForm(
        content(rule_type="entity_list", names=""), rule_types=STORY_RULE_TYPES
    )
    assert form.is_valid(), form.errors
    # Blank names at story or character scope mean the story's characters.
    assert form.rule() == {"type": "entity_list", "names": []}
    assert list(form.fields)[:7] == [
        "name",
        "applies_to",
        "rule_type",
        "words",
        "patterns",
        "names",
        "prompt",
    ]


@pytest.mark.parametrize("policy_id", [MINORS, TEEN_POLICY])
def test_initial_values_round_trip_to_the_same_content(policy_id: str) -> None:
    current = Policy.from_json(policy(policy_id))
    assert current is not None
    initial = policy_initial(current)
    form = PolicyContentForm({**content(words="", prompt=""), **initial})
    assert form.is_valid(), form.errors
    body = form.content()
    assert body["rule"] == current.rule
    assert body["applies_to"] == current.applies_to
    assert body["age_condition"] == current.age_condition
    assert body["effect"] == "deny"
