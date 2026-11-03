"""/catalog/scenarios/ (#68): the list (with companions), a scenario's detail in every view, and
its versions."""

import re
from typing import Any

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
from tests.fakes.ownership import answer_one_relation, shows_panel
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
        "world-seeds",  # #73: the cast table's partial renders both
        "opening",
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
    # #268: "Deferred" is "Rules and declarations", its three values shown as they are.
    assert "Stored by Lite but not used until a later Engine milestone" not in page
    assert "<h2>Rules and declarations</h2>" in html
    assert '<dl class="deferred">' in html
    # The latest view reads the draft too (identical here: no note).
    reads = [dict(r.url.params) for r in calls(engine, "GET", ITEM_PATH)]
    assert reads == [{"projection": "full"}, {"projection": "full", "draft": "true"}]
    assert "draft-differs" not in html
    assert 'href="/catalog/scenarios/01JC000000000000000000SCN2/edit/"' in html


def test_detail_lists_co_author_knowledge(operator_client: Client, engine: FakeEngine) -> None:
    """#233: "Co-author knowledge" with ``setting_label``, right after Player authority; ``None``
    reads "—"."""
    body = detail()
    body["story_settings_defaults"]["co_author_knowledge"] = "free"
    engine.respond("GET", S_ITEM, 200, json=body)
    html = detail_page(operator_client, engine)
    section = html[html.index('<section id="story-settings-defaults">') :]
    section = section[: section.index("</dl>")]
    rows = re.findall(r"<dt>([^<]*)</dt><dd>([^<]*)</dd>", section)
    labels = [label for label, _ in rows]
    start = labels.index("Player authority")
    assert rows[start + 1] == ("Co-author knowledge", "Free (accept, no notice)")
    engine.reset()
    body["story_settings_defaults"]["co_author_knowledge"] = None
    engine.respond("GET", S_ITEM, 200, json=body)
    assert "Co-author knowledge —" in text(detail_page(operator_client, engine))


def test_detail_lists_the_new_story_setting_defaults(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#198 and #233: five rows after Player authority, before Turn concurrency."""
    body = detail()
    body["story_settings_defaults"].update(
        player_view="story", simultaneous_tie_break="state:initiative", reactive_interjections=True
    )
    engine.respond("GET", S_ITEM, 200, json=body)
    html = detail_page(operator_client, engine)
    section = html[html.index('<section id="story-settings-defaults">') :]
    section = section[: section.index("</dl>")]
    rows = re.findall(r"<dt>([^<]*)</dt><dd>([^<]*)</dd>", section)
    labels = [label for label, _ in rows]
    start = labels.index("Player authority")
    assert rows[start + 1 : start + 7] == [
        ("Co-author knowledge", "—"),
        ("Player view (where perception is on)", "Story (every beat in full)"),
        ("Sequence order (sequential turns)", "—"),
        ("Tie-break (simultaneous turns)", "state:initiative (M6: request order applies)"),
        ("Reactive interjections", "On"),
        ("Turn concurrency", "—"),
    ]
    engine.reset()
    body["story_settings_defaults"].update(
        player_view=None, sequence_order="director", reactive_interjections=False
    )
    engine.respond("GET", S_ITEM, 200, json=body)
    page = text(detail_page(operator_client, engine))
    assert "Player view (where perception is on) —" in page
    assert "Sequence order (sequential turns) Director" in page
    assert "Reactive interjections Off" in page


def test_setting_label() -> None:
    from console.story_settings import setting_label

    assert setting_label("player_view", None) == "—"
    assert setting_label("player_view", "character") == "Character (audience-filtered)"
    assert setting_label("sequence_order", "random") == "Random"  # C3: humanised, no suffix
    assert setting_label("simultaneous_tie_break", "request_order") == "Request order"
    assert setting_label("simultaneous_tie_break", "coin_flip") == "Coin flip"
    assert setting_label("reactive_interjections", False) == "Off"


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
    # #205: the detail fixture's topology and channels, which the #68 draft fixture lacks.
    other["world"]["locations"] = detail()["world"]["locations"]
    other["starting_state"]["channels"] = detail()["starting_state"]["channels"]
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


def test_the_detail_offers_start_a_story_at_the_shown_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#75: a published, non-archived scenario's detail links step 2 at the version shown."""
    engine.respond("GET", S_ITEM, 200, json=detail())
    html = detail_page(operator_client, engine)
    start = f"/stories/new/from-scenario/?scenario={SCN}&amp;version=2"
    assert f'href="{start}">Start a story</a>' in html


def test_an_archived_scenario_disables_start_a_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 200, json=detail(status="archived"))
    html = detail_page(operator_client, engine)
    assert ">Start a story</a>" not in html
    assert "Archived — unarchive to start a story" in html


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


def test_the_detail_includes_the_ownership_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#79: the panel, after the main sections and before "Deferred"."""
    engine.respond("GET", S_ITEM, 200, json=detail())
    answer_one_relation(engine, "scenario", SCN)
    html = detail_page(operator_client, engine)
    assert shows_panel(html, "scenario", SCN)
    assert html.index('<section id="drivers">') < html.index('id="ownership"')
    assert html.index('id="ownership"') < html.index('<section id="deferred">')


def test_a_companions_ownership_panel_is_read_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#79: a companion's owner and relations follow its character: shown, never offered."""
    engine.respond("GET", S_ITEM, 200, json=detail(id=COMPANION_SCN, companion_of=CHAR))
    engine.respond("GET", C_ITEM, 200, json=character_card())
    answer_ratings(engine)
    answer_profiles(engine)
    answer_one_relation(engine, "scenario", COMPANION_SCN)
    html = operator_client.get(f"/catalog/scenarios/{COMPANION_SCN}/").content.decode()
    panel = html.split('id="ownership"', 1)[1].split("</section>", 1)[0]
    assert f'Follows <a href="/catalog/characters/{CHAR}/">Sir Aldric</a>' in panel
    assert "<code>editor</code>" in panel
    assert "<form" not in panel
    assert "/ownership/" not in panel
    card_reads = [r for r in engine.requests if r.url.path == f"/catalog/characters/{CHAR}"]
    assert len(card_reads) == 1  # the panel shares the page's one card read


# --- #205: topology and remote channels -----------------------------------------------------------


def topology_detail(**changes: object) -> dict[str, object]:
    """The detail fixture with undeclared ids: barn lies in an ``attic``, the farmhouse also
    adjoins a ``well`` (an explicit ``null`` barrier) and a barn entry with ``none``, and a
    channel names a ``stranger``."""
    body = detail(**changes)
    farmhouse, barn = body["world"]["locations"]
    farmhouse["adjacent_to"] = [
        {"location": "barn", "barrier": "door_closed"},
        {"location": "well", "barrier": None},
        {"location": "barn", "barrier": "none"},
    ]
    barn["located_in"] = "attic"
    body["starting_state"]["channels"] = [
        {"id": "phone", "participants": ["mother", "son", "mother"]},
        {"id": "radio"},
        {"id": "ghost", "participants": ["stranger"]},
    ]
    return body


def location_cells(html: str, location_id: str) -> list[str]:
    row = html[html.index(f'<tr id="location-{location_id}">') :]
    row = row[: row.index("</tr>")]
    return re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)


ADJOINS = (
    'The barn (<code>barn</code>) · <span title="door_closed">Door closed</span>; '
    "<code>well</code> (undeclared) · Door open (default); "
    'The barn (<code>barn</code>) · <span title="none">No barrier</span>'
)
CHANNELS = (
    '<dt>Remote channels</dt><dd class="channels"><code>phone</code>: <code>mother</code>, '
    "<code>son</code>, <code>mother</code><br><code>radio</code>: no participants<br>"
    "<code>ghost</code>: <code>stranger</code> (undeclared)</dd>"
)


def test_the_detail_page_shows_topology_and_channels(
    operator_client: Client, engine: FakeEngine
) -> None:
    # The fixture as is: barn lies in the farmhouse, behind a closed door from it.
    engine.respond("GET", S_ITEM, 200, json=detail())
    html = detail_page(operator_client, engine)
    assert "<th>Lies in</th><th>Adjoins</th>" in html
    assert location_cells(html, "farmhouse")[3:] == [
        "—",
        'The barn (<code>barn</code>) · <span title="door_closed">Door closed</span>',
    ]
    assert location_cells(html, "barn")[3:] == ["The farmhouse (<code>farmhouse</code>)", "—"]
    assert 'Remote channels</dt><dd class="channels"><code>phone</code>: ' in html
    # Undeclared ids are marked; an unset or null barrier reads the default, with no title.
    engine.reset()
    engine.respond("GET", S_ITEM, 200, json=topology_detail())
    html = detail_page(operator_client, engine)
    assert location_cells(html, "farmhouse")[3:] == ["—", ADJOINS]
    assert location_cells(html, "barn")[3:] == ["<code>attic</code> (undeclared)", "—"]
    assert CHANNELS in html
    opening = html[html.index('<section id="opening">') :]
    rows = re.findall(r"<dt>([^<]*)</dt>", opening[: opening.index("</dl>")])
    assert rows == ["Narration", "Starting location", "Roles present", "Remote channels"]
    # A published version shows the same rows.
    engine.reset()
    engine.respond("GET", S_ITEM, 200, json=topology_detail(version=1))
    html = detail_page(operator_client, engine, "?version=1")
    assert location_cells(html, "farmhouse")[3:] == ["—", ADJOINS]
    assert CHANNELS in html
    # Complement: the public card shows none of it.
    engine.reset()
    engine.respond("GET", S_ITEM, 200, json=card_of(topology_detail()))
    page = text(detail_page(operator_client, engine, "?projection=card"))
    for label in ("Lies in", "Adjoins", "Remote channels"):
        assert label not in page


def test_a_scenario_without_channels_shows_a_dash(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = detail()
    body["starting_state"].pop("channels")
    engine.respond("GET", S_ITEM, 200, json=body)
    html = detail_page(operator_client, engine)
    assert '<dt>Remote channels</dt><dd class="channels">—</dd>' in html


# --- #267: the starting States and the roles' initial values --------------------------------------


def states_detail(**changes: Any) -> dict[str, Any]:
    body = fixture("draft_states", **changes)
    body["starting_state"]["states"][0]["entity_id"] = "alice"
    body["starting_state"]["states"][1]["entity_id"] = "ghost"
    return body


def state_cells(html: str, name: str) -> list[str]:
    section = html[html.index('<section id="states">') :]
    section = section[: section.index("</section>")]
    start = section.index(f"<td><code>{name}</code></td>")
    row = section[start : section.index("</tr>", start)]
    return [c.strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]


def test_the_detail_page_shows_states_and_initial_values(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 200, json=states_detail())
    engine.respond("GET", C_ITEM, 200, json=character_card())
    html = detail_page(operator_client, engine)
    gold = state_cells(html, "gold")
    assert gold[1:8] == [
        "Number",
        "Per character",
        "Player visible (default)",
        "Story (default)",
        "gold",
        "100 gold",
        "min 0",
    ]
    assert gold[9] == "<code>alice</code> (role)"
    assert '<td title="character">Per character</td>' in html
    assert "<td>Player visible (default)</td>" in html  # an unset one has no title
    door = state_cells(html, "door_open")
    assert door[3] == "Hidden"
    assert door[6] == "false"
    assert door[9] == "<code>ghost</code> (undeclared)"
    assert "<dt>Silent</dt><dd>Yes</dd>" in html
    bank = state_cells(html, "bank_balance")
    assert text(bank[4]) == "External · bank · balance"
    assert bank[6] == '<span class="undefined">undefined</span>'
    assert bank[8] == "coins"
    assert "<dt>Reservation expiry</dt><dd>30 min</dd>" in html
    # The roles' initial values, by D5 with the State's type and unit.
    lines = re.findall(r'<dd class="initial-values">(.*?)</dd>', html, flags=re.S)
    assert lines == ["gold: 250 gold", "mood: &quot;tense&quot; (undeclared State)"]
    # "Deferred" no longer lists the States.
    deferred = html[html.index('<section id="deferred">') :]
    assert "<dt>States</dt>" not in deferred
    # Complement: the public card shows none of it.
    engine.reset()
    engine.respond("GET", S_ITEM, 200, json=card_of(states_detail()))
    html = detail_page(operator_client, engine, "?projection=card")
    assert 'id="states"' not in html
    assert "Initial State values" not in html


def test_the_state_table_lines() -> None:
    from django.template.loader import render_to_string

    from console.catalog.cast_table import initial_value_lines, state_rows
    from console.engine.models import Scenario, ScenarioRole

    body = states_detail()
    # Deliberately off-schema: an entry missing ``type`` (shown raw), one with every bound.
    body["starting_state"]["states"].append({"name": "broken"})
    body["starting_state"]["states"][0]["constraints"] = {
        "min": 0,
        "max": 100,
        "non_negative": True,
        "tolerance": 2,
    }
    scenario = Scenario.from_json(
        {**body, "starting_state": {**body["starting_state"], "states": []}}
    )
    assert scenario is not None
    rows = state_rows(body["starting_state"]["states"], scenario.cast, scenario)
    assert rows[0].constraints == "min 0 · max 100 · non-negative · tolerance 2"
    enum = state_rows(
        [{"name": "w", "type": "enum", "constraints": {"allowed_values": ["rain", "sun"]}}],
        scenario.cast,
        scenario,
    )
    assert enum[0].constraints == "one of rain, sun"
    html = render_to_string("console/catalog/scenarios/_state_table.html", {"states": rows})
    assert (
        '<tr class="state-raw"><td colspan="11"><pre>{&quot;name&quot;: &quot;broken&quot;}</pre>'
        in html
    )
    bob = ScenarioRole.from_json(
        {
            "id": "bob",
            "public_label": "Bob",
            "setup_description": "x",
            "kind": "npc",
            "state_initial_values": {"door_open": True, "gold": 1200},
        }
    )
    assert bob is not None
    assert initial_value_lines(bob, body["starting_state"]["states"]) == [
        "door_open: true (not a per-character State)",
        "gold: 1,200 gold",
    ]


# --- #268: the starting clock and the scheduled events -------------------------------------------


def clock_detail(**changes: Any) -> dict[str, Any]:
    body = fixture("draft_clock", **changes)
    body["starting_state"]["scheduled_events"][0]["participants"].append("ghost")
    return body


def section_of(html: str, section_id: str) -> str:
    section = html[html.index(f'<section id="{section_id}">') :]
    return section[: section.index("</section>")]


def test_the_detail_page_shows_the_clock_and_events(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 200, json=clock_detail())
    engine.respond("GET", C_ITEM, 200, json=character_card())
    html = detail_page(operator_client, engine)
    clock = section_of(html, "clock")
    page = text(clock)
    assert "Anchor year 2026 · month 3 · day 14 · Saturday · 06:00 · band morning" in page
    assert '<dd class="mode" title="manual">Manual</dd>' in clock
    assert '<dd class="time-awareness" title="approximate">Approximate</dd>' in clock
    assert '<dd class="skip">90 min</dd>' in clock
    assert '<dd class="stop">No</dd>' in clock
    assert "<tr><td>50</td><td>no end</td><td>15</td></tr>" in clock
    assert "<tr><td>night</td><td>00:00</td><td>06:00</td></tr>" in clock
    assert "<summary>Custom calendar</summary>" in clock
    assert "Moonday" in clock
    events = section_of(html, "scheduled-events")
    rows = re.findall(r'<tr class="event-row">(.*?)</tr>', events, flags=re.S)
    assert [text(r) for r in rows] == [
        "the dinner at 18:00 — world bob , alice , ghost (undeclared) "
        "The farmhouse ( farmhouse ) — —",
        "the letter arrives +02h 10m — alice — — alice, family (group) —",
        "the heist at 02:10 bob — — — — Secret",
    ]
    assert '<td class="planned" title="130 min">+02h 10m</td>' in events
    assert '<td class="planned">at 18:00</td>' in events
    assert '<td class="disclosure" title="secret">Secret</td>' in events
    # Order: after the States, before "Rules and declarations", which lists only the three.
    assert html.index('<section id="states">') < html.index('<section id="clock">')
    assert html.index('<section id="scheduled-events">') < html.index('<section id="deferred">')
    deferred = section_of(html, "deferred")
    assert re.findall(r"<dt>(.*?)</dt>", deferred) == ["Predicates", "Closed sets", "Rules"]
    # Complement: the public card shows none of it.
    engine.reset()
    engine.respond("GET", S_ITEM, 200, json=card_of(clock_detail()))
    html = detail_page(operator_client, engine, "?projection=card")
    for marker in ('id="clock"', 'id="scheduled-events"', "Rules and declarations"):
        assert marker not in html


@pytest.mark.parametrize(
    ("clock", "anchor", "mode"),
    [
        (None, None, None),
        ({}, None, None),
        ({"mode": "paced"}, "Unanchored (default)", '<dd class="mode" title="paced">Paced</dd>'),
        ({"anchor": {}}, "Unanchored (default)", '<dd class="mode">Paced (default)</dd>'),
    ],
)
def test_the_detail_page_reads_an_unset_clock(
    operator_client: Client,
    engine: FakeEngine,
    clock: dict[str, Any] | None,
    anchor: str | None,
    mode: str | None,
) -> None:
    body = fixture("draft_clock")
    body["starting_state"]["clock"] = clock
    body["starting_state"]["scheduled_events"] = []
    engine.respond("GET", S_ITEM, 200, json=body)
    engine.respond("GET", C_ITEM, 200, json=character_card())
    html = detail_page(operator_client, engine)
    section = section_of(html, "clock")
    if anchor is None:
        assert "<p>—</p>" in section
    else:
        assert f'<dd class="anchor">{anchor}</dd>' in section
        assert mode is not None
        assert mode in section
        assert '<dd class="skip">1,440 min (default)</dd>' in section
        assert '<dd class="stop">Yes (default)</dd>' in section
        assert '<dd class="time-awareness">Exact (default)</dd>' in section
        assert '<dd class="calendar">Gregorian (default)</dd>' in section
        assert text(section).count("Engine default") == 2
    assert "<p>—</p>" in section_of(html, "scheduled-events")


def test_the_clock_and_event_rows() -> None:
    from console.catalog.cast_table import clock_view, event_rows
    from console.engine.models import Scenario

    body = fixture("draft_clock")
    scenario = Scenario.from_json(body)
    assert scenario is not None
    view = clock_view({"max_unprompted_skip_minutes": 2000})
    assert view is not None
    assert view.skip == "2,000 min"
    # Deliberately off-schema: a clock with an extra key and an event missing ``planned`` are
    # shown raw.
    raw = clock_view({"mode": "paced", "colour": "red"})
    assert raw is not None
    assert raw.clock is None
    assert '"colour": "red"' in raw.raw
    (broken,) = event_rows([{"proposition": "x"}], scenario)
    assert broken.event is None
    assert broken.raw == '{"proposition": "x"}'
    (place,) = event_rows([{"proposition": "x", "planned": 0, "place": "cellar"}], scenario)
    assert place.place is not None
    assert place.place.name is None  # "cellar (undeclared)"
