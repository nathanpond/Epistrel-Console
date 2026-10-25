"""Archive, unarchive, delete and listing status of catalog characters and scenarios (#69)."""

import copy
import re
from html import unescape
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.models import EngineOperation
from tests.catalog.helpers import (
    CHAR,
    ITEM,
    answer_edit_reads,
    body_of,
    card,
    draft,
    form_data,
    full,
    writes,
)
from tests.catalog.scenario_helpers import (
    COMPANION_SCN,
    S_ITEM,
    SCN,
    answer_profiles,
    answer_ratings,
    as_post,
    posted,
)
from tests.catalog.scenario_helpers import answer_edit_reads as scenario_reads
from tests.catalog.scenario_helpers import detail as scenario_detail
from tests.catalog.scenario_helpers import draft as scenario_draft
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import SETTINGS, calls, error, hidden, text

C_DETAIL = f"/catalog/characters/{CHAR}/"
S_DETAIL = f"/catalog/scenarios/{SCN}/"
COMPANION_DETAIL = f"/catalog/scenarios/{COMPANION_SCN}/"
ROOT = {"character": "/catalog/characters/", "scenario": "/catalog/scenarios/"}
IDS = {"character": CHAR, "scenario": SCN}
ENGINE_ITEM = {"character": ITEM, "scenario": S_ITEM}
KIND = {"character": "catalog_character", "scenario": "scenario"}


def url(entry: str, action: str, entry_id: str | None = None) -> str:
    return f"{ROOT[entry]}{entry_id or IDS[entry]}/{action}/"


def engine_path(entry: str, suffix: str = "") -> str:
    """The Engine path the call goes to (the Console's root without its trailing slash)."""
    return f"{ROOT[entry]}{IDS[entry]}{suffix}"


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def status_body(entry: str, **changes: Any) -> dict[str, Any]:
    name = "catalog_characters_status" if entry == "character" else "catalog_scenarios_status"
    body: dict[str, Any] = copy.deepcopy(engine_fixture(name))
    return {**body, **changes}


def listing_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("catalog_listing_status"))
    return {**body, **changes}


def companion_refusal() -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("error_companion_follows_character")
    return body


def invalid_listing() -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("error_invalid_listing_status")
    return body


def confirm_post(client: Client, path: str, **query: str) -> Any:
    """GET the confirm page with ``query``, then POST what its form carries."""
    page = client.get(path, query).content.decode()
    data = {
        name: unescape(value)
        for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page)
    }
    data.pop("csrfmiddlewaretoken", None)
    return client.post(path, data)


def character_page(client: Client, engine: FakeEngine, query: str = "", **changes: Any) -> str:
    body = full(**changes)
    engine.respond("GET", ITEM, 200, json=body)
    response = client.get(f"{C_DETAIL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def scenario_page(
    client: Client,
    engine: FakeEngine,
    body: dict[str, Any] | None = None,
    query: str = "",
    path: str = S_DETAIL,
) -> str:
    engine.respond("GET", S_ITEM, 200, json=body if body is not None else scenario_detail())
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(f"{path}{query}")
    assert response.status_code == 200
    return response.content.decode()


def companion_scenario(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("catalog_characters_companion_draft"))
    body.update({"id": COMPANION_SCN, **changes})
    return body


# --- each action calls the right endpoint, for both entry types -----------------------------------


@pytest.mark.parametrize("entry", ["character", "scenario"])
def test_archive_posts_archive_and_returns_to_the_detail(
    operator_client: Client, engine: FakeEngine, entry: str
) -> None:
    engine.respond("POST", f"{ENGINE_ITEM[entry]}/archive", 200, json=status_body(entry))
    response = confirm_post(operator_client, url(entry, "archive"), name="Harbour")
    (sent,) = writes(engine)
    assert (sent.method, sent.url.path) == ("POST", engine_path(entry, "/archive"))
    assert sent.headers["Idempotency-Key"]
    assert response.status_code == 302
    assert response["Location"] == f"{ROOT[entry]}{IDS[entry]}/"
    follows = " (its companion follows)" if entry == "character" else ""
    assert flashes(response) == [f"Archived Harbour{follows}."]


@pytest.mark.parametrize("entry", ["character", "scenario"])
def test_unarchive_posts_unarchive_and_names_the_new_status(
    operator_client: Client, engine: FakeEngine, entry: str
) -> None:
    body = status_body(entry, status="published")
    engine.respond("POST", f"{ENGINE_ITEM[entry]}/unarchive", 200, json=body)
    response = confirm_post(operator_client, url(entry, "unarchive"), name="Harbour")
    (sent,) = writes(engine)
    assert (sent.method, sent.url.path) == ("POST", engine_path(entry, "/unarchive"))
    follows = " (its companion follows)" if entry == "character" else ""
    assert flashes(response) == [f"Unarchived Harbour (now published){follows}."]
    assert response["Location"] == f"{ROOT[entry]}{IDS[entry]}/"


def test_an_engine_no_op_gets_the_normal_flash(operator_client: Client, engine: FakeEngine) -> None:
    """Unarchiving an entry that is not archived answers it unchanged: the status shown is the
    response's."""
    body = status_body("scenario", status="draft", current_version=None)
    engine.respond("POST", f"{S_ITEM}/unarchive", 200, json=body)
    response = confirm_post(operator_client, url("scenario", "unarchive"), name="Harbour")
    assert flashes(response) == ["Unarchived Harbour (now draft)."]


@pytest.mark.parametrize("entry", ["character", "scenario"])
def test_delete_sends_delete_and_returns_to_the_list(
    operator_client: Client, engine: FakeEngine, entry: str
) -> None:
    engine.respond("DELETE", ENGINE_ITEM[entry], 204)
    query = {"name": "Harbour", "next": f"{ROOT[entry]}{IDS[entry]}/"}
    if entry == "character":
        query["has_companion"] = "1"
    response = confirm_post(operator_client, url(entry, "delete"), **query)
    (sent,) = writes(engine)
    assert (sent.method, sent.url.path) == ("DELETE", engine_path(entry))
    assert response["Location"] == ROOT[entry]
    also = " and its companion" if entry == "character" else ""
    assert flashes(response) == [f"Deleted Harbour{also}."]


@pytest.mark.parametrize("entry", ["character", "scenario"])
def test_listing_status_puts_the_value(
    operator_client: Client, engine: FakeEngine, entry: str
) -> None:
    engine.respond("PUT", f"{ENGINE_ITEM[entry]}/listing-status", 200, json=listing_body())
    data = {
        "value": " in_review\t",
        "current_listing_status": "public",
        "name": "Harbour",
        "idempotency_key": "01JC0000000000000000000KEY",
    }
    if entry == "character":
        data["has_companion"] = "1"
    response = operator_client.post(url(entry, "listing-status"), data)
    (sent,) = writes(engine)
    assert (sent.method, sent.url.path) == ("PUT", engine_path(entry, "/listing-status"))
    assert body_of(sent) == {"value": "in_review"}
    assert sent.headers["Idempotency-Key"] == "01JC0000000000000000000KEY"
    assert response["Location"] == f"{ROOT[entry]}{IDS[entry]}/"
    follows = " (its companion follows)" if entry == "character" else ""
    assert flashes(response) == [f"Listing status set to in_review{follows}."]


@pytest.mark.parametrize("entry", ["character", "scenario"])
@pytest.mark.parametrize("action", ["archive", "unarchive", "delete", "listing-status"])
def test_every_write_is_recorded(
    operator_client: Client, engine: FakeEngine, operator: Any, entry: str, action: str
) -> None:
    item = ENGINE_ITEM[entry]
    if action == "delete":
        engine.respond("DELETE", item, 204)
        method = f"delete_{KIND[entry]}"
    elif action == "listing-status":
        engine.respond("PUT", f"{item}/listing-status", 200, json=listing_body())
        method = f"set_{KIND[entry]}_listing_status"
    else:
        engine.respond("POST", f"{item}/{action}", 200, json=status_body(entry))
        method = f"{action}_{KIND[entry]}"
    if action == "listing-status":
        operator_client.post(url(entry, action), {"value": "in_review"})
    else:
        confirm_post(operator_client, url(entry, action))
    row = EngineOperation.objects.get(kind=method)
    assert (row.entity_type, row.entity_id, row.operator) == (entry, IDS[entry], operator)


# --- confirm pages --------------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["character", "scenario"])
@pytest.mark.parametrize("action", ["archive", "unarchive", "delete"])
def test_the_confirm_get_makes_no_engine_call_and_cancel_goes_back(
    operator_client: Client, engine: FakeEngine, entry: str, action: str
) -> None:
    back = f"{ROOT[entry]}{IDS[entry]}/?version=1"
    response = operator_client.get(url(entry, action), {"name": "Harbour", "next": back})
    html = response.content.decode()
    assert response.status_code == 200
    assert "<em>Harbour</em>" in html
    cancel = re.search(r'<a href="([^"]+)">Cancel</a>', html)
    assert cancel is not None
    assert unescape(cancel.group(1)) == back
    assert engine.requests == []


def test_the_archive_confirm_names_the_companion_for_a_character_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = {"name": "Sir Aldric", "has_companion": "1"}
    character = text(operator_client.get(url("character", "archive"), query).content.decode())
    assert (
        "Archive Sir Aldric ? It stays readable but cannot be edited, published or used to "
        "create stories. Its companion is archived with it." in character
    )
    scenario = text(operator_client.get(url("scenario", "archive"), query).content.decode())
    assert "It stays readable but cannot be edited, published or used to create stories." in (
        scenario
    )
    assert "Its companion" not in scenario  # has_companion is ignored for a scenario
    assert engine.requests == []


def test_a_characters_unarchive_confirm_mentions_the_companion(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = {"name": "Sir Aldric", "has_companion": "1"}
    html = text(operator_client.get(url("character", "unarchive"), query).content.decode())
    assert (
        "Unarchive Sir Aldric ? It returns to published (or draft if it was never published). "
        "Its companion is restored with it." in html
    )


def test_the_character_delete_confirm_names_the_companion(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = {"name": "Sir Aldric", "has_companion": "1"}
    html = text(operator_client.get(url("character", "delete"), query).content.decode())
    assert (
        "Permanently delete Sir Aldric , all its versions and its companion? Stories created "
        "from it keep their copies." in html
    )


def test_the_scenario_delete_confirm_has_no_companion_clause(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = {"name": "Harvest Weekend", "has_companion": "1"}  # ignored for a scenario
    html = text(operator_client.get(url("scenario", "delete"), query).content.decode())
    assert (
        "Permanently delete Harvest Weekend and all its versions? Stories created from it keep "
        "their copies." in html
    )
    assert "its companion" not in html


def test_a_character_without_a_companion_has_no_companion_clauses(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = text(operator_client.get(url("character", "archive"), {"name": "A"}).content.decode())
    assert "Its companion" not in html
    engine.respond("DELETE", ITEM, 204)
    response = confirm_post(operator_client, url("character", "delete"), name="A")
    assert flashes(response) == ["Deleted A."]


def test_a_blank_name_shows_the_unnamed_label(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(url("scenario", "archive")).content.decode()
    assert f"<em>(unnamed) …{SCN[-6:]}</em>" in html


def test_the_name_is_escaped(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(url("scenario", "archive"), {"name": "<b>x</b>"}).content.decode()
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_an_off_site_next_falls_back_to_the_detail(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", f"{S_ITEM}/archive", 200, json=status_body("scenario"))
    response = confirm_post(
        operator_client, url("scenario", "archive"), name="H", next="https://evil.example/"
    )
    assert response["Location"] == S_DETAIL


def test_another_error_rerenders_the_confirm_under_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", f"{S_ITEM}/archive", 404, json=error("not_found", "gone"))
    page = operator_client.get(url("scenario", "archive"), {"name": "H"}).content.decode()
    key = hidden(page, "idempotency_key")
    response = operator_client.post(
        url("scenario", "archive"), {"idempotency_key": key, "name": "H"}
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Engine error 404 not_found" in html
    assert "<em>H</em>" in html  # the confirm page again
    assert hidden(html, "idempotency_key") != key  # with a fresh key


# --- companion_follows_character ------------------------------------------------------------------


def test_companion_follows_character_names_and_links_the_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", f"{S_ITEM}/archive", 422, json=companion_refusal())
    engine.respond("GET", ITEM, 200, json=card())
    response = operator_client.post(
        url("scenario", "archive", COMPANION_SCN), {"name": "Companion", "companion_of": CHAR}
    )
    assert response["Location"] == COMPANION_DETAIL
    (flash,) = flashes(response)
    assert flash == (
        f'This is <a href="/catalog/characters/{CHAR}/">Sir Aldric</a>\'s companion — '
        "archive, delete or relabel the character instead"
    )
    (read,) = calls(engine, "GET", f"/catalog/characters/{CHAR}")
    assert read.url.params["projection"] == "card"


def test_companion_follows_shows_the_id_when_the_card_read_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", S_ITEM, 422, json=companion_refusal())
    engine.respond("GET", ITEM, 404, json=error("not_found", "gone"))
    response = operator_client.post(
        url("scenario", "delete", COMPANION_SCN), {"companion_of": CHAR}
    )
    (flash,) = flashes(response)
    assert f'<a href="/catalog/characters/{CHAR}/">{CHAR}</a>' in flash


def test_companion_follows_without_companion_of_has_no_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PUT", f"{S_ITEM}/listing-status", 422, json=companion_refusal())
    response = operator_client.post(
        url("scenario", "listing-status", COMPANION_SCN), {"value": "private"}
    )
    assert flashes(response) == [
        "This is a character's companion — archive, delete or relabel the character instead"
    ]
    assert response["Location"] == COMPANION_DETAIL
    assert calls(engine, "GET") == []


def test_the_posted_companion_of_is_quoted_in_the_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", f"{S_ITEM}/archive", 422, json=companion_refusal())
    engine.respond("GET", ITEM, 404, json=error("not_found", "gone"))
    response = operator_client.post(
        url("scenario", "archive", COMPANION_SCN), {"companion_of": 'a b"<x>'}
    )
    (flash,) = flashes(response)
    assert 'href="/catalog/characters/a%20b%22%3Cx%3E/"' in flash
    assert "&lt;x&gt;" in flash


# --- listing status -------------------------------------------------------------------------------


def test_an_unchanged_listing_status_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.post(
        url("character", "listing-status"),
        {"value": "public\t", "current_listing_status": "public", "name": "A"},
    )
    assert engine.requests == []
    assert flashes(response) == ["No change."]
    assert response["Location"] == C_DETAIL


def test_invalid_listing_status_shows_the_allowed_values(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PUT", f"{ITEM}/listing-status", 422, json=invalid_listing())
    response = operator_client.post(
        url("character", "listing-status"),
        {"value": "featured", "current_listing_status": "public"},
    )
    assert response["Location"] == C_DETAIL
    assert flashes(response) == [
        "featured is not a listing status here. Allowed: private, in_review, public."
    ]


def test_another_listing_status_error_renders_the_panel_with_a_way_back(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PUT", f"{S_ITEM}/listing-status", 404, json=error("not_found", "gone"))
    response = operator_client.post(
        url("scenario", "listing-status"),
        {"value": "private", "current_listing_status": "public", "name": "Harvest Weekend"},
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert "Engine error 404 not_found" in html
    assert f'<a class="back" href="{S_DETAIL}">Back to Harvest Weekend</a>' in html


def test_listing_status_get_is_not_allowed(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.get(url("scenario", "listing-status")).status_code == 405


def test_the_select_offers_the_deployments_values_in_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = character_page(operator_client, engine, listing_status="in_review")
    form = html.split('class="listing-status"')[1].split("</form>")[0]
    options = re.findall(r'<option value="([^"]*)"( selected)?>([^<]*)</option>', form)
    assert options == [
        ("private", "", "private"),
        ("in_review", " selected", "in_review"),
        ("public", "", "public"),
    ]
    assert 'name="current_listing_status" value="in_review"' in form
    assert 'name="has_companion" value="1"' in form
    assert len(calls(engine, "GET", SETTINGS)) == 1


def test_a_current_value_missing_from_the_list_stays_selected(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = character_page(operator_client, engine, listing_status="legacy")
    assert '<option value="legacy" selected>legacy (not in list)</option>' in html


def test_unavailable_settings_show_the_current_value(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SETTINGS, 503, json=error("unavailable", "down"))
    html = character_page(operator_client, engine)
    assert '<select name="value"' not in html
    assert "Listing values unavailable: 503 unavailable" in html
    assert "Listing status: <code>public</code>" in html


# --- the detail pages' controls -------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "?version=1", "?view=draft"])
def test_controls_show_on_every_full_character_view(
    operator_client: Client, engine: FakeEngine, query: str
) -> None:
    html = character_page(operator_client, engine, query)
    assert 'class="button archive"' in html
    assert 'class="button danger delete"' in html
    assert 'class="listing-status"' in html
    archive = re.search(r'href="([^"]+)">Archive</a>', html)
    assert archive is not None
    link = unescape(archive.group(1))
    assert link.startswith(f"{C_DETAIL}archive/?name=Sir+Aldric&has_companion=1&next=")


def test_controls_show_on_a_scenario_full_view(operator_client: Client, engine: FakeEngine) -> None:
    html = scenario_page(operator_client, engine)
    assert 'class="button archive"' in html
    assert 'class="listing-status"' in html
    delete = re.search(r'href="([^"]+)">Delete</a>', html)
    assert delete is not None
    assert "has_companion" not in unescape(delete.group(1))


def test_the_card_view_has_no_controls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=card(status="archived"))
    html = operator_client.get(f"{C_DETAIL}?projection=card").content.decode()
    assert 'id="lifecycle"' not in html
    assert '<p class="archived-tag"><strong>Archived</strong></p>' in html
    assert "this entry can be read but not edited" not in html
    assert calls(engine, "GET", SETTINGS) == []


def test_the_companion_shows_follows_and_no_controls(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=card())  # the companion-of card read
    html = scenario_page(
        operator_client,
        engine,
        companion_scenario(listing_status="in_review"),
        query="?view=draft",
        path=COMPANION_DETAIL,
    )
    assert (
        f'Follows <a href="/catalog/characters/{CHAR}/">Sir Aldric</a> · Listing status: '
        "<code>in_review</code>" in html
    )
    assert 'class="button archive"' not in html
    assert 'class="button danger delete"' not in html
    assert '<select name="value"' not in html
    assert calls(engine, "GET", SETTINGS) == []  # no select: no settings read
    assert len(calls(engine, "GET", f"/catalog/characters/{CHAR}")) == 1  # one card read


def test_follows_shows_the_id_when_the_card_read_failed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 404, json=error("not_found", "gone"))
    html = scenario_page(
        operator_client, engine, companion_scenario(), query="?view=draft", path=COMPANION_DETAIL
    )
    assert f'Follows <a href="/catalog/characters/{CHAR}/">{CHAR}</a>' in html


def test_a_forced_archive_on_the_companion_shows_the_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    """No Console pre-check: the forced POST goes to the Engine, whose 422 is the message."""
    engine.respond("POST", f"{S_ITEM}/archive", 422, json=companion_refusal())
    engine.respond("GET", ITEM, 200, json=card())
    response = confirm_post(
        operator_client,
        url("scenario", "archive", COMPANION_SCN),
        name="Companion",
        companion_of=CHAR,
    )
    assert [r.method for r in writes(engine)] == ["POST"]
    (flash,) = flashes(response)
    assert f'This is <a href="/catalog/characters/{CHAR}/">Sir Aldric</a>\'s companion' in flash
    assert response["Location"] == COMPANION_DETAIL


# --- archived entries -----------------------------------------------------------------------------


def test_an_archived_character_shows_the_banner_and_disables_edit_and_publish(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = character_page(operator_client, engine, status="archived")
    page = text(html)
    assert (
        "Archived — this entry can be read but not edited, published or used for new stories."
        in page
    )
    assert 'class="edit"' not in html
    assert 'disabled title="Archived — unarchive to edit">Edit</button>' in html
    publish = html.split('class="publish-form"')[1].split("</form>")[0]
    assert '<button type="submit" disabled>Publish</button>' in publish
    # what stays enabled: unarchive (banner and controls), delete and the listing status
    assert html.count('class="button unarchive"') == 2
    assert 'class="button archive"' not in html
    assert 'class="button danger delete"' in html
    assert '<select name="value"' in html


def test_an_archived_scenario_shows_the_banner_and_disables_edit(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = scenario_page(operator_client, engine, scenario_detail(status="archived"))
    assert "Archived — this entry can be read but not edited" in text(html)
    assert 'class="edit"' not in html
    assert 'disabled title="Archived — unarchive to edit">Edit</button>' in html
    unarchive = re.search(r'class="button unarchive" href="([^"]+)"', html)
    assert unarchive is not None
    assert unescape(unarchive.group(1)).startswith(f"{S_DETAIL}unarchive/?name=Harvest+Weekend")


def test_an_archived_companion_banner_points_to_the_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=card())
    html = scenario_page(
        operator_client,
        engine,
        companion_scenario(status="archived"),
        query="?view=draft",
        path=COMPANION_DETAIL,
    )
    assert (
        f'Archived with <a href="/catalog/characters/{CHAR}/">Sir Aldric</a> — unarchive the '
        "character to restore it." in html
    )
    assert 'class="button unarchive"' not in html


def test_the_edit_page_of_an_archived_entry_renders_read_only_with_the_banner(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine, draft(status="archived"))
    html = operator_client.get(f"{C_DETAIL}edit/").content.decode()
    assert "Archived — this entry can be read but not edited" in text(html)
    unarchive = re.search(r'class="button unarchive" href="([^"]+)"', html)
    assert unarchive is not None
    assert "has_companion=1" in unescape(unarchive.group(1))
    assert 'value="save" disabled title="Archived — unarchive to edit"' in html


def test_the_edit_page_of_an_archived_companion_names_the_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#74: the editor names a companion's character from one card read (#69 showed the id)."""
    engine.respond("GET", S_ITEM, 200, json=companion_scenario(status="archived"))
    engine.respond("GET", ITEM, 200, json=card())
    answer_ratings(engine)
    answer_profiles(engine)
    html = operator_client.get(f"{COMPANION_DETAIL}edit/").content.decode()
    assert f'Archived with <a href="/catalog/characters/{CHAR}/">Sir Aldric</a>' in html
    (read,) = calls(engine, "GET", f"/catalog/characters/{CHAR}")
    assert dict(read.url.params) == {"projection": "card"}


def test_the_edit_page_of_an_archived_companion_falls_back_to_the_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 200, json=companion_scenario(status="archived"))
    engine.respond("GET", ITEM, 503, json=error("database_unavailable", "down"))
    answer_ratings(engine)
    answer_profiles(engine)
    html = operator_client.get(f"{COMPANION_DETAIL}edit/").content.decode()
    assert f'Archived with <a href="/catalog/characters/{CHAR}/">{CHAR}</a>' in html


@pytest.mark.parametrize("entry", ["character", "scenario"])
def test_a_forced_edit_post_shows_the_engines_entry_archived_refusal(
    operator_client: Client, engine: FakeEngine, entry: str
) -> None:
    """Complement: the page disables Save on an archived entry, but a POST from a page rendered
    before the entry was archived still reaches the Engine, whose refusal is shown."""
    if entry == "character":
        answer_edit_reads(engine)
        page = operator_client.get(f"{C_DETAIL}edit/")
        data: Any = {**form_data(page), "definition__true_age": "41", "action": "save"}
    else:
        scenario_reads(engine, scenario_draft())
        page = operator_client.get(f"{S_DETAIL}edit/")
        data = posted(page)
        data["public_card__name"] = "Renamed"
        data = as_post(data)
    engine.reset()
    refusal = error("entry_archived", "The entry is archived")
    engine.respond("PATCH", ENGINE_ITEM[entry], 422, json=refusal)
    response = operator_client.post(f"{ROOT[entry]}{IDS[entry]}/edit/", data)
    html = response.content.decode()
    assert [r.method for r in writes(engine)] == ["PATCH"]
    assert "Engine error 422 entry_archived" in html
