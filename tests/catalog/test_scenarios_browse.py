"""/catalog/scenarios/ (#68): the list (with companions), a scenario's detail in every view, and
its versions."""

import re

import pytest
from django.test import Client

from tests.catalog.helpers import CHAR, CHAR2, answer_ratings, answer_settings, calls, error, text
from tests.catalog.scenario_helpers import (
    C_ITEM,
    COMPANION_SCN,
    DETAIL,
    PROFILES,
    S_ITEM,
    S_LIST,
    S_VERSIONS,
    SCN,
    URL,
    answer_profiles,
    card_of,
    detail,
    draft,
    fixture,
    list_item,
    listing,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

ITEM_PATH = f"/catalog/scenarios/{SCN}"


def answer_lookups(engine: FakeEngine) -> None:
    answer_ratings(engine)
    answer_settings(engine)


def character_card(name: str = "Sir Aldric", **changes: object) -> dict[str, object]:
    body: dict[str, object] = dict(engine_fixture("get_catalog_character_card"))
    body.update(name=name, **changes)
    return body


def list_params(engine: FakeEngine) -> list[tuple[str, str]]:
    (listed,) = calls(engine, "GET", S_LIST)
    return list(listed.url.params.multi_items())


# --- list -----------------------------------------------------------------------------------------


def test_the_list_shows_each_scenario_and_leaves_companions_out_by_default(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond("GET", S_LIST, 200, json=listing(list_item(0)))
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert ("include_companions", "true") not in list_params(engine)
    row = html[html.index(f'id="scenario-{SCN}"') :]
    row = row[: row.index("</tr>")]
    assert "Harvest Weekend" in row
    assert '<a class="label" href="/catalog/scenarios/?label=family">family</a>' in row
    assert "Teen" in text(row)
    assert "public" in text(row)
    assert "console:nate" in row
    assert "Companion of" not in html
    assert 'href="/catalog/scenarios/new/"' in html
    assert 'name="include_companions"' in html
    assert "checked" not in html.split('name="include_companions"')[1].split(">")[0]


def test_include_companions_lists_and_marks_them(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    second = list_item(1, id="01JC000000000000000000SCN3", name="Mira, again")
    engine.respond("GET", S_LIST, 200, json=listing(list_item(0), list_item(1), second))
    engine.respond("GET", C_ITEM, 200, json=character_card())
    html = operator_client.get(f"{URL}?include_companions=on").content.decode()
    assert ("include_companions", "true") in list_params(engine)
    # One card read for the one distinct character, in the card projection.
    (read,) = calls(engine, "GET", f"/catalog/characters/{CHAR}")
    assert dict(read.url.params) == {"projection": "card"}
    marker = f'Companion of <a href="/catalog/characters/{CHAR}/">Sir Aldric</a>'
    assert html.count(marker) == 2
    plain = html[html.index(f'id="scenario-{SCN}"') :]
    assert "Companion of" not in plain[: plain.index("</tr>")]


def test_a_companion_whose_character_read_fails_shows_the_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond(
        "GET", S_LIST, 200, json=listing(list_item(1), list_item(1, companion_of=CHAR2, id="X"))
    )
    engine.respond("GET", C_ITEM, 404, json=error("not_found"))
    html = operator_client.get(f"{URL}?include_companions=on").content.decode()
    assert f'Companion of <a href="/catalog/characters/{CHAR}/">{CHAR}</a>' in html
    assert f'Companion of <a href="/catalog/characters/{CHAR2}/">{CHAR2}</a>' in html
    assert "name unavailable: 404 not_found" in html


def test_the_filters_are_sent(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", S_LIST, 200, json=listing(list_item(0)))
    query = (
        "q=farm&label=family,%20drama&content_rating=00000000000000000000RAT002"
        "&listing_status=public&owner=alice&relation=fan&relation_principal=bob&status=draft"
    )
    operator_client.get(f"{URL}?{query}")
    params = list_params(engine)
    for pair in (
        ("q", "farm"),
        ("label", "family"),
        ("label", "drama"),
        ("content_rating", "00000000000000000000RAT002"),
        ("listing_status", "public"),
        ("owner", "alice"),
        ("relation", "fan:bob"),
        ("status", "draft"),
    ):
        assert pair in params


def test_an_invalid_filter_makes_no_list_call(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    response = operator_client.get(f"{URL}?label=bad%21label")
    assert response.status_code == 200
    assert calls(engine, "GET", S_LIST) == []
    assert "Labels are 1 to 40" in response.content.decode()


def test_next_page_keeps_the_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", S_LIST, 200, json=listing(list_item(0), next_cursor="c2"))
    html = operator_client.get(f"{URL}?include_companions=on").content.decode()
    assert 'href="?include_companions=on&amp;cursor=c2">Next page' in html


def test_the_nav_links_the_scenarios(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", S_LIST, 200, json=listing(list_item(0)))
    html = operator_client.get(URL).content.decode()
    nav = html[html.index('<nav class="shell">') : html.index("</nav>")]
    assert 'href="/catalog/scenarios/"' in nav


# --- detail ---------------------------------------------------------------------------------------


def detail_page(client: Client, engine: FakeEngine, query: str = "") -> str:
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(f"{DETAIL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def test_the_detail_shows_every_section(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=detail())
    html = detail_page(operator_client, engine)
    sections = [
        "card",
        "metadata",
        "world",
        "cast",
        "opening",
        "world-seeds",
        "story-settings-defaults",
        "recommendation",
        "visual-style",
        "creation-rules",
        "drivers",
        "deferred",
    ]
    positions = [html.index(f'<section id="{s}">') for s in sections]
    assert positions == sorted(positions)
    page = text(html)
    assert "Version 2 (latest)" in page
    assert "A farmhouse outside a small river town." in page
    assert "farmhouse The farmhouse Where everyone sleeps." in page
    assert "tractor Old Red item the tractor The tractor that never starts." in page
    assert "town Riverbend other (default)" in page
    assert "Dawn over the fields." in page
    assert "Default output viewpoint son" in page
    assert "Lite default" in page  # the profile's name
    assert 'href="/guardrails/ratings/00000000000000000000RAT002/">Teen</a>' in html
    assert "Only when a minor is involved or present" in page
    assert "Stored by Lite but not used until a later Engine milestone" in page
    assert '<details class="deferred">' in html
    # The latest view reads the draft too (identical here: no note).
    reads = [dict(r.url.params) for r in calls(engine, "GET", ITEM_PATH)]
    assert reads == [{"projection": "full"}, {"projection": "full", "draft": "true"}]
    assert "draft-differs" not in html
    assert 'href="/catalog/scenarios/01JC000000000000000000SCN2/edit/"' in html


def test_the_policies_table_shows_the_defaults(operator_client: Client, engine: FakeEngine) -> None:
    body = detail()
    body["story_settings_defaults"]["policies"] = [
        {"name": "Plain", "rule": {"type": "regex", "patterns": ["x"]}, "message": "No."}
    ]
    engine.respond("GET", S_ITEM, 200, json=body)
    page = text(detail_page(operator_client, engine))
    assert "Plain Text output (default) regex" in page
    assert "Always No." in page


def test_the_draft_differs_note(operator_client: Client, engine: FakeEngine) -> None:
    changed = draft()
    changed["drivers"]["endings"] = ["Something else."]
    engine.respond_sequence(
        "GET", S_ITEM, [{"status": 200, "json": detail()}, {"status": 200, "json": changed}]
    )
    html = detail_page(operator_client, engine)
    assert "The draft differs from this published version" in html


def test_the_envelope_alone_does_not_differ(operator_client: Client, engine: FakeEngine) -> None:
    other = draft(updated_at="2026-10-23T10:00:00.000000Z", listing_status="private")
    engine.respond_sequence(
        "GET", S_ITEM, [{"status": 200, "json": detail()}, {"status": 200, "json": other}]
    )
    assert "draft-differs" not in detail_page(operator_client, engine)


def test_view_draft_and_version(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=draft())
    page = text(detail_page(operator_client, engine, "?view=draft"))
    assert "Draft, revision 5" in page
    assert "Draft revision 5" in page
    engine.reset()
    engine.respond("GET", S_ITEM, 200, json=detail(version=1))
    page = text(detail_page(operator_client, engine, "?version=1"))
    assert "Version 1" in page
    reads = [dict(r.url.params) for r in calls(engine, "GET", ITEM_PATH)]
    assert reads == [{"projection": "full", "version": "1"}]


def test_a_never_published_scenario_shows_its_draft(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        S_ITEM,
        [
            {"status": 422, "json": error("not_published")},
            {"status": 200, "json": draft(current_version=None, status="draft")},
        ],
    )
    page = text(detail_page(operator_client, engine))
    assert "Draft — never published" in page


def test_view_as_public_card_shows_the_roles(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=card_of(detail()))
    html = detail_page(operator_client, engine, "?projection=card")
    page = text(html)
    assert "Public card view" in page
    assert "mother Mother npc open Her children are home." in page
    reads = [dict(r.url.params) for r in calls(engine, "GET", ITEM_PATH)]
    assert reads == [{"projection": "card"}]
    assert "Deferred" not in page


def test_a_companion_detail_names_its_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 200, json=detail(id=COMPANION_SCN, companion_of=CHAR))
    engine.respond("GET", C_ITEM, 200, json=character_card())
    answer_ratings(engine)
    answer_profiles(engine)
    html = operator_client.get(f"/catalog/scenarios/{COMPANION_SCN}/").content.decode()
    assert f'Companion of <a href="/catalog/characters/{CHAR}/">Sir Aldric</a>' in html
    assert f'href="/catalog/scenarios/{COMPANION_SCN}/edit/"' in html  # #74: still editable


def test_an_archived_scenario_disables_edit(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 200, json=detail(status="archived"))
    html = detail_page(operator_client, engine)
    assert "Archived — unarchive to edit" in html
    assert 'class="edit"' not in html


def test_long_text_is_truncated_with_the_whole_in_details(
    operator_client: Client, engine: FakeEngine
) -> None:
    long = "word " * 60
    body = detail()
    body["world"]["description"] = long
    engine.respond("GET", S_ITEM, 200, json=body)
    html = detail_page(operator_client, engine)
    assert '<span class="truncated">' in html
    assert '<details class="full-text">' in html


def test_unknown_lookups_show_the_ids(operator_client: Client, engine: FakeEngine) -> None:
    body = detail()
    body["recommendation"]["engine_profile_id"] = "gone"
    body["metadata"]["content_rating_id"] = "01JC0000000000000000000RAT"
    engine.respond("GET", S_ITEM, 200, json=body)
    html = detail_page(operator_client, engine)
    assert "<code>gone</code>" in html
    assert "<code>01JC0000000000000000000RAT</code>" in html


def test_a_failed_read_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_ITEM, 404, json=error("not_found", "No such scenario"))
    response = operator_client.get(DETAIL)
    assert response.status_code == 200
    assert "not_found" in response.content.decode()
    assert calls(engine, "GET", PROFILES) == []


# --- versions -------------------------------------------------------------------------------------


def test_the_versions_newest_first(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_VERSIONS, 200, json=fixture("versions"))
    engine.respond("GET", S_ITEM, 200, json=card_of(detail()))
    html = operator_client.get(f"{DETAIL}versions/").content.decode()
    assert html.index('id="version-2"') < html.index('id="version-1"')
    assert re.search(r"Version 2</a> \(latest\)", html)
    assert "Versions: Harvest Weekend" in text(html)
    assert "alice" in html


def test_never_published_versions(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_VERSIONS, 200, json={"items": []})
    engine.respond("GET", S_ITEM, 422, json=error("not_published"))
    html = operator_client.get(f"{DETAIL}versions/").content.decode()
    assert "Never published." in html
