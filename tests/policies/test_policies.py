"""/guardrails/policies/ (#47): list, detail, create, edit and retire guardrail policies."""

import json
import re
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.policies.helpers import (
    IDEMPOTENCY_KEY,
    ITEM,
    LIST,
    MINORS,
    RATINGS,
    RETIRED,
    TEEN,
    TEEN_POLICY,
    answer_list,
    answer_ratings,
    calls,
    error,
    mutation,
    policy,
    wordlist_post,
)

pytestmark = pytest.mark.django_db

URL = "/guardrails/policies/"
MINORS_WARNING = (
    "This is the minors protection policy. Retiring it stops it applying to every story — "
    "removing it is the deployment&#x27;s decision."
)


def row(html: str, policy_id: str) -> str:
    match = re.search(rf'<tr id="policy-{re.escape(policy_id)}">(.*?)</tr>', html, re.S)
    assert match, f"no row for {policy_id}"
    return match.group(1)


def cells(html: str, policy_id: str) -> list[str]:
    found = re.findall(r"<td[^>]*>(.*?)</td>", row(html, policy_id), re.S)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip() for cell in found]


NAME, ID, SCOPE, KIND, APPLIES, AGE, EFFECT, STATUS, ACTIONS = range(9)


def hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, f"no hidden {name}"
    return match.group(1)


def field_html(page: str, name: str) -> str:
    """One form field's block (up to the next field)."""
    return page.split(f'class="field field-{name}"')[1].split('class="field ')[0]


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def sent(request: Any) -> Any:
    return json.loads(request.content)


# --- List ---------------------------------------------------------------------------------------


def test_list_shows_every_policy_with_its_columns(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    html = operator_client.get(URL).content.decode()
    minors = cells(html, MINORS)
    assert minors[NAME] == "No sexual content involving or near minors seeded"
    assert minors[ID] == MINORS
    assert minors[SCOPE] == "global"
    assert minors[KIND] == "model_assisted"
    assert minors[APPLIES] == "text_input, text_output"
    assert minors[AGE] == "minor_involved_or_present"
    assert minors[EFFECT] == "deny"
    assert minors[STATUS] == "live"
    teen = cells(html, TEEN_POLICY)
    assert teen[SCOPE] == "rating: Teen"
    assert teen[KIND] == "deterministic"
    assert teen[AGE] == "none"
    assert "seeded" not in teen[NAME]
    retired = cells(html, RETIRED)
    assert retired[STATUS] == "retired 2026-10-24 21:55:00 UTC"
    assert retired[APPLIES] == "text_output, image_prompt (used from M8)"


def test_list_sorts_global_first_then_rating_order_then_name(
    operator_client: Client, engine: FakeEngine
) -> None:
    items = [
        policy(TEEN_POLICY, id="teen-b", name="B teen"),
        policy(
            TEEN_POLICY, id="everyone", name="A everyone", scope_id="00000000000000000000RAT001"
        ),
        policy(TEEN_POLICY, id="teen-a", name="a teen"),
        policy(MINORS),
        policy(RETIRED, id="zz-global", name="Zebra", retired_at=None),
    ]
    answer_list(engine, items)
    html = operator_client.get(URL).content.decode()
    order = re.findall(r'<tr id="policy-([^"]+)">', html)
    assert order == [MINORS, "zz-global", "everyone", "teen-a", "teen-b"]


def test_list_filters_pass_scope_and_include_retired(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    operator_client.get(URL)
    (plain,) = calls(engine, "GET", LIST)
    assert dict(plain.url.params) == {}
    engine.reset()
    operator_client.get(URL, {"scope": "rating", "retired": "1"})
    (filtered,) = calls(engine, "GET", LIST)
    assert dict(filtered.url.params) == {"scope": "rating", "include_retired": "1"}
    engine.reset()
    operator_client.get(URL, {"scope": "nonsense"})
    (ignored,) = calls(engine, "GET", LIST)
    assert dict(ignored.url.params) == {}


def test_list_reads_ratings_once_with_retired_ones(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    operator_client.get(URL)
    (ratings,) = calls(engine, "GET", RATINGS)
    assert dict(ratings.url.params) == {"include_retired": "1"}


def test_list_shows_retired_and_unknown_ratings(
    operator_client: Client, engine: FakeEngine
) -> None:
    items = [policy(TEEN_POLICY), policy(TEEN_POLICY, id="lost", scope_id="01UNKNOWNRATING")]
    answer_list(engine, items)
    answer_ratings(engine, Teen={"retired_at": "2026-10-24T22:00:00Z"})
    html = operator_client.get(URL).content.decode()
    assert cells(html, TEEN_POLICY)[SCOPE] == "rating: Teen (retired)"
    assert cells(html, "lost")[SCOPE] == "rating: 01UNKNOWNRATING"


def test_list_degrades_when_ratings_cannot_be_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    engine.respond("GET", RATINGS, 503, json=error("database_unavailable"))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert cells(html, TEEN_POLICY)[SCOPE] == f"rating: {TEEN}"
    assert "The content ratings could not be read" in html


def test_list_links_guardrail_rejections(operator_client: Client, engine: FakeEngine) -> None:
    answer_list(engine)
    html = operator_client.get(URL).content.decode()
    assert (
        '<a class="rejections" href="/operations/?guardrail=1">Guardrail rejections →</a>' in html
    )


def test_seeded_minors_retire_button_carries_the_warning(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    html = operator_client.get(URL).content.decode()
    minors = row(html, MINORS)
    assert "Retire (minors protection)</a>" in minors
    assert f'title="{MINORS_WARNING}"' in minors
    teen = row(html, TEEN_POLICY)
    assert ">Retire</a>" in teen
    assert "minors protection" not in teen


def test_retired_policies_keep_edit_but_have_no_retire(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_list(engine)
    retired = row(operator_client.get(URL).content.decode(), RETIRED)
    assert f'href="/guardrails/policies/{RETIRED}/edit/"' in retired
    assert "/retire/" not in retired


def test_story_policies_are_listed_without_actions(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = policy(
        TEEN_POLICY, id="story-pol", scope="story", scope_id="01STORY", story_id="01STORY"
    )
    answer_list(engine, [story])
    html = operator_client.get(URL).content.decode()
    story_row = cells(html, "story-pol")
    assert story_row[SCOPE] == "story: 01STORY"
    assert story_row[ACTIONS] == "written through the story's settings"
    assert "/edit/" not in row(html, "story-pol")


# --- Detail -------------------------------------------------------------------------------------


def test_detail_shows_every_field_and_the_full_rule(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=policy(MINORS))
    html = operator_client.get(f"{URL}{MINORS}/").content.decode()
    assert '<span class="seeded">seeded</span>' in html
    assert "Answer JSON {violation, confidence in [0,1]}." in html
    assert "When the model is uncertain: <code>violation</code>" in html
    assert "This content isn&#x27;t allowed because a minor is involved or present." in html
    assert "by system" in html
    assert MINORS_WARNING in html
    assert "Retire (minors protection)" in html
    # A global policy needs no rating names: one read only.
    assert [r.url.path for r in engine.requests] == [f"/admin/policies/{MINORS}"]


def test_detail_lists_a_wordlists_entries_and_its_rating(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=policy(TEEN_POLICY))
    answer_ratings(engine)
    html = operator_client.get(f"{URL}{TEEN_POLICY}/").content.decode()
    assert "<li><code>gore</code></li><li><code>disembowel</code></li>" in html
    assert "rating: Teen" in html
    assert 'class="seeded"' not in html


def test_detail_held_by_erasure_renders_the_error_panel_not_a_500(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        ITEM,
        503,
        json=error("erasure_pending", "held until the erasure is reapplied", story_id="01S"),
        headers={"Retry-After": "30"},
    )
    response = operator_client.get(f"{URL}{TEEN_POLICY}/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Engine error 503 erasure_pending" in html
    assert "held until the erasure is reapplied" in html


# --- Create -------------------------------------------------------------------------------------


def new_page(client: Client, engine: FakeEngine) -> str:
    answer_ratings(engine)
    html = client.get(f"{URL}new/").content.decode()
    engine.reset()
    return html


def test_new_form_offers_global_and_live_ratings(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine, Mature={"retired_at": "2026-10-24T22:00:00Z"})
    html = operator_client.get(f"{URL}new/").content.decode()
    options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', html)
    scopes = [(v, label) for v, label in options if v == "global" or v.startswith("rating:")]
    assert scopes == [
        ("global", "Global"),
        ("rating:00000000000000000000RAT001", "Rating: Everyone"),
        (f"rating:{TEEN}", "Rating: Teen"),
        ("rating:00000000000000000000RAT004", "Rating: Explicit"),
    ]
    rule_types = re.findall(r'<option value="(wordlist|regex|entity_list|model_assisted)"', html)
    assert rule_types == ["wordlist", "regex", "model_assisted"]
    assert 'value="image_output"' in html
    assert "image_output (used from M8)" in html
    assert 'value="text_input" id="id_applies_to_0" checked' in html


def test_create_posts_the_policy_fields_and_redirects(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=mutation())
    response = operator_client.post(
        f"{URL}new/",
        wordlist_post(words="  ferry \n\nFerries\nferry\n", ratings=hidden(html, "ratings")),
    )
    assert response.status_code == 302
    assert response["Location"] == f"{URL}no_ferries/"
    (post,) = engine.requests  # the signed ratings: no second read
    assert post.method == "POST"
    assert post.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert sent(post) == {
        "scope": "global",
        "name": "no ferries",
        "applies_to": ["text_output"],
        "rule": {"type": "wordlist", "words": ["ferry", "Ferries"]},
        "age_condition": None,
        "message": "Ferries are off-limits",
        "effect": "deny",
    }
    assert flashes(response) == [
        "Created no ferries — version 1, config revision 7, idempotency key "
        "01M5R0KEY00000000000000001"
    ]


def test_create_a_rating_model_assisted_policy_with_an_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=mutation())
    operator_client.post(
        f"{URL}new/",
        wordlist_post(
            id="teen_check",
            scope=f"rating:{TEEN}",
            rule_type="model_assisted",
            prompt="Is anyone hurt?",
            on_uncertain="pass",
            age_condition="minor_involved_or_present",
            applies_to=["text_output", "text_input"],
            ratings=hidden(html, "ratings"),
        ),
    )
    (post,) = engine.requests
    assert sent(post) == {
        "id": "teen_check",
        "scope": "rating",
        "scope_id": TEEN,
        "name": "no ferries",
        "applies_to": ["text_input", "text_output"],
        "rule": {"type": "model_assisted", "prompt": "Is anyone hurt?", "on_uncertain": "pass"},
        "age_condition": "minor_involved_or_present",
        "message": "Ferries are off-limits",
        "effect": "deny",
    }


def test_rating_scope_requires_a_rating_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    response = operator_client.post(
        f"{URL}new/", wordlist_post(scope="rating", ratings=hidden(html, "ratings"))
    )
    assert response.status_code == 200
    assert "A rating-scoped policy needs a rating: choose one." in response.content.decode()
    assert engine.requests == []


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"words": " \n "}, "Enter at least one word or phrase."),
        ({"rule_type": "regex", "patterns": "ok\n(unclosed"}, "Pattern 2 does not compile"),
        ({"rule_type": "model_assisted", "prompt": ""}, "Enter the prompt"),
        ({"applies_to": []}, "Choose at least one target."),
        ({"name": "x" * 81}, "Ensure this value has at most 80 characters"),
        ({"rule_type": "entity_list"}, "Select a valid choice."),
    ],
)
def test_refused_input_makes_zero_calls(
    operator_client: Client, engine: FakeEngine, changes: dict[str, Any], message: str
) -> None:
    html = new_page(operator_client, engine)
    response = operator_client.post(
        f"{URL}new/", wordlist_post(**changes, ratings=hidden(html, "ratings"))
    )
    assert message in response.content.decode()
    assert engine.requests == []


def test_failed_ratings_read_disables_rating_scope(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", RATINGS, 503, json=error("database_unavailable"))
    html = operator_client.get(f"{URL}new/").content.decode()
    assert "rating scope is unavailable" in html
    assert 'value="rating:' not in html
    engine.reset()
    response = operator_client.post(
        f"{URL}new/", wordlist_post(scope=f"rating:{TEEN}", ratings=hidden(html, "ratings"))
    )
    assert "rating scope is unavailable" in response.content.decode()
    assert engine.requests == []


def test_create_422_places_rule_errors_on_their_inputs(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    engine.respond(
        "POST",
        LIST,
        422,
        json=error(
            "validation_error",
            "too long",
            field="body.rule.wordlist.words[0]",
            errors=[
                {"field": "body.rule.wordlist.words[0]", "message": "word too long"},
                {"field": "body.name", "message": "another live policy has this name"},
            ],
        ),
    )
    response = operator_client.post(f"{URL}new/", wordlist_post(ratings=hidden(html, "ratings")))
    page = response.content.decode()
    words = field_html(page, "words")
    assert "word too long" in words
    name = field_html(page, "name")
    assert "another live policy has this name" in name
    assert "The Engine rejected the policy: too long" in page
    assert 'value="no ferries"' in page  # input kept
    assert hidden(page, "idempotency_key") != IDEMPOTENCY_KEY  # a fresh key per render


def test_create_other_errors_show_the_panel_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    engine.respond(
        "POST", LIST, 409, json=error("validation_error", "policy id x is taken", reason="id_taken")
    )
    page = operator_client.post(
        f"{URL}new/", wordlist_post(id="x", ratings=hidden(html, "ratings"))
    ).content.decode()
    assert "Engine error 409 validation_error" in page
    assert 'value="no ferries"' in page


# --- Edit ---------------------------------------------------------------------------------------


def edit_page(client: Client, engine: FakeEngine, policy_id: str, **changes: Any) -> str:
    engine.respond("GET", ITEM, 200, json=policy(policy_id, **changes))
    answer_ratings(engine)
    html = client.get(f"{URL}{policy_id}/edit/").content.decode()
    engine.reset()
    return html


def edit_post(html: str, **changes: Any) -> dict[str, Any]:
    """The Teen wordlist policy's edit form as rendered, with ``changes``."""
    data: dict[str, Any] = {
        "snapshot": hidden(html, "snapshot"),
        "ratings": hidden(html, "ratings"),
        "idempotency_key": IDEMPOTENCY_KEY,
        "name": "No gore words",
        "applies_to": ["text_output"],
        "rule_type": "wordlist",
        "words": "gore\ndisembowel",
        "patterns": "",
        "prompt": "",
        "on_uncertain": "violation",
        "age_condition": "",
        "message": "Keep it gore-free.",
    }
    return {**data, **changes}


def test_edit_form_shows_content_and_fixed_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    assert 'value="No gore words"' in html
    assert "gore\ndisembowel</textarea>" in html
    assert re.search(r'<option value="wordlist" selected>', html)
    assert "<dt>Scope</dt><dd>rating: Teen</dd>" in html
    assert "<dt>Kind</dt><dd>deterministic</dd>" in html
    # Kind is fixed after create: a deterministic policy offers deterministic rules only.
    rule_types = re.findall(r'<option value="(wordlist|regex|entity_list|model_assisted)"', html)
    assert rule_types == ["wordlist", "regex"]
    assert 'name="scope"' not in html
    assert 'name="id"' not in html


def test_edit_puts_the_whole_policy(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    engine.respond("PUT", ITEM, 200, json=mutation(TEEN_POLICY, version=3))
    response = operator_client.post(
        f"{URL}{TEEN_POLICY}/edit/",
        edit_post(
            html, rule_type="regex", patterns="gor+e", age_condition="minor_involved_or_present"
        ),
    )
    assert response.status_code == 302
    assert response["Location"] == f"{URL}{TEEN_POLICY}/"
    (put,) = engine.requests  # snapshot and ratings carried: no reads
    assert put.method == "PUT"
    assert put.url.path == f"/admin/policies/{TEEN_POLICY}"
    assert put.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert sent(put) == {
        "name": "No gore words",
        "applies_to": ["text_output"],
        "rule": {"type": "regex", "patterns": ["gor+e"]},
        "age_condition": "minor_involved_or_present",
        "message": "Keep it gore-free.",
        "effect": "deny",
    }
    assert flashes(response) == [
        f"Saved No gore words — version 3, config revision 8, idempotency key {IDEMPOTENCY_KEY}"
    ]


def test_edit_refuses_a_rule_of_another_kind_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    response = operator_client.post(
        f"{URL}{TEEN_POLICY}/edit/", edit_post(html, rule_type="model_assisted", prompt="p")
    )
    assert "Select a valid choice." in response.content.decode()
    assert engine.requests == []


def test_editing_a_retired_policy_shows_the_engines_409(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    engine.respond(
        "PUT",
        ITEM,
        409,
        json=error("validation_error", f"policy {TEEN_POLICY} is retired", reason="retired"),
    )
    response = operator_client.post(f"{URL}{TEEN_POLICY}/edit/", edit_post(html, name="Renamed"))
    page = response.content.decode()
    assert '<ul class="errorlist nonfield"><li>Retired policies cannot be changed</li></ul>' in page
    assert 'value="Renamed"' in page  # input kept
    assert "Engine error 409" not in page


def test_other_409s_show_the_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    engine.respond(
        "PUT", ITEM, 409, json=error("idempotency_conflict", "key reused for another request")
    )
    page = operator_client.post(f"{URL}{TEEN_POLICY}/edit/", edit_post(html)).content.decode()
    assert "Engine error 409 idempotency_conflict" in page
    assert "Retired policies cannot be changed" not in page


def test_edit_422_places_errors(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    engine.respond(
        "PUT",
        ITEM,
        422,
        json=error(
            "validation_error",
            "Input should be 'text_input', 'text_output', 'image_prompt' or 'image_output'",
            field="body.applies_to[0]",
        ),
    )
    page = operator_client.post(f"{URL}{TEEN_POLICY}/edit/", edit_post(html)).content.decode()
    applies = field_html(page, "applies_to")
    assert "Input should be" in applies


def test_editing_the_minors_policy_shows_the_warning_and_keeps_model_assisted(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, MINORS)
    assert MINORS_WARNING in html
    rule_types = re.findall(r'<option value="(wordlist|regex|entity_list|model_assisted)"', html)
    assert rule_types == ["model_assisted"]
    assert re.search(r'<option value="minor_involved_or_present" selected>', html)


def test_an_entity_list_rule_is_kept_read_only(operator_client: Client, engine: FakeEngine) -> None:
    rule = {"type": "entity_list", "names": ["Ada", "Bram"]}
    html = edit_page(operator_client, engine, RETIRED, rule=rule, retired_at=None)
    assert "kept as is, not editable here" in html
    assert "&quot;Bram&quot;" in html
    engine.respond("PUT", ITEM, 200, json=mutation(RETIRED, rule=rule))
    operator_client.post(
        f"{URL}{RETIRED}/edit/",
        edit_post(html, rule_type="entity_list", words="", name="No real addresses"),
    )
    (put,) = engine.requests
    assert sent(put)["rule"] == rule


def test_tampered_snapshot_is_read_again(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, TEEN_POLICY)
    engine.respond("GET", ITEM, 200, json=policy(TEEN_POLICY))
    engine.respond("PUT", ITEM, 200, json=mutation(TEEN_POLICY))
    operator_client.post(f"{URL}{TEEN_POLICY}/edit/", edit_post(html, snapshot="forged"))
    assert [r.method for r in engine.requests] == ["GET", "PUT"]


# --- Retire -------------------------------------------------------------------------------------


def test_retire_confirm_get_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(
        f"{URL}{TEEN_POLICY}/retire/", {"name": "No gore words", "next": URL}
    )
    page = response.content.decode()
    assert "<em>No gore words</em> stops applying to every story that uses it." in page
    assert f"<code>{TEEN_POLICY}</code>" in page
    assert MINORS_WARNING not in page
    assert engine.requests == []


def test_retire_post_deletes_and_returns_to_the_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    # Complement of the zero-call GET: the Confirm POST is what retires.
    engine.respond(
        "DELETE", ITEM, 200, json=mutation(TEEN_POLICY, retired_at="2026-10-24T23:00:00Z")
    )
    response = operator_client.post(
        f"{URL}{TEEN_POLICY}/retire/",
        {"name": "No gore words", "next": URL, "idempotency_key": IDEMPOTENCY_KEY},
    )
    assert response.status_code == 302
    assert response["Location"] == URL
    (delete,) = engine.requests
    assert delete.method == "DELETE"
    assert delete.url.path == f"/admin/policies/{TEEN_POLICY}"
    assert delete.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert flashes(response) == [
        f"Retired No gore words — config revision 8, idempotency key {IDEMPOTENCY_KEY}"
    ]


def test_retiring_an_already_retired_policy_says_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", ITEM, 204)
    response = operator_client.post(
        f"{URL}{RETIRED}/retire/", {"name": "No real addresses", "idempotency_key": IDEMPOTENCY_KEY}
    )
    assert flashes(response) == ["No real addresses was already retired — nothing changed"]


def test_retiring_the_minors_policy_warns_on_the_confirm_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = operator_client.get(f"{URL}{MINORS}/retire/").content.decode()
    assert MINORS_WARNING in page
    assert "Retire (minors protection)</button>" in page
    assert engine.requests == []


# --- Recording (#44) ----------------------------------------------------------------------------


def test_every_write_is_recorded(operator_client: Client, engine: FakeEngine) -> None:
    html = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=mutation())
    operator_client.post(f"{URL}new/", wordlist_post(ratings=hidden(html, "ratings")))
    html = edit_page(operator_client, engine, TEEN_POLICY)
    engine.respond("PUT", ITEM, 200, json=mutation(TEEN_POLICY))
    operator_client.post(f"{URL}{TEEN_POLICY}/edit/", edit_post(html))
    engine.respond("DELETE", ITEM, 200, json=mutation(TEEN_POLICY))
    operator_client.post(f"{URL}{TEEN_POLICY}/retire/", {"idempotency_key": IDEMPOTENCY_KEY})
    rows = list(
        EngineOperation.objects.order_by("pk").values_list("kind", "http_method", "path", "outcome")
    )
    assert rows == [
        ("create_policy", "POST", "/admin/policies", "ok"),
        ("update_policy", "PUT", f"/admin/policies/{TEEN_POLICY}", "ok"),
        ("retire_policy", "DELETE", f"/admin/policies/{TEEN_POLICY}", "ok"),
    ]


# --- Navigation ---------------------------------------------------------------------------------


def test_policies_are_reachable_from_the_guardrails_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    ratings = operator_client.get("/guardrails/ratings/").content.decode()
    assert '<a href="/guardrails/policies/">Guardrail policies</a>' in ratings
    answer_list(engine)
    html = operator_client.get(URL).content.decode()
    assert '<a href="/guardrails/ratings/" class="active">Guardrails</a>' in html
