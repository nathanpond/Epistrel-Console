"""A story's own policies (#54): the list, the add / edit form and its ``policies`` upsert."""

import re
from typing import Any

from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.settings.helpers import (
    MARA,
    POLICY,
    POLICY_URL,
    SETTINGS,
    STORY,
    TOBIN,
    URL,
    answer_page,
    body,
    calls,
    error,
    form_data,
    section,
    settings_json,
    snapshot_of,
    writes,
    written_json,
)

NO_WAR = "01M5R8954SQXFAN5N439FEG48J"
WITHHELD = "01M5R8954SQXFAN5N439FEG48M"


def _page(client: Client, engine: FakeEngine, query: str = "") -> str:
    answer_page(engine, settings=settings_json("get_story_settings_policies"))
    html: str = client.get(URL + query).content.decode()
    return html


def _policy_json(**changes: Any) -> dict[str, Any]:
    policy = {
        "id": NO_WAR,
        "scope": "story",
        "scope_id": STORY,
        "story_id": STORY,
        "name": "No war",
        "applies_to": ["text_output"],
        "kind": "deterministic",
        "effect": "deny",
        "rule": {"type": "wordlist", "words": ["the war"]},
        "age_condition": None,
        "message": "No war talk.",
        "version": 1,
        "created_by": "console:nate",
        "updated_by": "console:nate",
        "created_at": "2026-10-25T00:44:59.417347Z",
        "updated_at": "2026-10-25T00:44:59.417347Z",
        "retired_at": None,
    }
    policy.update(changes)
    return policy


def _new_policy(html: str, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = form_data(html, POLICY_URL)
    data.update(
        {
            "name": "Tobin keeps quiet",
            "applies_to": ["text_input", "text_output"],
            "rule_type": "wordlist",
            "words": "the vault\nthe key",
            "message": "Tobin never mentions the vault.",
        }
    )
    data.update(changes)
    return data


def test_the_list_shows_the_storys_own_policies(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)

    own = section(html, "story-policies")
    assert "Story policies cannot be removed in this Engine version" in own
    assert "No war" in own
    assert "Tobin keeps quiet" in own
    assert "No slurs" not in own  # a global policy is not the story's
    assert "Remove" not in own
    assert f"?policy={NO_WAR}#story-policies" in own
    # A withheld name is shown as such and not offered for editing.
    assert "<em>(name withheld — erasure pending)</em>" in own
    assert f"?policy={WITHHELD}" not in own
    # Every effective policy is listed read-only with its source and kind.
    effective = section(html, "effective-policies")
    assert "No slurs" in effective
    assert "<td>model_assisted</td>" in effective
    assert "(name withheld — erasure pending)" in effective


def test_add_sends_one_policy_without_an_id(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    response = operator_client.post(POLICY_URL, _new_policy(html))

    assert response.status_code == 302
    assert response["Location"] == f"{URL}#story-policies"
    [patch] = writes(engine)
    assert body(patch) == {
        "policies": [
            {
                "scope": "story",
                "name": "Tobin keeps quiet",
                "applies_to": ["text_input", "text_output"],
                "rule": {"type": "wordlist", "words": ["the vault", "the key"]},
                "age_condition": None,
                "message": "Tobin never mentions the vault.",
                "effect": "deny",
            }
        ]
    }
    assert EngineOperation.objects.get().kind == "patch_story_settings"
    flashed = operator_client.get(URL).content.decode()
    assert "Added story policy “Tobin keeps quiet” — config revision 4" in flashed


def test_a_character_policy_names_its_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    assert f'<option value="character:{MARA}">Character: Mara</option>' in html
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    operator_client.post(
        POLICY_URL,
        _new_policy(
            html,
            scope=f"character:{TOBIN}",
            rule_type="entity_list",
            names="",
            age_condition="minor_involved_or_present",
        ),
    )

    [item] = body(writes(engine)[0])["policies"]
    assert item["scope"] == "character"
    assert item["scope_id"] == TOBIN
    assert item["rule"] == {"type": "entity_list", "names": []}
    assert item["age_condition"] == "minor_involved_or_present"


def test_edit_prefills_and_resends_the_id(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", POLICY, 200, json=_policy_json())
    html = _page(operator_client, engine, f"?policy={NO_WAR}")

    assert "Edit policy: No war" in html
    assert f'name="policy_id" value="{NO_WAR}"' in html
    assert "the war</textarea>" in html
    engine.respond("PATCH", SETTINGS, 200, json=written_json())

    data: dict[str, Any] = form_data(html, POLICY_URL)
    data["applies_to"] = ["text_output"]
    data["message"] = "Never the war."
    operator_client.post(POLICY_URL, data)

    [item] = body(writes(engine)[0])["policies"]
    assert item["id"] == NO_WAR
    assert item["message"] == "Never the war."
    assert item["rule"] == {"type": "wordlist", "words": ["the war"]}
    flashed = operator_client.get(URL).content.decode()
    assert "Updated story policy “No war”" in flashed


def test_a_withheld_policy_is_not_editable(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, f"?policy={WITHHELD}")

    assert "That policy is not one of this story&#x27;s editable policies." in html
    assert calls(engine, "GET", f"/admin/policies/{WITHHELD}") == []
    assert "Add a story policy" in html


def test_cannot_relax_is_explained(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        422,
        json=error(
            "cannot_relax",
            "a policy can only deny; lower scopes add policies and never relax them",
            field="body.policies[0].effect",
            reason="effect",
            effect="allow",
        ),
    )

    page = operator_client.post(POLICY_URL, _new_policy(html, effect="allow")).content.decode()

    assert (
        "A story policy can only add restrictions — a policy can only deny; lower scopes add "
        "policies and never relax them"
    ) in page
    assert '<option value="allow" selected>' in page  # input kept


def test_a_422_on_the_rule_is_placed(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        422,
        json=error(
            "validation_error",
            "bad",
            errors=[{"field": "body.policies[0].rule.wordlist.words[1]", "message": "too odd"}],
        ),
    )

    page = operator_client.post(POLICY_URL, _new_policy(html)).content.decode()

    words = re.search(r'<div class="field field-words">.*?</div>', page, re.S)
    assert words is not None
    assert "too odd" in words.group(0)


def test_an_invalid_policy_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()

    page = operator_client.post(POLICY_URL, _new_policy(html, words="")).content.decode()

    assert engine.requests == []
    assert "Enter at least one word or phrase." in page


def test_busy_on_a_policy_save(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PATCH",
        SETTINGS,
        423,
        json=error("story_busy", "busy", reason="in_flight", operation_id="01M5R8954SQXFAN5N4"),
        headers={"Retry-After": "3"},
    )

    page = operator_client.post(POLICY_URL, _new_policy(html)).content.decode()

    assert "Story busy — another turn is running" in page
    assert 'value="Tobin keeps quiet"' in page


def test_the_snapshot_carries_the_cast(operator_client: Client, engine: FakeEngine) -> None:
    """A refused submit re-renders from the snapshot: no story read."""
    html = _page(operator_client, engine)
    engine.requests.clear()

    page = operator_client.post(
        POLICY_URL, {**_new_policy(html, name=""), "snapshot": snapshot_of(html)}
    ).content.decode()

    assert engine.requests == []
    assert f'<option value="character:{TOBIN}">Character: Tobin</option>' in page
