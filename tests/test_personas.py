"""List, create, edit and delete personas (#67)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import ActingPrincipal, EngineOperation, KnownPrincipal
from console.views import personas
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import calls, error, hidden, text

pytestmark = pytest.mark.django_db

LIST = "/personas"
ITEM = "/personas/{persona_id}"
PID = "01JP0000000000000000PERSA1"
PID2 = "01JP0000000000000000PERSA2"
URL = "/personas/"
NEW = "/personas/new/"
DETAIL = f"/personas/{PID}/"
EDIT = f"/personas/{PID}/edit/"
DELETE = f"/personas/{PID}/delete/"
KEY = "01JP00000000000000000KEY01"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the persona views talk to, through the real recording client."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(personas, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def persona(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_persona"))
    return {**body, **changes}


def written(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("create_persona"))
    return {**body, **changes}


def listing(*items: dict[str, Any], next_cursor: str | None = None) -> dict[str, Any]:
    return {"items": list(items), "next_cursor": next_cursor}


def body_of(request: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def list_params(engine: FakeEngine) -> list[tuple[str, str]]:
    (listed,) = calls(engine, "GET", LIST)
    return list(listed.url.params.multi_items())


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> str:
    engine.respond("GET", ITEM, 200, json=body or persona())
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response.content.decode()


def edit_data(html: str, **changes: str) -> dict[str, str]:
    """What the edit page posts unchanged (its fields as rendered), with ``changes``."""
    data = {
        "snapshot": hidden(html, "snapshot"),
        "idempotency_key": hidden(html, "idempotency_key"),
        "name": "Ari",
        "gender": "",
        "age": "30",
        "description": "Quick-witted and restless.\r\nTall, with ink-stained fingers.",
    }
    return {**data, **changes}


def new_data(**changes: str) -> dict[str, str]:
    data = {
        "idempotency_key": KEY,
        "name": " Ari ",
        "gender": "",
        "age": "30",
        "description": "Quick-witted.\r\nTall.",
        "owner": " alice\t",
    }
    return {**data, **changes}


# --- list -----------------------------------------------------------------------------------------


def test_list_shows_every_principals_personas(operator_client: Client, engine: FakeEngine) -> None:
    other = persona(id=PID2, name="Bo", gender="man", age=41, owner_principal="bob")
    engine.respond("GET", LIST, 200, json=listing(persona(), other))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert list_params(engine) == [("limit", "50")]  # no owner: every principal's
    assert f'href="/personas/{PID}/"' in html
    row = html[html.index(f'id="persona-{PID}"') : html.index(f'id="persona-{PID2}"')]
    assert "Ari" in row
    assert "<td>—</td>" in row
    assert "<td>30</td>" in row
    assert "<code>alice</code>" in row or ">alice<" in row
    assert 'href="/personas/?owner=alice"' in row
    assert "2026-10-20 09:00:00 UTC" in row
    assert "<td>man</td>" in html
    assert 'href="/personas/new/?next=%2Fpersonas%2F"' in html


def test_list_filters_send_owner_and_one_relation(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 200, json=listing(persona()))
    response = operator_client.get(
        URL, {"owner": " bob ", "relation": "player", "relation_principal": "carol"}
    )
    assert list_params(engine) == [
        ("limit", "50"),
        ("owner", "bob"),
        ("relation", "player:carol"),
    ]
    html = response.content.decode()
    # The owner filter link keeps the relation; New persona carries the owner.
    assert 'href="/personas/?owner=alice&amp;relation=player&amp;relation_principal=carol"' in html
    assert 'href="/personas/new/?owner=bob&amp;next=' in html
    assert KnownPrincipal.objects.count() == 0  # GET filters are never remembered


def test_relation_name_alone_matches_any_principal(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 200, json=listing())
    response = operator_client.get(URL, {"relation": "player"})
    assert list_params(engine) == [("limit", "50"), ("relation", "player:")]
    assert "No personas match these filters." in response.content.decode()


def test_empty_list_without_filters(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL).content.decode()
    assert "No personas." in html
    assert "match these filters" not in html


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"relation_principal": "carol"}, "Enter a relation name"),
        ({"relation": "bad name"}, "A relation name is 1 to 64"),
        ({"owner": "a\nb"}, "Principal contains a control character."),
        ({"owner": "x" * 257}, "Principal is longer than 256 characters."),
    ],
)
def test_invalid_filters_make_no_call(
    operator_client: Client, engine: FakeEngine, params: dict[str, str], message: str
) -> None:
    response = operator_client.get(URL, params)
    assert response.status_code == 200
    assert message in response.content.decode()
    assert engine.requests == []


def test_system_is_a_valid_owner_filter(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json=listing())
    operator_client.get(URL, {"owner": "system"})
    assert list_params(engine) == [("limit", "50"), ("owner", "system")]


def test_paging_keeps_the_filters(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json=listing(persona(), next_cursor="c2"))
    html = operator_client.get(URL, {"owner": "alice"}).content.decode()
    assert 'href="?owner=alice&amp;cursor=c2"' in html
    engine.reset()
    engine.respond("GET", LIST, 200, json=listing(persona()))
    html = operator_client.get(URL, {"owner": "alice", "cursor": "c2"}).content.decode()
    assert list_params(engine) == [("limit", "50"), ("owner", "alice"), ("cursor", "c2")]
    assert 'href="?owner=alice">First page' in html


def test_a_422_on_the_list_shows_the_panel_below_the_kept_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LIST, 422, json=error("cursor_invalid", "bad cursor"))
    html = operator_client.get(URL, {"owner": "alice", "cursor": "zz"}).content.decode()
    assert "cursor_invalid" in html
    assert html.index('name="owner"') < html.index("cursor_invalid")
    assert 'value="alice"' in html


# --- detail ---------------------------------------------------------------------------------------


def test_detail_shows_every_field_and_the_copy_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=persona(gender="woman"))
    html = operator_client.get(DETAIL).content.decode()
    page = text(html)
    assert f"<code>{PID}</code>" in html
    assert "Stories that used this persona keep their copy." in page
    assert "Gender woman" in page
    assert "Age 30" in page
    assert "<pre" in html
    assert "white-space: pre-wrap" in html
    assert "Quick-witted and restless.\nTall, with ink-stained fingers." in html
    assert "2026-10-20 09:00:00 UTC" in html
    assert "2026-10-21 10:30:00 UTC" in html
    assert f'href="/personas/{PID}/edit/"' in html
    assert (
        f'href="/personas/{PID}/delete/?name=Ari&amp;owner=alice&amp;next=%2Fpersonas%2F"' in html
    )


def test_detail_404_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 404, json=error("not_found", "no such persona"))
    html = operator_client.get(DETAIL).content.decode()
    assert "not_found" in html


# --- create ---------------------------------------------------------------------------------------


def test_new_page_prefills_the_acting_principal(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(NEW).content.decode()
    assert re.search(r'name="owner" value="console:nate"', html)
    assert 'maxlength="4000"' in html
    assert 'rows="8"' in html
    order = [html.index(f'name="{n}"') for n in ("name", "gender", "age", "description", "owner")]
    assert order == sorted(order)
    assert re.search(r'<input type="number" name="age"[^>]*>', html)
    assert 'href="/personas/">Cancel' in html
    assert engine.requests == []


def test_new_page_takes_the_owner_and_next_from_the_query(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(NEW, {"owner": "bob", "next": "/personas/?owner=bob"}).content
    assert b'name="owner" value="bob"' in html
    assert b'href="/personas/?owner=bob">Cancel' in html
    off_site = operator_client.get(NEW, {"next": "https://evil.example/"}).content
    assert b'href="/personas/">Cancel' in off_site


def test_create_sends_the_owner_and_lands_on_the_detail(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", LIST, 201, json=written())
    response = operator_client.post(NEW, new_data())
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (sent,) = calls(engine, "POST", LIST)
    assert body_of(sent) == {
        "name": "Ari",
        "age": 30,
        "description": "Quick-witted.\nTall.",
        "owner": "alice",
    }  # a blank gender is left out
    assert sent.headers["Idempotency-Key"] == KEY
    assert KnownPrincipal.objects.filter(principal="alice").exists()
    flashed = flashes(response)
    assert flashed == [f"Created persona Ari ({PID}) — key {KEY}"]
    row = EngineOperation.objects.get(kind="create_persona")
    assert (row.entity_type, row.entity_id, row.principal) == ("persona", PID, "console:nate")


def test_create_sends_a_given_gender(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", LIST, 201, json=written(gender="nonbinary"))
    operator_client.post(NEW, new_data(gender=" nonbinary "))
    (sent,) = calls(engine, "POST", LIST)
    assert body_of(sent)["gender"] == "nonbinary"


def test_default_owner_is_the_acting_principal_not_the_operator(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    """Complement: acting as alice, the pre-filled owner is alice, and so is what is sent."""
    ActingPrincipal.objects.create(user=operator, principal="alice")
    html = operator_client.get(NEW).content.decode()
    owner = re.search(r'name="owner" value="([^"]*)"', html)
    assert owner is not None
    assert owner.group(1) == "alice"
    engine.respond("POST", LIST, 201, json=written())
    operator_client.post(NEW, new_data(owner=owner.group(1)))
    (sent,) = calls(engine, "POST", LIST)
    assert body_of(sent)["owner"] == "alice"
    assert "console:nate" not in json.dumps(body_of(sent))
    assert sent.headers["X-Epistrel-Principal"] == "alice"


def test_name_taken_on_create_shows_beside_name(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", LIST, 409, json=error("persona_name_taken", "taken"))
    response = operator_client.post(NEW, new_data(name="ari"))
    html = response.content.decode()
    assert response.status_code == 200
    field = html[html.index('id="f-name"') : html.index('id="f-gender"')]
    assert "already has a persona with this name" in field
    assert "alice" in field  # the submitted, trimmed owner
    assert 'value="ari"' in field  # the input is kept


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        ({"name": "x" * 121}, "name", "at most 120 characters"),
        ({"name": "   "}, "name", "This field is required."),
        ({"gender": "g" * 41}, "gender", "at most 40 characters"),
        ({"age": ""}, "age", "Enter an age."),
        ({"age": "151"}, "age", "less than or equal to 150"),
        ({"age": "-1"}, "age", "greater than or equal to 0"),
        ({"description": " \r\n "}, "description", "This field is required."),
        ({"description": "d" * 4001}, "description", "at most 4000 characters"),
        ({"owner": "system"}, "owner", "reserved for the Engine"),
        ({"owner": ""}, "owner", "Principal can't be blank."),
    ],
)
def test_bounds_are_enforced_before_any_call(
    operator_client: Client,
    engine: FakeEngine,
    changes: dict[str, str],
    field: str,
    message: str,
) -> None:
    response = operator_client.post(NEW, new_data(**changes))
    html = unescape(response.content.decode())
    assert response.status_code == 200
    start = html.index(f'id="f-{field}"')
    assert message in html[start : html.index("</p>", start)]
    assert engine.requests == []


def test_crlf_counts_as_one_character_against_the_limit(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", LIST, 201, json=written())
    description = "a" * 3998 + "\r\n" + "b"  # 4001 posted, 4000 once normalised
    operator_client.post(NEW, new_data(description=description))
    (sent,) = calls(engine, "POST", LIST)
    assert len(body_of(sent)["description"]) == 4000


def test_a_422_on_create_is_placed_at_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    failure = error(
        "validation_error",
        "invalid",
        errors=[{"field": "body.name", "message": "bad name"}],
    )
    engine.respond("POST", LIST, 422, json=failure)
    html = operator_client.post(NEW, new_data()).content.decode()
    start = html.index('id="f-name"')
    assert "bad name" in html[start : html.index('id="f-gender"')]
    assert 'class="engine-error"' not in html  # placed, so no panel


# --- edit -----------------------------------------------------------------------------------------


def test_edit_form_has_no_owner_input(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    assert 'name="owner"' not in html
    assert "Change the owner from the Owners panel" in html
    assert "<title>Edit Ari" in html
    assert f'href="/personas/{PID}/">Cancel' in html


def test_edit_sends_only_changed_fields(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, persona(gender="man"))
    engine.respond("PATCH", ITEM, 200, json=written(description="Calmer now."))
    data = edit_data(html, gender="", description="Calmer now.", owner="mallory")
    response = operator_client.post(EDIT, data)
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    (sent,) = calls(engine, "PATCH", ITEM.format(persona_id=PID))
    assert body_of(sent) == {"gender": None, "description": "Calmer now."}
    assert [r.method for r in engine.requests] == ["PATCH"]
    flashed = flashes(response)
    assert flashed == [f"Saved persona Ari — key {KEY}"]
    row = EngineOperation.objects.get(kind="patch_persona")
    assert (row.entity_type, row.entity_id) == ("persona", PID)


def test_edit_with_nothing_changed_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine)
    response = operator_client.post(EDIT, edit_data(html, name=" Ari "))
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    assert engine.requests == []
    flashed = flashes(response)
    assert flashed == ["Nothing changed"]


def test_edit_bounds_make_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    response = operator_client.post(EDIT, edit_data(html, age="200"))
    assert "less than or equal to 150" in response.content.decode()
    assert engine.requests == []


def test_name_taken_on_edit_names_the_personas_owner(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 409, json=error("persona_name_taken", "taken"))
    response = operator_client.post(EDIT, edit_data(html, name="Bo"))
    page = response.content.decode()
    field = page[page.index('id="f-name"') : page.index('id="f-gender"')]
    assert "already has a persona with this name" in field
    assert "alice" in field
    assert [r.method for r in engine.requests] == ["PATCH"]  # the owner came from the snapshot


def test_a_tampered_snapshot_writes_nothing(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine)
    engine.respond("GET", ITEM, 200, json=persona())
    response = operator_client.post(EDIT, edit_data(html, snapshot="forged", name="Bo"))
    page = response.content.decode()
    assert "This form expired — your changes were not saved; check and submit again" in page
    assert 'value="Bo"' in page  # the input is kept
    assert [r.method for r in engine.requests] == ["GET"]


# --- delete ---------------------------------------------------------------------------------------


def test_delete_confirm_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(DELETE, {"name": "Ari", "owner": "alice", "next": "/personas/"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "Stories that used it keep their copy" in html
    assert "<em>Ari</em>" in html
    assert "<code>alice</code>" in html
    assert f'href="/personas/{PID}/">Cancel' in html
    assert engine.requests == []


def test_delete_confirm_without_display_data_shows_the_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = operator_client.get(DELETE).content.decode()
    assert f"<code>{PID}</code>" in html
    assert engine.requests == []


def test_delete_deletes_and_returns_to_the_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", ITEM, 204, headers={"Idempotent-Replay": "true"})
    page = operator_client.get(
        DELETE, {"name": "Ari", "owner": "alice", "next": "/personas/?owner=alice"}
    ).content.decode()
    data = {
        "idempotency_key": hidden(page, "idempotency_key"),
        "name": "Ari",
        "owner": "alice",
        "back": hidden(page, "back"),
    }
    response = operator_client.post(DELETE, data)
    assert response.status_code == 302
    assert response["Location"] == "/personas/?owner=alice"
    (sent,) = calls(engine, "DELETE", ITEM.format(persona_id=PID))
    assert sent.headers["Idempotency-Key"] == data["idempotency_key"]
    flashed = flashes(response)
    assert flashed == [f"Deleted persona Ari ({PID}) — key {data['idempotency_key']}"]
    row = EngineOperation.objects.get(kind="delete_persona")
    assert (row.entity_type, row.entity_id) == ("persona", PID)


def test_delete_never_redirects_off_site(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ITEM, 204)
    response = operator_client.post(DELETE, {"back": "https://evil.example/"})
    assert response["Location"] == URL


def test_a_404_on_delete_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", ITEM, 404, json=error("not_found", "gone"))
    response = operator_client.post(DELETE, {"name": "Ari"})
    assert "not_found" in response.content.decode()
