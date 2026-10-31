"""Model a story entity as a minor character (#199): the entity page's action line and the
``inspect:entity-model`` form, write and redirect.

Runs against the real urlconf on the schema-validating fake Engine, as ``test_scene.py`` does:
the shell reads the Structured v2 configuration and ``story_json(next_turn_index=4)`` (Mara
``T3A``, the player, and Tobin ``T3B``); the profile is ``entity_profile_omniscient.json``
(Tobin's entity ``TB1``, a person, at turn 3); the write answers m5's ``entity_modeled.json``.
"""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.held import held_text
from console.models import EngineOperation
from console.views import inspect as views
from console.views.inspect_scene import EntitySnapshot, with_article
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, lite, structured_v2
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
MARA, TOBIN = P + "T3A", P + "T3B"
TOBIN_E = P + "TB1"
PROFILE = "/stories/{story_id}/entities/{entity_id}/profile"
MODEL = "/stories/{story_id}/entities/{entity_id}/model"
MODEL_PATH = f"/stories/{STORY}/entities/{TOBIN_E}/model"
ENTITY_PAGE = f"/stories/{STORY}/inspect/entities/{TOBIN_E}/"
MODEL_PAGE = f"/stories/{STORY}/inspect/entities/{TOBIN_E}/model/"
M5_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "engine" / "m5"
KEY = "01JM5KY0000000000000000099"
CAST_URL = f"/stories/{STORY}/"
SETTINGS_URL = f"/stories/{STORY}/settings/"
LINK = "Model as a minor character…"
PAUSED = "Paused — the Engine will refuse until the check finishes"
NO_ARRIVALS = "This story allows no new arrivals — the Engine will refuse; turn it on in"


def m5(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((M5_FIXTURES / f"{name}.json").read_text())
    return loaded


def profile_body(**entity: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(
        json.loads((FIXTURES / "entity_profile_omniscient.json").read_text())
    )
    body["entity"].update(entity)
    return body


def modeled(**character: Any) -> dict[str, Any]:
    """``entity_modeled.json`` as the write's answer for Tobin's entity."""
    body = copy.deepcopy(m5("entity_modeled"))
    body["character"].update({"id": TOBIN_E, "name": "Tobin", **character})
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v2())
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=4))
    fake.respond("GET", PROFILE, 200, json=profile_body())
    fake.respond("POST", MODEL, 201, json=modeled())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def paths(engine: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in engine.requests]


def profile_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path.endswith("/profile")]


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def shell_and_profile() -> list[tuple[str, str]]:
    return [
        ("GET", f"/stories/{STORY}/effective-config"),
        ("GET", f"/stories/{STORY}"),
        ("GET", f"/stories/{STORY}/entities/{TOBIN_E}/profile"),
    ]


def action_line(html: str) -> str:
    found = re.search(r'<p class="entity-action">(.*?)</p>', html, re.S)
    return found.group(1) if found else ""


def snapshot(name: str = "Tobin", kind: str = "person", entity_id: str = TOBIN_E) -> str:
    return EntitySnapshot(STORY, entity_id, name, kind).signed()


def post(client: Client, data: dict[str, str] | None = None, query: str = "", **extra: str) -> Any:
    posted = {"idempotency_key": KEY, "snapshot": snapshot(), "true_age": "40", **(data or {})}
    return client.post(f"{MODEL_PAGE}{query}", {**posted, **extra})


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def sent_body(request: httpx.Request) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(request.content)
    return loaded


def key_field(html: str) -> str:
    found = re.search(r'name="idempotency_key" value="([^"]*)"', html)
    assert found is not None
    return found.group(1)


def refusal(html: str) -> str:
    found = re.search(r'<p class="errorlist model-refusal" role="alert">(.*?)</p>', html, re.S)
    assert found is not None, html
    return found.group(1)


# --- the entity page's action line ----------------------------------------------------------------


def test_person_entity_offers_model_link(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{ENTITY_PAGE}?as_of=2&viewpoint=omniscient")
    line = action_line(html)
    # The profile's resolved turn (3) and viewpoint ride on the link.
    assert f'<a class="model-entity" href="{MODEL_PAGE}?as_of=3&amp;viewpoint=omniscient">' in line
    assert LINK in line
    assert "model-note" not in line
    assert html.index("context-line") < html.index("entity-action") < html.index("scene-form")
    assert "Character:" not in html


def test_cast_entity_shows_character_line_not_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = story_json(next_turn_index=4)
    story["characters"][1]["id"] = TOBIN_E  # Tobin's entity is a cast member
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = page(operator_client, ENTITY_PAGE)
    line = action_line(html)
    assert (
        line == f'Character: <a class="cast-character" href="{CAST_URL}#cast-{TOBIN_E}">Tobin</a>'
    )
    assert LINK not in html
    assert "Minor" not in line  # C4: no role or weight wording
    assert "Reactive" not in line


def test_a_modeled_minor_is_listed_in_the_recorded_cast(
    operator_client: Client, engine: FakeEngine
) -> None:
    """A modeled minor is a story character, so ``get_story`` lists it: the recorded M5 story
    with ``entity_modeled.json``'s character in its cast shows the "Character:" line."""
    warden = m5("entity_modeled")["character"]
    story = m5("story")
    story["characters"].append(warden)
    engine.respond("GET", STORY_PATH, 200, json=story)
    engine.respond(
        "GET", PROFILE, 200, json=profile_body(entity_id=warden["id"], name="the warden")
    )
    html = page(operator_client, f"/stories/{STORY}/inspect/entities/{warden['id']}/")
    assert f'#cast-{warden["id"]}">Dock warden</a>' in action_line(html)
    assert LINK not in html


@pytest.mark.parametrize(
    ("kind", "offered"),
    [("place", False), ("item", False), ("organization", False), ("other", True), ("person", True)],
)
def test_place_entity_offers_nothing(
    operator_client: Client, engine: FakeEngine, kind: str, offered: bool
) -> None:
    engine.respond("GET", PROFILE, 200, json=profile_body(kind=kind))
    html = page(operator_client, ENTITY_PAGE)
    assert (LINK in html) is offered
    assert "Character:" not in html
    if not offered:
        assert action_line(html) == ""


def test_an_archived_story_or_a_failed_story_read_offers_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=4, status="archived"))
    assert action_line(page(operator_client, ENTITY_PAGE)) == ""
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, ENTITY_PAGE)
    assert action_line(html) == ""
    assert "Character:" not in html


def test_paused_note_beside_link(operator_client: Client, engine: FakeEngine) -> None:
    # Paused and arrivals off: only the pause shows.
    story = story_json(next_turn_index=4, status="paused", settings={"allow_new_arrivals": False})
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = page(operator_client, ENTITY_PAGE)
    line = action_line(html)
    assert LINK in line
    assert f'<small class="muted model-note">{PAUSED}</small>' in line
    assert "new arrivals" not in line
    assert paths(engine) == shell_and_profile()


def test_no_new_arrivals_note_beside_link(operator_client: Client, engine: FakeEngine) -> None:
    story = story_json(next_turn_index=4, settings={"allow_new_arrivals": False})
    engine.respond("GET", STORY_PATH, 200, json=story)
    line = action_line(page(operator_client, ENTITY_PAGE))
    assert LINK in line
    assert (
        f'<small class="muted model-note">{NO_ARRIVALS} <a href="{SETTINGS_URL}">Settings</a>'
        "</small>"
    ) in line
    assert paths(engine) == shell_and_profile()


@pytest.mark.parametrize("arrivals", [True, None], ids=["on", "missing"])
def test_no_note_on_an_ordinary_story(
    operator_client: Client, engine: FakeEngine, arrivals: bool | None
) -> None:
    story = story_json(next_turn_index=4)
    if arrivals is None:
        story["settings"].pop("allow_new_arrivals")
    engine.respond("GET", STORY_PATH, 200, json=story)
    line = action_line(page(operator_client, ENTITY_PAGE))
    assert LINK in line
    assert "model-note" not in line


# --- the form page --------------------------------------------------------------------------------


def test_the_form_page_names_the_entity_from_an_omniscient_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"{MODEL_PAGE}?as_of=3&viewpoint=character:{MARA}")
    (read,) = profile_reads(engine)
    assert dict(read.url.params) == {"viewpoint": "omniscient"}  # at head
    assert "<title>Model Tobin as a minor character</title>" in html
    assert '<h2 class="entity-model-header">Model Tobin as a minor character</h2>' in html
    assert f'Person · <code title="{TOBIN_E}">…4P7R0TB1</code>' in html
    back = f"{ENTITY_PAGE}?as_of=3&amp;viewpoint=character%3A{MARA}"
    assert f'href="{back}">← Entity</a>' in html
    assert f'action="{MODEL_PAGE}?as_of=3&amp;viewpoint=character%3A{MARA}"' in html
    assert "scene-form" not in html
    true_age = re.search(r'<input type="number" name="true_age"[^>]*>', html)
    assert true_age is not None
    assert "min=" not in true_age.group(0)
    assert "max=" not in true_age.group(0)
    assert '<textarea name="definition" cols="40" rows="4"' in html
    form = html.split('class="entity-model-form"')[1]
    assert "maxlength" not in form
    assert "Blank: the entity&#x27;s name" in html
    assert "Model as minor character</button>" in html
    assert "<fieldset disabled>" not in html


def test_invalid_query_values_are_dropped(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"{MODEL_PAGE}?as_of=99&viewpoint=character:{P}ZZZ")
    assert f'href="{ENTITY_PAGE}">← Entity</a>' in html
    assert f'action="{MODEL_PAGE}"' in html
    response = post(operator_client, query="?as_of=-1&viewpoint=omniscient")
    assert response["Location"] == f"{ENTITY_PAGE}?viewpoint=omniscient"


def test_an_unnamed_entity_shows_its_short_id(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROFILE, 200, json=profile_body(name=""))
    html = page(operator_client, MODEL_PAGE)
    assert "<title>Model …4P7R0TB1 as a minor character</title>" in html


def test_the_form_page_notes_a_refusal_ahead(operator_client: Client, engine: FakeEngine) -> None:
    story = story_json(next_turn_index=4, settings={"allow_new_arrivals": False})
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = page(operator_client, MODEL_PAGE)
    assert NO_ARRIVALS in html
    assert html.index("model-note") < html.index("entity-model-form")


def test_an_archived_story_disables_the_form_but_a_forced_post_still_sends(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=4, status="archived"))
    html = page(operator_client, MODEL_PAGE)
    assert "This story is archived — unarchive it to model its entities." in html
    assert "<fieldset disabled>" in html
    engine.respond(
        "POST", MODEL, 409, json=error_json("story_archived", "archived", story_id=STORY)
    )
    response = post(operator_client)
    assert len(writes(engine)) == 1
    assert "This story is archived — unarchive it to model its entities." in refusal(
        response.content.decode()
    )


def test_lite_story_model_page_is_greyed_with_no_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    html = page(operator_client, MODEL_PAGE)
    assert '<div class="panel unavailable">' in html
    assert "entity-model-form" not in html
    post(operator_client)
    assert profile_reads(engine) == []
    assert writes(engine) == []


def test_profile_404_replaces_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PROFILE, 404, json=error_json("not_found", "no entity"))
    html = page(operator_client, MODEL_PAGE)
    assert "That entity isn&#x27;t one of this story&#x27;s" in html
    assert "entity-model-form" not in html


def test_story_404_is_the_story_not_found_before_the_profile(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 404, json=error_json("not_found", "no story"))
    html = page(operator_client, MODEL_PAGE)
    assert "Not found (it may have been deleted or truncated)" in html
    assert profile_reads(engine) == []


def test_a_failed_profile_read_keeps_a_form_headed_by_the_short_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE, 503, json=error_json("ledger_unavailable", "down"))
    html = page(operator_client, MODEL_PAGE)
    assert "Engine error 503 ledger_unavailable" in html
    assert "<title>Model …4P7R0TB1 as a minor character</title>" in html
    assert html.index("Engine error 503") < html.index("entity-model-form")


def test_a_failed_story_read_leaves_the_form_without_notes(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, MODEL_PAGE)
    assert "entity-model-form" in html
    assert "model-note" not in html


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    held = error_json("erasure_pending", "held")
    engine.respond("GET", PROFILE, 503, json=held, headers={"Retry-After": "30"})
    response = operator_client.get(MODEL_PAGE)
    assert response.status_code == 503
    assert held_text("30") in response.content.decode()
    engine.respond("POST", MODEL, 503, json=held, headers={"Retry-After": "30"})
    response = post(operator_client)
    assert response.status_code == 503
    assert held_text("30") in response.content.decode()
    assert "entity-model-form" not in response.content.decode()


# --- the form's checks ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("true_age", "error"),
    [
        ("151", "True age is 0 to 150."),
        ("-1", "True age is 0 to 150."),
        ("", "Enter a true age."),
        ("forty", "Enter a whole number."),
        ("4.5", "Enter a whole number."),
    ],
)
def test_invalid_form_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, true_age: str, error: str
) -> None:
    response = post(operator_client, {"true_age": true_age})
    html = response.content.decode()
    assert response.status_code == 200
    assert error in html
    assert writes(engine) == []
    assert key_field(html) == KEY  # the posted key is kept


@pytest.mark.parametrize("age", ["0", "150"])
def test_the_range_ends_are_valid(operator_client: Client, engine: FakeEngine, age: str) -> None:
    post(operator_client, {"true_age": age})
    (write,) = writes(engine)
    assert sent_body(write) == {"true_age": int(age)}


def test_a_long_definition_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, {"definition": "x" * 4001}).content.decode()
    assert "At most 4,000 characters (4,001 given)." in html
    assert writes(engine) == []
    post(operator_client, {"definition": "  " + "y" * 3998 + "\r\nz  "})  # CRLF → LF: 4,000
    (write,) = writes(engine)
    assert sent_body(write) == {"true_age": 40, "definition": "y" * 3998 + "\nz"}


@pytest.mark.parametrize("definition", ["", "   \r\n  "], ids=["blank", "whitespace"])
def test_blank_definition_is_omitted(
    operator_client: Client, engine: FakeEngine, definition: str
) -> None:
    post(operator_client, {"definition": definition})
    (write,) = writes(engine)
    assert sent_body(write) == {"true_age": 40}


def test_unnamed_entity_requires_a_definition(operator_client: Client, engine: FakeEngine) -> None:
    unnamed = snapshot(name="")
    html = post(operator_client, {"snapshot": unnamed}).content.decode()
    assert "Enter a definition — this entity has no name." in html
    assert writes(engine) == []
    post(operator_client, {"snapshot": unnamed, "definition": "A watchman"})
    (write,) = writes(engine)
    assert sent_body(write) == {"true_age": 40, "definition": "A watchman"}


@pytest.mark.parametrize("bad", ["", "tampered", "other-entity"], ids=["missing", "bad", "other"])
def test_a_bad_snapshot_names_the_short_id_and_still_sends(
    operator_client: Client, engine: FakeEngine, bad: str
) -> None:
    value = snapshot(entity_id=P + "ZZ1") if bad == "other-entity" else bad
    engine.respond("POST", MODEL, 423, json=error_json("story_busy", "busy", reason="in_flight"))
    html = post(operator_client, {"snapshot": value}).content.decode()
    assert len(writes(engine)) == 1
    assert "<title>Model …4P7R0TB1 as a minor character</title>" in html


def test_post_makes_no_profile_read(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client)
    assert profile_reads(engine) == []
    assert paths(engine) == [
        ("GET", f"/stories/{STORY}/effective-config"),
        ("GET", f"/stories/{STORY}"),
        ("POST", MODEL_PATH),
    ]


# --- success --------------------------------------------------------------------------------------


def test_success_redirects_with_flash_linking_the_cast_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post(operator_client, {"definition": "A watchman\r\nat the gate"}, "?as_of=3")
    assert response.status_code == 302
    assert response["Location"] == f"{ENTITY_PAGE}?as_of=3"
    assert flashes(response) == [
        (
            "success",
            "Modeled Tobin as a minor character — timeline revision 9. "
            f'<a href="{CAST_URL}#cast-{TOBIN_E}">See it in the cast</a>',
        )
    ]
    (write,) = writes(engine)
    assert write.headers["Idempotency-Key"] == KEY
    row = EngineOperation.objects.get()
    assert row.kind == "model_entity"
    assert row.entity_type == "story"
    assert row.entity_id == STORY
    assert row.request["body"] == {"true_age": 40, "definition": "A watchman\nat the gate"}


def test_an_unnamed_character_is_flashed_by_its_short_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", MODEL, 201, json=modeled(name=""))
    response = post(operator_client)
    ((_, text),) = flashes(response)
    assert text.startswith("Modeled …4P7R0TB1 as a minor character — timeline revision 9.")


def test_the_flash_is_marked_safe_and_escapes_the_name(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", MODEL, 201, json=modeled(name="<b>Tobin</b>"))
    response = post(operator_client)
    (message,) = get_messages(response.wsgi_request)
    assert "safe" in message.extra_tags.split()
    assert str(message).startswith("Modeled &lt;b&gt;Tobin&lt;/b&gt; as")


def test_bad_response_flashes_a_warning(operator_client: Client, engine: FakeEngine) -> None:
    def unparseable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"unexpected": True})

    engine.side_effect("POST", MODEL, unparseable)
    response = post(operator_client, query="?as_of=3")
    assert response.status_code == 302
    assert response["Location"] == f"{ENTITY_PAGE}?as_of=3"
    assert flashes(response) == [
        ("warning", "Modeled — the Engine's answer couldn't be read; see the cast")
    ]


# --- refusals -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        (
            {"reason": "already_modeled", "character_id": TOBIN_E},
            f'Already a character — <a href="{CAST_URL}#cast-{TOBIN_E}">see it in the cast</a>',
        ),
        (
            {"reason": "not_a_person", "kind": "place"},
            "Only a person (or an entity of kind other) can be modeled — this is a place",
        ),
        (
            {"reason": "not_a_person", "kind": "organization"},
            "Only a person (or an entity of kind other) can be modeled — this is an organization",
        ),
        (
            {"reason": "allow_new_arrivals"},
            "This story allows no new arrivals — turn on Allow new arrivals in "
            f'<a href="{SETTINGS_URL}">Settings</a> first',
        ),
    ],
    ids=["already_modeled", "not_a_person_place", "not_a_person_organization", "no_arrivals"],
)
def test_already_modeled_not_a_person_and_no_arrivals_refusals(
    operator_client: Client, engine: FakeEngine, details: dict[str, Any], expected: str
) -> None:
    engine.respond("POST", MODEL, 422, json=error_json("validation_error", "refused", **details))
    response = post(operator_client, {"definition": "kept text"})
    html = response.content.decode()
    assert response.status_code == 200
    assert refusal(html) == expected
    assert "kept text</textarea>" in html
    assert 'value="40"' in html
    assert key_field(html) != KEY  # an Engine 4xx mints a fresh key


def test_already_modeled_without_character_id_links_the_cast(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", MODEL, 422, json=error_json("validation_error", "x", reason="already_modeled")
    )
    html = post(operator_client).content.decode()
    assert refusal(html) == f'Already a character — <a href="{CAST_URL}">see it in the cast</a>'


def test_the_kind_with_its_article() -> None:
    assert with_article("place") == "a place"
    assert with_article("organization") == "an organization"
    assert with_article("living_thing") == "a living thing"


@pytest.mark.parametrize("field", ["true_age", "definition"])
def test_true_age_422_lands_beside_the_field(
    operator_client: Client, engine: FakeEngine, field: str
) -> None:
    engine.respond(
        "POST",
        MODEL,
        422,
        json=error_json("validation_error", "out of bounds", field=f"body.{field}"),
    )
    html = post(operator_client).content.decode()
    found = re.search(rf'<div class="field field-{field}">(.*?)</div>', html, re.S)
    assert found is not None
    assert "out of bounds" in found.group(1)
    assert "model-refusal" not in html


def test_another_422_lands_above_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", MODEL, 422, json=error_json("validation_error", "something else"))
    html = post(operator_client).content.decode()
    assert '<div class="errors" role="alert"><p class="errorlist">something else</p></div>' in html


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (
            423,
            error_json("story_busy", "busy", reason="in_flight"),
            "Story busy — another turn is running",
        ),
        (
            409,
            error_json("story_archived", "archived", story_id=STORY),
            "This story is archived — unarchive it to model its entities.",
        ),
        (
            409,
            error_json(
                "story_paused",
                "paused",
                story_id=STORY,
                job_id=P + "JB1",
                cause="rating_check",
                progress={"done": 1, "total": 4},
            ),
            "The story is paused for a check",
        ),
        (
            409,
            error_json("erased_subject_referenced", "erased"),
            "This text refers to an erased subject — nothing was saved",
        ),
    ],
    ids=["busy", "archived", "paused", "erased"],
)
def test_story_busy_archived_and_paused_messages(
    operator_client: Client, engine: FakeEngine, status: int, error: dict[str, Any], expected: str
) -> None:
    engine.respond("POST", MODEL, status, json=error)
    html = post(operator_client).content.decode()
    assert expected in refusal(html)
    assert "entity-model-form" in html
    assert key_field(html) != KEY


def test_the_paused_refusal_keeps_its_link(operator_client: Client, engine: FakeEngine) -> None:
    error = error_json(
        "story_paused",
        "paused",
        story_id=STORY,
        job_id=P + "JB1",
        cause="rating_check",
        progress={"done": 1, "total": 4},
    )
    engine.respond("POST", MODEL, 409, json=error)
    assert "See the check</a>" in refusal(post(operator_client).content.decode())


def test_module_off_is_a_form_error_with_the_config_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", MODEL, 409, json=error_json("module_off", "off", module="character_knowledge")
    )
    html = post(operator_client).content.decode()
    line = refusal(html)
    assert f'<a href="/stories/{STORY}/config/">Config</a>' in line
    assert "Engine error" not in html


def test_entity_404_drops_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", MODEL, 404, json=error_json("not_found", "no entity"))
    html = post(operator_client).content.decode()
    assert "That entity isn&#x27;t one of this story&#x27;s" in html
    assert "entity-model-form" not in html
    assert '<h2 class="entity-model-header">Model Tobin as a minor character</h2>' in html
    assert "← Entity</a>" in html


@pytest.mark.parametrize("status", [503, 401], ids=["unavailable", "unauthorized"])
def test_503_keeps_the_idempotency_key(
    operator_client: Client, engine: FakeEngine, status: int
) -> None:
    engine.respond("POST", MODEL, status, json=error_json("database_unavailable", "down"))
    html = post(operator_client).content.decode()
    assert f"Engine error {status}" in html
    assert key_field(html) == KEY
    assert html.index(f"Engine error {status}") < html.index("entity-model-form")


def test_an_unreachable_engine_keeps_the_idempotency_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("POST", MODEL, httpx.ConnectError("refused"))
    html = post(operator_client).content.decode()
    assert key_field(html) == KEY
    assert "entity-model-form" in html
