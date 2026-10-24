"""/guardrails/ratings/ (#49): create, edit and retire ratings; #47's second write."""

import re
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.engine.models import ContentRatingSummary
from console.forms.ratings import CREATE_POLICY_NOTE, unchanged
from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.ratings.helpers import (
    EVERYONE,
    EXPLICIT,
    EXPLICIT_POLICY,
    IDEMPOTENCY_KEY,
    ITEM,
    LIST,
    MATURE,
    POLICIES,
    TEEN,
    TEEN_POLICY,
    URL,
    answer_policies,
    answer_ratings,
    calls,
    detail,
    error,
    hidden,
    mutation,
    rating,
    rating_policy,
    sent,
    text,
)

pytestmark = pytest.mark.django_db


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def options(html: str, name: str) -> list[tuple[str, str]]:
    """A checkbox group's ``(value, label)`` pairs."""
    found = re.findall(
        rf'<label for="id_{name}_\d+"><input type="checkbox" name="{name}" value="([^"]*)"'
        rf"[^>]*>\s*([^<]*)</label>",
        html,
    )
    return [(value, label.strip()) for value, label in found]


def field_block(page: str, name: str) -> str:
    """A form field's markup, up to the next field (checkbox groups nest ``<div>``s)."""
    match = re.search(
        rf'<div class="field field-{name}">(.*?)(?=<div class="field |</form>)', page, re.S
    )
    assert match, f"no field {name}"
    return match.group(1)


def checked(html: str, name: str) -> list[str]:
    return re.findall(rf'name="{name}" value="([^"]*)"[^>]*checked', html)


# --- New ----------------------------------------------------------------------------------------


def new_page(client: Client, engine: FakeEngine) -> str:
    answer_ratings(engine)
    html = client.get(f"{URL}new/").content.decode()
    engine.reset()
    return html


def new_post(html: str, **changes: Any) -> dict[str, Any]:
    return {
        "name": "Gritty",
        "description": "Grim, but not explicit.",
        "display_order": "",
        "prompt_guidance": "Keep it grim.",
        "image_guidance": "",
        "inherits_from": [MATURE],
        "idempotency_key": IDEMPOTENCY_KEY,
        "options": hidden(html, "options"),
        **changes,
    }


def test_new_form_offers_live_parents_and_no_policy_picker(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine, Teen={"retired_at": "2026-10-24T22:00:00Z"})
    html = operator_client.get(f"{URL}new/").content.decode()
    assert options(html, "inherits_from") == [
        (EVERYONE, "Everyone"),
        (MATURE, "Mature"),
        (EXPLICIT, "Explicit"),
    ]
    assert 'name="policy_ids"' not in html
    assert CREATE_POLICY_NOTE in html
    assert "Save the rating, then add rating policies under Guardrails → Policies" in html
    assert "Guidance is not inherited" in html
    assert (calls(engine, "GET", LIST)[0].url.params["include_retired"]) == "1"


def test_create_body_omits_a_blank_display_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=mutation())
    response = operator_client.post(
        f"{URL}new/", new_post(html, prompt_guidance="  Keep it grim.\r\nNo gore.  ")
    )
    (post,) = engine.requests
    assert sent(post) == {
        "name": "Gritty",
        "description": "Grim, but not explicit.",
        "prompt_guidance": "Keep it grim.\nNo gore.",
        "image_guidance": "",
        "inherits_from": [MATURE],
    }
    assert post.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert response.status_code == 302
    assert response["Location"] == f"{URL}01M5R0RAT00000000000000005/"
    assert flashes(response) == [
        f"Created Gritty — version 1, config revision 12, idempotency key {IDEMPOTENCY_KEY}"
    ]


def test_create_with_display_order_and_no_parents(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=mutation())
    operator_client.post(f"{URL}new/", new_post(html, display_order="15", inherits_from=[]))
    (post,) = engine.requests
    assert sent(post)["display_order"] == 15
    assert sent(post)["inherits_from"] == []


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"name": "  "}, "name"),
        ({"name": "x" * 81}, "name"),
        ({"display_order": str(2**31)}, "display_order"),
        ({"prompt_guidance": "x" * 8001}, "prompt_guidance"),
        ({"inherits_from": ["01FORGED000000000000000000"]}, "inherits_from"),
    ],
)
def test_refused_input_makes_zero_engine_calls(
    operator_client: Client, engine: FakeEngine, changes: dict[str, Any], field: str
) -> None:
    html = new_page(operator_client, engine)
    response = operator_client.post(f"{URL}new/", new_post(html, **changes))
    assert engine.requests == []
    assert response.status_code == 200
    page = response.content.decode()
    assert "errorlist" in field_block(page, field)
    # A fresh key per render (#43).
    assert hidden(page, "idempotency_key") != IDEMPOTENCY_KEY


def test_create_422_is_placed_on_the_field(operator_client: Client, engine: FakeEngine) -> None:
    html = new_page(operator_client, engine)
    engine.respond(
        "POST",
        LIST,
        422,
        json=error(
            "validation_error",
            "inherits_from names ratings that do not exist",
            field="body.inherits_from",
        ),
    )
    page = operator_client.post(f"{URL}new/", new_post(html)).content.decode()
    assert "inherits_from names ratings that do not exist" in field_block(page, "inherits_from")
    assert "The Engine rejected the rating" in page


# --- Edit ---------------------------------------------------------------------------------------


def edit_page(client: Client, engine: FakeEngine, name: str = "Explicit", **changes: Any) -> str:
    body = detail(name, **changes)
    engine.respond("GET", ITEM, 200, json=body)
    answer_ratings(engine)
    answer_policies(engine)
    html = client.get(f"{URL}{body['id']}/edit/").content.decode()
    engine.reset()
    return html


def edit_post(html: str, **changes: Any) -> dict[str, Any]:
    explicit = rating("Explicit", policy_ids=[EXPLICIT_POLICY])
    return {
        "name": explicit["name"],
        "description": explicit["description"],
        "display_order": str(explicit["display_order"]),
        "prompt_guidance": explicit["prompt_guidance"],
        "image_guidance": explicit["image_guidance"],
        "policy_ids": explicit["policy_ids"],
        "inherits_from": explicit["inherits_from"],
        "idempotency_key": IDEMPOTENCY_KEY,
        "options": hidden(html, "options"),
        **changes,
    }


def test_edit_picker_offers_only_this_ratings_live_policies(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY, "old"]))
    answer_ratings(engine)
    answer_policies(
        engine,
        rating_policy("old", EXPLICIT, "Old rule", retired_at="2026-10-24T22:00:00Z"),
        rating_policy("retired_unattached", EXPLICIT, "Gone", retired_at="2026-10-24T22:00:00Z"),
        rating_policy("01M5R0EXPL0000000000000002", EXPLICIT, "Another"),
    )
    html = operator_client.get(f"{URL}{EXPLICIT}/edit/").content.decode()
    assert options(html, "policy_ids") == [
        ("01M5R0EXPL0000000000000002", "Another (01M5R0EXPL0000000000000002)"),
        (EXPLICIT_POLICY, f"No dragons ({EXPLICIT_POLICY})"),
        ("old", "Old rule (retired) (old)"),
    ]
    assert TEEN_POLICY not in html  # Teen's policy is not offered
    assert sorted(checked(html, "policy_ids")) == sorted([EXPLICIT_POLICY, "old"])
    (policies,) = calls(engine, "GET", POLICIES)
    assert dict(policies.url.params) == {"scope": "rating", "include_retired": "1"}
    # Inherits from: every live rating but itself.
    assert [value for value, _ in options(html, "inherits_from")] == [EVERYONE, TEEN, MATURE]
    assert 'value="40"' in html  # display order pre-filled
    assert "Version 1. Saving writes a new version." in html


def test_edit_puts_every_field_as_a_new_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, policy_ids=[EXPLICIT_POLICY])
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    engine.respond("PUT", ITEM, 200, json=mutation("Explicit", version=2))
    response = operator_client.post(
        f"{URL}{EXPLICIT}/edit/",
        edit_post(html, prompt_guidance="Adults only.", inherits_from=[], policy_ids=[]),
    )
    get, put = engine.requests
    assert get.method == "GET"  # the baseline, re-read
    assert sent(put) == {
        "name": "Explicit",
        "description": rating("Explicit")["description"],
        "display_order": 40,
        "prompt_guidance": "Adults only.",
        "image_guidance": rating("Explicit")["image_guidance"],
        "policy_ids": [],
        "inherits_from": [],
    }
    assert put.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert response["Location"] == f"{URL}{EXPLICIT}/"
    assert flashes(response) == [
        f"Saved Explicit — version 2, config revision 9, idempotency key {IDEMPOTENCY_KEY}"
    ]


def test_no_change_edit_sends_no_put(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, policy_ids=[EXPLICIT_POLICY])
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    guidance = rating("Explicit")["prompt_guidance"]
    response = operator_client.post(
        f"{URL}{EXPLICIT}/edit/",
        edit_post(html, prompt_guidance=f"  {guidance}\r\n", display_order="040"),
    )
    assert [r.method for r in engine.requests] == ["GET"]
    assert response["Location"] == f"{URL}{EXPLICIT}/"
    assert flashes(response) == ["No changes — no new version"]


def test_a_real_change_is_not_mistaken_for_none(
    operator_client: Client, engine: FakeEngine
) -> None:
    # Complement of the no-change test: one field differs, so the PUT goes out.
    html = edit_page(operator_client, engine, policy_ids=[EXPLICIT_POLICY])
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    engine.respond("PUT", ITEM, 200, json=mutation("Explicit", version=2))
    operator_client.post(f"{URL}{EXPLICIT}/edit/", edit_post(html, display_order="41"))
    assert [r.method for r in engine.requests] == ["GET", "PUT"]


def test_unchanged_compares_sets_text_and_order() -> None:
    explicit = ContentRatingSummary.from_json(rating("Explicit", policy_ids=["a", "b"]))
    assert explicit is not None
    body = {
        "name": " Explicit ",
        "description": explicit.description + "\r\n",
        "display_order": 40,
        "prompt_guidance": explicit.prompt_guidance.replace("\n", "\r\n"),
        "image_guidance": explicit.image_guidance,
        "policy_ids": ["b", "a"],
        "inherits_from": [],
    }
    assert unchanged(body, explicit)
    assert not unchanged({**body, "policy_ids": ["a"]}, explicit)
    assert not unchanged({**body, "display_order": 41}, explicit)
    assert not unchanged({**body, "image_guidance": "x"}, explicit)


def test_inheritance_cycle_message(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    engine.respond(
        "PUT",
        ITEM,
        422,
        json=error(
            "inheritance_cycle",
            "the rating would inherit from itself",
            field="body.inherits_from",
            cycle=[EXPLICIT, EVERYONE, TEEN, MATURE, EXPLICIT],
        ),
    )
    response = operator_client.post(
        f"{URL}{EXPLICIT}/edit/", edit_post(html, inherits_from=[EVERYONE])
    )
    page = response.content.decode()
    assert "That would make Explicit inherit from itself" in field_block(page, "inherits_from")
    assert checked(page, "inherits_from") == [EVERYONE]  # input kept


def test_put_409_is_the_retired_refusal(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=detail("Explicit"))
    engine.respond("PUT", ITEM, 409, json=error("validation_error", reason="retired"))
    page = operator_client.post(
        f"{URL}{EXPLICIT}/edit/", edit_post(html, description="new")
    ).content.decode()
    assert "Retired ratings cannot be changed" in page
    assert "new</textarea>" in page


def test_other_put_errors_show_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=detail("Explicit"))
    engine.respond("PUT", ITEM, 503, json=error("unavailable", "database down"))
    page = operator_client.post(
        f"{URL}{EXPLICIT}/edit/", edit_post(html, description="new")
    ).content.decode()
    assert "Engine error 503 unavailable" in page
    assert 'class="rating-form"' in page


def test_edit_degrades_when_policies_cannot_be_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    answer_ratings(engine)
    engine.respond("GET", POLICIES, 503, json=error("unavailable"))
    html = operator_client.get(f"{URL}{EXPLICIT}/edit/").content.decode()
    assert 'name="policy_ids"' not in html
    assert "the rating&#x27;s policies are kept as they are" in html
    engine.reset()
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    engine.respond("PUT", ITEM, 200, json=mutation("Explicit", version=2))
    operator_client.post(f"{URL}{EXPLICIT}/edit/", edit_post(html, description="new"))
    _, put = engine.requests
    assert sent(put)["policy_ids"] == [EXPLICIT_POLICY]


def test_tampered_options_are_read_again(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=detail("Explicit"))
    answer_ratings(engine)
    answer_policies(engine)
    engine.respond("PUT", ITEM, 200, json=mutation("Explicit", version=2))
    operator_client.post(
        f"{URL}{EXPLICIT}/edit/", edit_post(html, options="forged", description="new")
    )
    assert [r.method for r in engine.requests][-1] == "PUT"
    assert calls(engine, "GET", LIST)


# --- Retire -------------------------------------------------------------------------------------


def test_retire_confirm_get_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"{URL}{TEEN}/retire/?name=Teen&next={URL}")
    assert engine.requests == []
    page = text(response.content.decode())
    assert "Stories using Teen keep their version; new stories cannot choose it." in page
    assert TEEN in page


def test_retire_deletes_and_returns_to_the_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", ITEM, 200, json=mutation("Mature", retired_at="2026-10-24T22:00:00Z"))
    response = operator_client.post(
        f"{URL}{MATURE}/retire/", {"idempotency_key": IDEMPOTENCY_KEY, "name": "Mature"}
    )
    (delete,) = engine.requests
    assert (delete.method, delete.url.path) == ("DELETE", f"/admin/content-ratings/{MATURE}")
    assert delete.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert response["Location"] == URL
    assert flashes(response) == [
        f"Retired Mature — config revision 9, idempotency key {IDEMPOTENCY_KEY}"
    ]


def test_retire_already_retired(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ITEM, 204)
    response = operator_client.post(f"{URL}{MATURE}/retire/", {"name": "Mature"})
    assert flashes(response) == ["Mature was already retired — nothing changed"]


def test_retiring_the_default_says_so(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "DELETE",
        ITEM,
        409,
        json=error("validation_error", "is the deployment default", reason="is_default"),
    )
    response = operator_client.post(f"{URL}{TEEN}/retire/", {"name": "Teen"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "This is the deployment default — choose another default first" in html
    assert '<a href="/engine/settings/">deployment settings</a>' in html


def test_other_retire_409_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ITEM, 409, json=error("idempotency_conflict", "key reused"))
    html = operator_client.post(f"{URL}{TEEN}/retire/", {"name": "Teen"}).content.decode()
    assert "Engine error 409 idempotency_conflict" in html
    assert "deployment default" not in html


# --- Recording (#44) ----------------------------------------------------------------------------


def test_writes_are_recorded(operator_client: Client, engine: FakeEngine) -> None:
    html = new_page(operator_client, engine)
    engine.respond("POST", LIST, 201, json=mutation())
    operator_client.post(f"{URL}new/", new_post(html))
    edit = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=detail("Explicit"))
    engine.respond("PUT", ITEM, 200, json=mutation("Explicit", version=2))
    operator_client.post(f"{URL}{EXPLICIT}/edit/", edit_post(edit, description="new"))
    engine.respond("DELETE", ITEM, 409, json=error("validation_error", reason="is_default"))
    operator_client.post(f"{URL}{EXPLICIT}/retire/", {"name": "Explicit"})
    rows = list(
        EngineOperation.objects.order_by("pk").values_list("kind", "http_method", "path", "outcome")
    )
    assert rows == [
        ("create_content_rating", "POST", "/admin/content-ratings", "ok"),
        ("update_content_rating", "PUT", f"/admin/content-ratings/{EXPLICIT}", "ok"),
        ("retire_content_rating", "DELETE", f"/admin/content-ratings/{EXPLICIT}", "error"),
    ]


# --- #47's second write -------------------------------------------------------------------------

POLICY_URL = "/guardrails/policies/new/"
NEW_POLICY = "01M5R0EXPL0000000000000009"


def policy_post(client: Client, engine: FakeEngine) -> dict[str, Any]:
    answer_ratings(engine)
    html = client.get(POLICY_URL).content.decode()
    engine.reset()
    return {
        "id": "",
        "scope": f"rating:{EXPLICIT}",
        "name": "No wyverns",
        "applies_to": ["text_output"],
        "rule_type": "wordlist",
        "words": "wyvern",
        "on_uncertain": "violation",
        "age_condition": "",
        "message": "No wyverns",
        "idempotency_key": IDEMPOTENCY_KEY,
        "ratings": hidden(html, "ratings"),
    }


def created_policy() -> dict[str, Any]:
    body = rating_policy(NEW_POLICY, EXPLICIT, "No wyverns")
    return {**body, "config_revision": 13, "idempotency_key": IDEMPOTENCY_KEY}


def test_rating_policy_is_added_to_its_rating(operator_client: Client, engine: FakeEngine) -> None:
    form = policy_post(operator_client, engine)
    engine.respond("POST", POLICIES, 201, json=created_policy())
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[EXPLICIT_POLICY]))
    engine.respond(
        "PUT", ITEM, 200, json=mutation("Explicit", version=3, policy_ids=[EXPLICIT_POLICY])
    )
    response = operator_client.post(POLICY_URL, form)
    post, get, put = engine.requests
    assert (post.method, get.method, put.method) == ("POST", "GET", "PUT")
    assert put.url.path == f"/admin/content-ratings/{EXPLICIT}"
    assert sent(put)["policy_ids"] == [EXPLICIT_POLICY, NEW_POLICY]
    assert sent(put)["name"] == "Explicit"
    assert put.headers["Idempotency-Key"] == f"{IDEMPOTENCY_KEY}.rating"
    assert response["Location"] == f"/guardrails/policies/{NEW_POLICY}/"
    assert flashes(response)[1].startswith("Added No wyverns to Explicit's policies — version 3")
    rows = list(EngineOperation.objects.order_by("pk").values_list("kind", "outcome"))
    assert rows == [("create_policy", "ok"), ("update_content_rating", "ok")]


def test_rating_policy_already_on_the_rating_is_not_written_again(
    operator_client: Client, engine: FakeEngine
) -> None:
    form = policy_post(operator_client, engine)
    engine.respond("POST", POLICIES, 201, json=created_policy())
    engine.respond("GET", ITEM, 200, json=detail("Explicit", policy_ids=[NEW_POLICY]))
    response = operator_client.post(POLICY_URL, form)
    assert [r.method for r in engine.requests] == ["POST", "GET"]
    assert response["Location"] == f"/guardrails/policies/{NEW_POLICY}/"


def test_failed_second_write_keeps_the_policy_and_says_so(
    operator_client: Client, engine: FakeEngine
) -> None:
    form = policy_post(operator_client, engine)
    engine.respond("POST", POLICIES, 201, json=created_policy())
    engine.respond("GET", ITEM, 200, json=detail("Explicit"))
    engine.respond("PUT", ITEM, 409, json=error("validation_error", reason="retired"))
    response = operator_client.post(POLICY_URL, form)
    assert response.status_code == 200
    html = response.content.decode()
    assert "No wyverns was not added to Explicit&#x27;s policies — Engine error 409" in html
    assert "was created" in html
    assert f'href="/guardrails/policies/{NEW_POLICY}/"' in html
    assert f'href="/guardrails/ratings/{EXPLICIT}/edit/"' in html
    rows = list(EngineOperation.objects.order_by("pk").values_list("kind", "outcome"))
    assert rows == [("create_policy", "ok"), ("update_content_rating", "error")]


def test_global_policy_has_no_second_write(operator_client: Client, engine: FakeEngine) -> None:
    form = policy_post(operator_client, engine)
    body = {**created_policy(), "scope": "global", "scope_id": None}
    engine.respond("POST", POLICIES, 201, json=body)
    operator_client.post(POLICY_URL, {**form, "scope": "global"})
    assert [r.method for r in engine.requests] == ["POST"]
