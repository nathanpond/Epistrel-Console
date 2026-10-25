"""See and change owners and relations on every owned entity (#79)."""

import copy
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings

from console.engine.client import OPERATIONS, OWNERSHIP_ID_PARAMS, EngineClient
from console.engine_access import engine_client_for
from console.entities import REGISTRY, EntityType
from console.forms.ownership import RELATION_ERROR, RelationForm, clean_relation
from console.models import EngineOperation, KnownPrincipal
from console.views import ownership
from console.views.catalog_lifecycle import follows_label
from console.views.ownership import ownership_context
from tests.fakes.engine import FakeEngine, recorded_schema
from tests.fakes.ownership import answer_relations, relations_path
from tests.fixtures import engine_fixture
from tests.ratings.helpers import error, hidden, text

pytestmark = pytest.mark.django_db

IDS: dict[EntityType, str] = {
    "story": "01JB2Z8Y5Q3W6K9M1N4P7R0T2V",
    "character": "01JC00000000000000000CHAR1",
    "scenario": "01JC000000000000000000SCN1",
    "persona": "01JP0000000000000000PERSA1",
    "media": "01JC000000000000000000MED1",
    "engine_profile": "00000000000000000000PRF001",
}
TYPES = list(REGISTRY)
SCENARIO = IDS["scenario"]
STORY = IDS["story"]
CHARACTER = IDS["character"]
KEY = "01JK0000000000000000000KEY"
SCHEMA_PATHS = recorded_schema()["paths"]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the ownership views talk to, through the real recording client."""
    fake = FakeEngine()
    answer_relations(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(ownership, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


# --- helpers --------------------------------------------------------------------------------------


def template(entity_type: EntityType, suffix: str) -> str:
    """The schema path template of one of the type's ownership routes."""
    root = REGISTRY[entity_type].engine_root
    return f"{root}/{{{OWNERSHIP_ID_PARAMS[entity_type]}}}{suffix}"


def entity_path(entity_type: EntityType, suffix: str = "") -> str:
    return f"{REGISTRY[entity_type].engine_root}/{IDS[entity_type]}{suffix}"


def owner_body(entity_type: EntityType, *, same: bool = False) -> dict[str, Any]:
    name = "owner_story" if entity_type == "story" else "owner_entity"
    body: dict[str, Any] = copy.deepcopy(engine_fixture("owner_same" if same else name))
    if same and entity_type == "story":
        body = {**copy.deepcopy(engine_fixture("owner_story")), "owner_principal": "alice"}
    body.update(entity_type=entity_type, entity_id=IDS[entity_type])
    return body


def relation_body(entity_type: EntityType, **changes: Any) -> dict[str, Any]:
    name = "relation_story" if entity_type == "story" else "relation_created"
    body: dict[str, Any] = copy.deepcopy(engine_fixture(name))
    body.update(entity_type=entity_type, entity_id=IDS[entity_type], principal="bob")
    body["relation"] = "editor"
    return {**body, **changes}


def url(action: str, entity_type: str = "scenario", entity_id: str = SCENARIO) -> str:
    tail = {"owner": "owner/", "add": "relations/add/", "remove": "relations/remove/"}[action]
    return f"/ownership/{entity_type}/{entity_id}/{tail}"


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def body_of(request: httpx.Request) -> Any:
    return json.loads(request.content)


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method != "GET"]


def panel_request(path: str = "/catalog/scenarios/x/") -> HttpRequest:
    return RequestFactory().get(path)


def fake_client(engine: FakeEngine) -> EngineClient:
    return EngineClient("http://engine.test", None, transport=engine.transport, principal="nate")


# --- the client, for every type -------------------------------------------------------------------


@pytest.mark.parametrize("entity_type", TYPES)
def test_every_route_is_registered_against_the_schema(entity_type: EntityType) -> None:
    """Each type's four routes are in the recorded schema and registered with its entity."""
    for method, suffix in [
        ("GET", "/relations"),
        ("PUT", "/owner"),
        ("POST", "/relations"),
        ("DELETE", "/relations/{relation}/{principal}"),
    ]:
        path = template(entity_type, suffix)
        assert method.lower() in SCHEMA_PATHS[path]
        op = OPERATIONS[f"{method} {path}"]
        assert (op.entity, op.entity_id) == (entity_type, "entity_id")
        assert op.is_recorded == (method != "GET")


@pytest.mark.parametrize("entity_type", TYPES)
def test_the_panel_reads_the_types_relations(entity_type: EntityType, engine: FakeEngine) -> None:
    body = engine_fixture(f"relations_{entity_type}")
    engine.respond("GET", relations_path(entity_type), 200, json=body)
    panel = ownership_context(
        panel_request(),
        fake_client(engine),
        entity_type,
        IDS[entity_type],
        name="Harbour",
        owner="stale-owner",
    )
    (read,) = engine.requests
    assert read.url.path == entity_path(entity_type, "/relations")
    assert panel.owner == "alice"  # the relations read wins over the host page's owner
    assert panel.rows is not None
    assert [r.item.relation for r in panel.rows] == [i["relation"] for i in body["items"]]


@pytest.mark.parametrize("entity_type", TYPES)
def test_a_transfer_puts_the_owner_of_that_type(
    entity_type: EntityType, operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PUT", template(entity_type, "/owner"), 200, json=owner_body(entity_type))
    response = operator_client.post(
        url("owner", entity_type, IDS[entity_type]),
        {"principal": "bob", "current": "alice", "name": "Harbour", "idempotency_key": KEY},
    )
    assert response.status_code == 302
    (put,) = writes(engine)
    assert (put.method, put.url.path) == ("PUT", entity_path(entity_type, "/owner"))
    assert body_of(put) == {"principal": "bob"}
    assert put.headers["Idempotency-Key"] == KEY
    (row,) = EngineOperation.objects.all()
    assert (row.kind, row.entity_type, row.entity_id) == (
        "transfer_owner",
        entity_type,
        IDS[entity_type],
    )
    revision = "timeline revision 12" if entity_type == "story" else "config revision 7"
    assert flashes(response) == [
        f"Transferred Harbour to bob (was alice) — {revision}, idempotency key {KEY}."
    ]


@pytest.mark.parametrize("entity_type", TYPES)
def test_an_add_posts_the_relation_of_that_type(
    entity_type: EntityType, operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", template(entity_type, "/relations"), 201, json=relation_body(entity_type)
    )
    response = operator_client.post(
        url("add", entity_type, IDS[entity_type]),
        {"principal": "bob", "relation": "editor", "idempotency_key": KEY},
    )
    assert response.status_code == 302
    (post,) = writes(engine)
    assert (post.method, post.url.path) == ("POST", entity_path(entity_type, "/relations"))
    assert body_of(post) == {"principal": "bob", "relation": "editor"}
    (row,) = EngineOperation.objects.all()
    assert (row.kind, row.entity_type, row.entity_id) == (
        "add_relation",
        entity_type,
        IDS[entity_type],
    )


@pytest.mark.parametrize("entity_type", TYPES)
def test_a_remove_deletes_the_relation_of_that_type(
    entity_type: EntityType, operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", template(entity_type, "/relations/{relation}/{principal}"), 204)
    response = operator_client.post(
        url("remove", entity_type, IDS[entity_type]),
        {"relation": "editor", "principal": "bob", "idempotency_key": KEY},
    )
    assert response.status_code == 302
    (delete,) = writes(engine)
    assert delete.url.path == entity_path(entity_type, "/relations/editor/bob")
    (row,) = EngineOperation.objects.all()
    assert (row.kind, row.entity_type, row.entity_id) == (
        "remove_relation",
        entity_type,
        IDS[entity_type],
    )
    assert flashes(response) == [f"Removed bob as editor — idempotency key {KEY}"]


def test_an_unknown_type_is_404_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    for action in ("owner", "add", "remove"):
        response = operator_client.post(
            url(action, "principal", "x"), {"principal": "bob", "relation": "editor"}
        )
        assert response.status_code == 404
    assert engine.requests == []


# --- transfer -------------------------------------------------------------------------------------


def test_the_transfer_confirm_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    query = urlencode(
        {"principal": "  bob\t", "current": "alice", "name": "Harbour", "next": "/x/"}
    )
    html = operator_client.get(f"{url('owner')}?{query}").content.decode()
    assert "Transfer Harbour from alice to bob ?" in text(html)
    assert hidden(html, "principal") == "bob"
    assert '<input type="hidden" name="next" value="/x/">' in html
    assert engine.requests == []
    assert not KnownPrincipal.objects.exists()  # remembered only by the confirming POST


@pytest.mark.parametrize("value", ["system", "", "a\nb", "x" * 257])
def test_an_invalid_target_is_refused_by_the_field_with_zero_calls(
    value: str, operator_client: Client, engine: FakeEngine
) -> None:
    query = urlencode({"principal": value, "current": "alice", "name": "Harbour"})
    html = operator_client.get(f"{url('owner')}?{query}").content.decode()
    assert 'class="errorlist' in html
    assert "Confirm" not in html
    response = operator_client.post(url("owner"), {"principal": value, "current": "alice"})
    assert response.status_code == 200
    assert engine.requests == []


def test_the_system_refusal_names_the_rule(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"{url('owner')}?principal=system").content.decode()
    assert "`system` is reserved for the Engine." in html


def test_transferring_to_the_current_owner_says_nothing_changed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "PUT", template("character", "/owner"), 200, json=owner_body("character", same=True)
    )
    response = operator_client.post(
        url("owner", "character", CHARACTER),
        {"principal": "alice", "current": "alice", "name": "Aldric", "has_companion": "1"},
    )
    assert flashes(response) == ["Already owned by alice — nothing changed"]


def test_a_character_transfer_says_its_companion_moves(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = urlencode(
        {"principal": "bob", "current": "alice", "name": "Aldric", "has_companion": 1}
    )
    html = operator_client.get(f"{url('owner', 'character', CHARACTER)}?{query}").content.decode()
    assert "Its companion scenario moves with it." in html
    assert hidden(html, "has_companion") == "1"
    engine.respond("PUT", template("character", "/owner"), 200, json=owner_body("character"))
    response = operator_client.post(
        url("owner", "character", CHARACTER),
        {"principal": "bob", "current": "alice", "name": "Aldric", "has_companion": "1"},
    )
    (flash,) = flashes(response)
    assert flash.endswith(" Its companion scenario moved with it.")
    assert "(was alice)" in flash
    (row,) = EngineOperation.objects.all()  # the companion is not a second row
    assert row.entity_type == "character"


def test_without_a_companion_the_transfer_does_not_mention_one(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = urlencode({"principal": "bob", "current": "alice", "name": "Ari"})
    html = operator_client.get(f"{url('owner', 'persona', IDS['persona'])}?{query}").content
    assert b"companion" not in html


def test_a_transfer_remembers_the_target(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("PUT", template("scenario", "/owner"), 200, json=owner_body("scenario"))
    operator_client.post(url("owner"), {"principal": "bob", "current": "alice"})
    assert KnownPrincipal.objects.filter(principal="bob").exists()


def test_persona_name_taken_shows_a_specific_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "PUT", template("persona", "/owner"), 409, json=error("persona_name_taken", "taken")
    )
    response = operator_client.post(
        url("owner", "persona", IDS["persona"]),
        {"principal": "bob", "current": "alice", "name": "Ari", "idempotency_key": KEY},
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert "bob already has a persona with this name — rename one first." in html
    assert hidden(html, "principal") == "bob"  # Confirm again in one click
    assert hidden(html, "idempotency_key") != KEY  # under a fresh key


@pytest.mark.parametrize(
    ("headers", "notice"),
    [
        ({"Retry-After": "5"}, "A turn is running — try again in 5 s"),
        ({}, "A turn is running — try again"),
    ],
)
def test_story_busy_shows_when_to_retry(
    headers: dict[str, str], notice: str, operator_client: Client, engine: FakeEngine
) -> None:
    busy = error("story_busy", "busy", reason="in_flight", operation_id="01JO00000000000000000OPER")
    engine.respond("PUT", template("story", "/owner"), 423, json=busy, headers=headers)
    response = operator_client.post(
        url("owner", "story", STORY), {"principal": "bob", "current": "alice"}
    )
    assert response.status_code == 200
    page = text(response.content.decode())
    assert f"{notice} HTTP 423" in page
    assert "01JO00000000000000000OPER" in page
    assert not flashes(response)


def test_a_stale_companion_form_shows_the_companion_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    """422 ``companion_follows_character`` (the page was a companion's after all): #69's line,
    naming and linking the character."""
    engine.respond(
        "PUT",
        template("scenario", "/owner"),
        422,
        json=engine_fixture("error_companion_follows_character"),
    )
    card = {**copy.deepcopy(engine_fixture("get_catalog_character_card")), "id": CHARACTER}
    engine.respond("GET", "/catalog/characters/{character_id}", 200, json=card)
    response = operator_client.post(url("owner"), {"principal": "bob", "current": "alice"})
    assert response.status_code == 200
    html = response.content.decode()
    name = card["name"]
    assert (
        f'This is <a href="/catalog/characters/{CHARACTER}/">{name}</a>\'s companion — archive, '
        "delete or relabel the character instead"
    ) in html.replace("&#x27;", "'")


def test_without_a_character_id_the_companion_line_rereads_the_scenario(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "PUT", template("scenario", "/owner"), 422, json=error("companion_follows_character")
    )
    engine.respond("GET", "/catalog/scenarios/{scenario_id}", 503, json=error("unavailable"))
    html = operator_client.post(url("owner"), {"principal": "bob"}).content.decode()
    assert "This is a character's companion — archive, delete or relabel" in html


def test_an_unworded_refusal_shows_the_panel_in_place(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PUT", template("scenario", "/owner"), 404, json=error("not_found", "gone"))
    response = operator_client.post(url("owner"), {"principal": "bob", "current": "alice"})
    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="engine-error"' in html
    assert "not_found" in html


def test_an_unsafe_next_returns_to_the_entity(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("PUT", template("scenario", "/owner"), 200, json=owner_body("scenario"))
    response = operator_client.post(
        url("owner"), {"principal": "bob", "next": "https://evil.example/"}
    )
    assert response["Location"] == f"/catalog/scenarios/{SCENARIO}/#ownership"


def test_a_story_write_reopens_the_play_views_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PUT", template("story", "/owner"), 200, json=owner_body("story"))
    response = operator_client.post(
        url("owner", "story", STORY),
        {"principal": "bob", "next": f"/stories/{STORY}/?viewpoint=narrator&panel=ownership"},
    )
    assert response["Location"] == (
        f"/stories/{STORY}/?viewpoint=narrator&panel=ownership#ownership"
    )


# --- add ------------------------------------------------------------------------------------------


def test_an_added_relation_says_added(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", template("scenario", "/relations"), 201, json=relation_body("scenario"))
    response = operator_client.post(
        url("add"),
        {"principal": "bob", "relation": "editor", "idempotency_key": KEY, "next": "/x/"},
    )
    assert response["Location"] == "/x/#ownership"
    assert flashes(response) == [f"Added bob as editor — config revision 7, idempotency key {KEY}"]
    assert KnownPrincipal.objects.filter(principal="bob").exists()


def test_an_existing_relation_says_already_related(
    operator_client: Client, engine: FakeEngine
) -> None:
    existing = {**engine_fixture("relation_existing"), "entity_id": SCENARIO}
    engine.respond("POST", template("scenario", "/relations"), 200, json=existing)
    response = operator_client.post(
        url("add"), {"principal": "bob", "relation": "editor", "idempotency_key": KEY}
    )
    assert flashes(response) == [
        f"bob is already editor — nothing changed (config revision 7, idempotency key {KEY})"
    ]


@pytest.mark.parametrize("relation", ["", "edit or", "editor!", "x" * 65, "éditeur", "a\tb", "a/b"])
def test_an_invalid_relation_name_makes_zero_calls(
    relation: str, operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.post(url("add"), {"principal": "bob", "relation": relation})
    assert response.status_code == 200
    html = response.content.decode()
    assert RELATION_ERROR.replace("`", "&#x60;") in html or RELATION_ERROR in html
    assert 'value="bob"' in html  # the input is kept
    assert engine.requests == []


def test_a_relation_name_with_surrounding_spaces_is_trimmed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", template("scenario", "/relations"), 201, json=relation_body("scenario"))
    operator_client.post(url("add"), {"principal": " bob ", "relation": " \teditor  "})
    (post,) = writes(engine)
    assert body_of(post) == {"principal": "bob", "relation": "editor"}


def test_relation_names_are_case_sensitive_and_kept() -> None:
    assert clean_relation("Co_Author-2") == "Co_Author-2"
    assert clean_relation("x" * 64) == "x" * 64


def test_the_relation_input_carries_its_rule() -> None:
    html = str(RelationForm(datalist="dl-x")["relation"])
    assert 'maxlength="64"' in html
    assert "pattern=" in html
    assert 'list="dl-x"' in html


def test_the_add_page_is_gettable_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(f"{url('add')}?name=Harbour").content.decode()
    assert "Harbour" in html
    assert '<option value="co_author">' in html
    assert engine.requests == []


def test_story_busy_on_add_keeps_the_input(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        template("story", "/relations"),
        423,
        json=error("story_busy", "busy"),
        headers={"Retry-After": "3"},
    )
    response = operator_client.post(
        url("add", "story", STORY), {"principal": "bob", "relation": "player"}
    )
    html = response.content.decode()
    assert "A turn is running — try again in 3 s" in html
    assert 'value="player"' in html


# --- remove, and a principal holding "/" ----------------------------------------------------------


def test_a_slash_principal_is_quoted_on_add_and_remove(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Demo 3: relate and unrelate ``team/alice`` on a story."""
    engine.respond(
        "POST", template("story", "/relations"), 201, json=engine_fixture("relation_story")
    )
    engine.respond("DELETE", template("story", "/relations/{relation}/{principal}"), 204)
    operator_client.post(
        url("add", "story", STORY), {"principal": "team/alice", "relation": "player"}
    )
    operator_client.post(
        url("remove", "story", STORY), {"relation": "player", "principal": "team/alice"}
    )
    post, delete = writes(engine)
    assert body_of(post)["principal"] == "team/alice"
    raw = delete.url.raw_path.decode("ascii")
    assert raw == f"/stories/{STORY}/relations/player/team%2Falice"
    # Complement: the raw "/" never reaches the path unencoded.
    assert "team/alice" not in raw


def test_the_remove_confirm_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    query = urlencode({"relation": "player", "principal": "team/alice", "name": "Harbour"})
    html = operator_client.get(f"{url('remove', 'story', STORY)}?{query}").content.decode()
    assert "Remove team/alice as player on Harbour ?" in text(html)
    assert hidden(html, "principal") == "team/alice"
    assert engine.requests == []


def test_the_remove_reads_the_posted_fields_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", template("scenario", "/relations/{relation}/{principal}"), 204)
    query = urlencode({"relation": "other", "principal": "mallory"})
    operator_client.post(f"{url('remove')}?{query}", {"relation": "editor", "principal": " bob "})
    (delete,) = writes(engine)
    # Exactly as listed: no trim.
    assert delete.url.raw_path.decode("ascii").endswith(f"/relations/editor/{quote(' bob ')}")


def test_a_missing_relation_says_already_removed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "DELETE",
        template("scenario", "/relations/{relation}/{principal}"),
        404,
        json=error("not_found", "no such relation"),
    )
    response = operator_client.post(url("remove"), {"relation": "editor", "principal": "bob"})
    assert response.status_code == 302
    assert flashes(response) == ["Already removed"]


def test_a_companion_refusal_on_remove_re_renders_the_confirm(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "DELETE",
        template("scenario", "/relations/{relation}/{principal}"),
        422,
        json=engine_fixture("error_companion_follows_character"),
    )
    engine.respond("GET", "/catalog/characters/{character_id}", 503, json=error("unavailable"))
    response = operator_client.post(url("remove"), {"relation": "editor", "principal": "bob"})
    assert response.status_code == 200
    html = response.content.decode()
    assert f'href="/catalog/characters/{CHARACTER}/"' in html  # the id, still linked
    assert hidden(html, "principal") == "bob"


def test_a_remove_without_its_fields_is_a_bad_request(
    operator_client: Client, engine: FakeEngine
) -> None:
    assert operator_client.get(url("remove")).status_code == 400
    assert operator_client.post(url("remove"), {"relation": "editor"}).status_code == 400
    assert engine.requests == []


# --- the panel ------------------------------------------------------------------------------------


def render_panel(engine: FakeEngine, entity_type: EntityType = "scenario", **kwargs: Any) -> str:
    panel = ownership_context(
        panel_request(), fake_client(engine), entity_type, IDS[entity_type], **kwargs
    )
    return render_to_string("console/_ownership.html", {"ownership": panel})


def test_the_panel_shows_owner_relations_and_forms(engine: FakeEngine) -> None:
    engine.respond(
        "GET", relations_path("scenario"), 200, json=engine_fixture("relations_scenario")
    )
    html = render_panel(engine, name="Harbour", owner="alice")
    assert '<section id="ownership" class="ownership">' in html
    page = text(html)
    assert "Principal Relation Added by Added at" in page
    assert "bob editor console:nate 2026-10-20 09:00:00 UTC" in page
    assert 'class="transfer-owner" method="get"' in html
    assert 'class="add-relation" method="post"' in html
    assert 'placeholder="alice"' in html  # the input starts blank
    remove = f"/ownership/scenario/{SCENARIO}/relations/remove/?relation=editor&amp;principal=bob"
    assert remove in html


def test_the_datalist_suggests_the_defaults_and_the_entitys_names(engine: FakeEngine) -> None:
    body = engine_fixture("relations_scenario")
    body["items"].append(
        {**body["items"][0], "relation": "Editor"}  # case-sensitive: kept apart
    )
    body["items"].append({**body["items"][0], "principal": "dave"})  # "editor" again
    engine.respond("GET", relations_path("scenario"), 200, json=body)
    html = render_panel(engine, name="Harbour", owner="alice")
    options = [part.split('"', 1)[0] for part in html.split('<option value="')[1:]]
    suggestions = [o for o in options if o in {"editor", "co_author", "player", "Editor"}]
    assert suggestions == ["editor", "co_author", "player", "Editor"]


def test_no_relations_says_so(engine: FakeEngine) -> None:
    assert "No relations." in render_panel(engine, name="Harbour", owner="alice")


def test_a_failed_relations_read_degrades_only_the_panel(engine: FakeEngine) -> None:
    engine.respond("GET", relations_path("scenario"), 503, json=error("unavailable", "down"))
    html = render_panel(engine, name="Harbour", owner="from-detail")
    assert 'class="engine-error"' in html
    assert "from-detail" in html  # the detail body's owner
    assert 'class="add-relation"' in html  # the forms stay
    assert "No relations." not in html
    assert "<table" not in html


def test_a_companion_panel_is_read_only(engine: FakeEngine) -> None:
    engine.respond(
        "GET", relations_path("scenario"), 200, json=engine_fixture("relations_scenario")
    )
    html = render_panel(
        engine, name="Harbour", owner="alice", follows=follows_label(CHARACTER, "Sir Aldric")
    )
    assert f'Follows <a href="/catalog/characters/{CHARACTER}/">Sir Aldric</a>' in html
    assert "<form" not in html
    assert "Remove" not in html
    assert "bob" in html  # the owner and relations table stays


def test_a_held_story_is_named_so(engine: FakeEngine) -> None:
    panel = ownership_context(
        panel_request(), fake_client(engine), "story", STORY, name=None, owner="alice"
    )
    assert panel.name == "(held — erasure pending)"
    assert panel.collapsible


def test_the_story_panel_opens_when_asked(engine: FakeEngine) -> None:
    request = panel_request(f"/stories/{STORY}/?panel=ownership")
    panel = ownership_context(
        request, fake_client(engine), "story", STORY, name="Harbour", owner="alice"
    )
    html = render_to_string("console/_ownership.html", {"ownership": panel})
    assert '<details id="ownership" class="ownership" open>' in html
    assert "<summary>Owner &amp; relations</summary>" in html
