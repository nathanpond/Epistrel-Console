"""The Scene tab and the entity page (#58): a structured story's scene and an entity's standing
profile at a turn and viewpoint.

Runs against the real urlconf on the schema-validating fake Engine. The story is
``story_json(next_turn_index=4)``: head 3, cast Mara (``T3A``, the player, the story default
viewpoint) and Tobin (``T3B``). Fixtures in ``tests/fixtures/inspect/``:

- ``scene_omniscient.json``: turn 3 · v2, at The cellar (``CE1``, place), present Mara (``MM1``),
  a lantern (``NT1``) and Tobin (``TB1``), in id order;
- ``scene_empty.json``: no location, nobody present; ``scene_turn0.json``: turn 0, variant null;
- ``entity_profile_omniscient.json``: Tobin at turn 3. "knows" Mara (out; qualifiers a string,
  a number, a ULID, a list and a boolean; fact ``FA1``), "distrusts" from an unnamed ``GH1``
  (in; no qualifiers; no fact); at The cellar within The inn ⊂ Harbour town (``FC1``); holds a
  brass key (``FD1``) and a coin (no fact);
- ``entity_profile_character.json``: the same as Mara, every ``source_fact_id`` null.
"""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine.models import EntityProfileBody
from console.engine_access import engine_client_for
from console.held import held_text
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, lite, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
MARA, TOBIN = P + "T3A", P + "T3B"  # cast characters
CELLAR, INN, TOWN = P + "CE1", P + "NN1", P + "TW1"
MARA_E, TOBIN_E, LANTERN, KEY, COIN, GHOST = (
    P + s for s in ("MM1", "TB1", "NT1", "KY1", "CN1", "GH1")
)
FA1, FC1, FD1 = P + "FA1", P + "FC1", P + "FD1"
AS_MARA = f"character:{MARA}"
TAB = f"/stories/{STORY}/inspect/scene/"
SCENE = "/stories/{story_id}/scene"
SCENE_PATH = f"/stories/{STORY}/scene"
PROFILE = "/stories/{story_id}/entities/{entity_id}/profile"
FIX = "Fix the form above to read the scene."
NOT_IN_CAST = "That viewpoint isn&#x27;t one of this story&#x27;s cast"


def entity_page(eid: str = TOBIN_E) -> str:
    return f"/stories/{STORY}/inspect/entities/{eid}/"


def fixture(name: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text()))
    body.update(over)
    return body


def profile_body(name: str = "entity_profile_omniscient", **over: Any) -> dict[str, Any]:
    return fixture(name, **over)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=4))
    fake.respond("GET", SCENE, 200, json=fixture("scene_omniscient"))
    fake.respond("GET", PROFILE, 200, json=profile_body())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, url: str = TAB, query: str = "") -> str:
    response = client.get(f"{url}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [
        r for r in engine.requests if r.url.path == SCENE_PATH or r.url.path.endswith("/profile")
    ]


def sent(engine: FakeEngine) -> dict[str, str]:
    (call,) = reads(engine)
    return dict(call.url.params)


def field_errors(html: str, field: str) -> str:
    found = re.search(
        rf'<div class="field field-{field}">(.*?)</div>\s*(?:<div|<button)', html, re.S
    )
    assert found is not None, field
    return found.group(1)


def options(html: str) -> list[tuple[str, str]]:
    select = re.search(r'<select name="viewpoint"[^>]*>(.*?)</select>', html, re.S)
    assert select is not None
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select.group(1))


def selected(html: str) -> str:
    select = re.search(r'<select name="viewpoint"[^>]*>(.*?)</select>', html, re.S)
    assert select is not None
    found = re.search(r'<option value="([^"]*)" selected>', select.group(1))
    assert found is not None
    return found.group(1)


def links(html: str) -> dict[str, str]:
    """Entity id → its link's href (entity links carry the full id in ``title``)."""
    return {
        title: href.replace("&amp;", "&")
        for href, title in re.findall(r'<a class="entity" href="([^"]+)" title="([^"]+)">', html)
    }


def params_of(href: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(href).query)


def section(html: str, name: str) -> str:
    found = re.search(rf'<section class="profile-{name}">(.*?)</section>', html, re.S)
    assert found is not None, name
    return found.group(1)


# --- the form and the params sent -----------------------------------------------------------------


def test_a_first_visit_reads_at_once_with_the_story_default_and_the_head(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert sent(engine) == {"viewpoint": AS_MARA}  # as_of left out: the head
    assert selected(html) == AS_MARA
    assert '<form method="get" action="' + TAB + '" class="scene-form">' in html
    form = re.search(r'<form method="get".*?</form>', html, re.S)
    assert form is not None
    assert "hx-" not in form.group(0)
    assert '<button type="submit">Apply</button>' in html
    assert "blank = head (3)" in html
    assert '<datalist id="scene-turns">' in html
    assert '<option value="3">3 (head)</option>' in html
    assert "In M4 every turn&#x27;s story-time is the story&#x27;s start" in html
    assert "<title>Inspect · Scene · Harbour</title>" in html


def test_the_viewpoint_select_lists_omniscient_then_the_cast(
    operator_client: Client, engine: FakeEngine
) -> None:
    assert options(page(operator_client)) == [
        ("omniscient", "Omniscient"),
        (AS_MARA, "Mara (player)"),
        (f"character:{TOBIN}", "Tobin"),
    ]


def test_duplicate_names_get_their_last_six(operator_client: Client, engine: FakeEngine) -> None:
    story = story_json(next_turn_index=4)
    story["characters"][1]["name"] = "Mara"
    engine.respond("GET", STORY_PATH, 200, json=story)
    labels = [label for _, label in options(page(operator_client))]
    assert labels == ["Omniscient", "Mara (…7R0T3A) (player)", "Mara (…7R0T3B)"]


def test_no_story_default_starts_omniscient(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        STORY_PATH,
        200,
        json=story_json(next_turn_index=4, settings={"default_output_viewpoint": None}),
    )
    html = page(operator_client)
    assert sent(engine) == {"viewpoint": "omniscient"}
    assert selected(html) == "omniscient"


def test_a_default_outside_the_cast_falls_back_to_omniscient(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        STORY_PATH,
        200,
        json=story_json(
            next_turn_index=4, settings={"default_output_viewpoint": f"character:{P}ZZ9"}
        ),
    )
    html = page(operator_client)
    assert sent(engine) == {"viewpoint": "omniscient"}  # not a 422
    assert FIX not in html


def test_a_blank_turn_is_not_sent_and_an_entered_one_is(
    operator_client: Client, engine: FakeEngine
) -> None:
    page(operator_client, query="?as_of=&viewpoint=omniscient")
    assert sent(engine) == {"viewpoint": "omniscient"}
    engine.reset()
    page(operator_client, query=f"?as_of=2&viewpoint=character:{TOBIN}")
    assert sent(engine) == {"as_of": "2", "viewpoint": f"character:{TOBIN}"}


@pytest.mark.parametrize(
    ("query", "field", "message"),
    [
        ("?as_of=4&viewpoint=omniscient", "as_of", "Turn must be 0\u20133"),
        ("?as_of=-1&viewpoint=omniscient", "as_of", "Enter a whole number, 0 or more."),
        ("?as_of=abc&viewpoint=omniscient", "as_of", "Enter a whole number, 0 or more."),
        (f"?viewpoint=character:{P}ZZ9", "viewpoint", NOT_IN_CAST),
    ],
)
@pytest.mark.parametrize("url", [TAB, entity_page()])
def test_a_form_error_makes_no_scene_or_profile_call(
    operator_client: Client, engine: FakeEngine, url: str, query: str, field: str, message: str
) -> None:
    html = page(operator_client, url, query)
    assert reads(engine) == []
    assert message in field_errors(html, field)
    assert f'<p class="fix-form">{FIX}</p>' in html


def test_a_non_ulid_entity_id_is_a_404_with_zero_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    for eid in ("not-an-id", TOBIN_E.lower(), P + "LV3"):
        assert operator_client.get(entity_page(eid)).status_code == 404
    assert engine.requests == []


# --- gating ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [TAB, entity_page()])
def test_a_lite_story_greys_scene_and_entity_with_zero_calls(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    html = page(operator_client, url)
    assert reads(engine) == []
    assert '<div class="panel unavailable">' in html
    assert "Needs the structured pipeline" in html
    assert 'aria-current="page">Scene</span>' in html
    assert "scene-form" not in html


def test_the_entity_page_highlights_the_scene_tab(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, entity_page())
    assert f'<a class="active" aria-current="page" href="{TAB}">Scene</a>' in html
    assert "<title>Inspect · Scene · Harbour</title>" in html


# --- the scene panel ------------------------------------------------------------------------------


def test_the_scene_header_location_and_present(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, query="?viewpoint=omniscient")
    assert '<h2 class="scene-header">Turn 3 · v2 · as Omniscient</h2>' in html
    location = re.search(r'<p class="scene-location">(.*?)</p>', html, re.S)
    assert location is not None
    assert '>The cellar</a> (<span class="kind" title="place">Place</span>)' in location.group(1)
    assert f'<code title="{CELLAR}">…4P7R0CE1</code>' in location.group(1)
    rows = re.findall(r'<tr class="present" data-entity="([^"]+)">', html)
    assert rows == [MARA_E, LANTERN, TOBIN_E]  # the Engine's order
    assert '<span class="kind" title="object">Object</span>' in html
    assert '<details class="more"><summary>More</summary>' in html
    assert f"&quot;entity_id&quot;: &quot;{LANTERN}&quot;" in html


def test_a_character_header_uses_the_cast_label(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("scene_omniscient", viewpoint=AS_MARA)
    body["present"] = [p for p in body["present"] if p["entity_id"] != LANTERN]
    engine.respond("GET", SCENE, 200, json=body)
    html = page(operator_client)
    assert '<h2 class="scene-header">Turn 3 · v2 · as Mara</h2>' in html
    assert LANTERN not in html


def test_turn_zero_drops_the_variant(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SCENE, 200, json=fixture("scene_turn0"))
    html = page(operator_client, query="?as_of=0&viewpoint=omniscient")
    assert '<h2 class="scene-header">Turn 0 · as Omniscient</h2>' in html


def test_an_empty_scene_shows_both_messages(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SCENE, 200, json=fixture("scene_empty"))
    html = page(operator_client)
    assert '<p class="scene-location none">Location unknown to this viewpoint</p>' in html
    assert '<p class="present-none none">None visible to this viewpoint</p>' in html
    assert 'class="engine-error"' not in html


def test_an_unknown_viewpoint_value_in_the_answer_shows_raw(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SCENE, 200, json=fixture("scene_omniscient", viewpoint="narrator"))
    assert "Turn 3 · v2 · as narrator</h2>" in page(operator_client)


def test_scene_links_carry_the_responses_turn_and_viewpoint(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, query="?as_of=&viewpoint=omniscient")  # a blank turn
    found = links(html)
    assert set(found) == {CELLAR, MARA_E, LANTERN, TOBIN_E}
    for eid, href in found.items():
        assert href.startswith(entity_page(eid))
        assert params_of(href) == {"as_of": ["3"], "viewpoint": ["omniscient"]}


def test_an_unnamed_entity_is_never_an_empty_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("scene_omniscient")
    body["present"][0]["name"] = ""
    engine.respond("GET", SCENE, 200, json=body)
    html = page(operator_client)
    assert f'title="{MARA_E}"><span class="muted">(unnamed)</span></a>' in html


# --- the entity page ------------------------------------------------------------------------------


def test_the_entity_header_back_link_and_form(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, entity_page(), "?as_of=&viewpoint=omniscient")
    assert sent(engine) == {"viewpoint": "omniscient"}
    assert (
        '<h2 class="entity-header">Tobin (<span class="kind" title="person">Person</span>) '
        f'<code title="{TOBIN_E}">…4P7R0TB1</code></h2>'
    ) in html
    assert '<p class="context-line">Turn 3 · as Omniscient</p>' in html
    assert f'<a class="scene-back" href="{TAB}?as_of=3&amp;viewpoint=omniscient">← Scene</a>' in (
        html
    )
    assert f'<form method="get" action="{entity_page()}" class="scene-form">' in html


def test_profile_relationships(operator_client: Client, engine: FakeEngine) -> None:
    html = section(page(operator_client, entity_page(), "?viewpoint=omniscient"), "relationships")
    rows = re.findall(r'<tr class="relationship">(.*?)</tr>', html, re.S)
    assert len(rows) == 2
    knows, distrusts = rows
    assert '<span title="out">→</span>' in knows
    assert '<span title="in">←</span>' in distrusts
    assert (
        f'<a href="/stories/{STORY}/inspect/predicates/?layer=all#p-{P}PR1" title="{P}PR1">'
        "knows</a>"
    ) in knows
    assert ">Mara</a>" in knows
    qualifiers = re.search(r'<td class="qualifiers">(.*?)</td>', knows, re.S)
    assert qualifiers is not None
    assert qualifiers.group(1) == (
        "since=<code>childhood</code>, trust=0.8, "
        f'witness=<code title="{MARA_E}">…4P7R0MM1</code>, '
        "tags=[&quot;old&quot;,&quot;close&quot;], secret=true"
    )
    assert '<td class="qualifiers">—</td>' in distrusts
    assert '<td class="valid">[0, …) min</td>' in knows
    assert '<td class="valid">[…, 120) min</td>' in distrusts
    assert f'<a href="/stories/{STORY}/inspect/canon/{FA1}/lineage/">' in knows
    assert '<td class="source-fact">—</td>' in distrusts
    assert '<span class="muted">(unnamed)</span>' in distrusts


def test_an_unknown_direction_renders_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = profile_body()
    body["relationships"][0]["direction"] = "both"
    engine.respond("GET", PROFILE, 200, json=body)
    html = section(page(operator_client, entity_page()), "relationships")
    assert '<td class="direction"><code>both</code></td>' in html


def test_profile_placement_within_chain(operator_client: Client, engine: FakeEngine) -> None:
    html = section(page(operator_client, entity_page(), "?viewpoint=omniscient"), "placement")
    where = re.search(r'<dd class="where">(.*?)</dd>', html, re.S)
    assert where is not None
    text = re.sub(r"<[^>]+>", "", where.group(1))
    assert text == "at The cellar within The inn ⊂ Harbour town"
    assert '<dd class="valid">[0, …) min</dd>' in html
    assert f'<a href="/stories/{STORY}/inspect/canon/{FC1}/lineage/">' in html


def test_placement_without_within_or_in_a_container(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = profile_body()
    body["placement"]["within"] = []
    engine.respond("GET", PROFILE, 200, json=body)
    where = re.search(r'<dd class="where">(.*?)</dd>', page(operator_client, entity_page()), re.S)
    assert where is not None
    assert re.sub(r"<[^>]+>", "", where.group(1)) == "at The cellar"

    body["placement"].update(location=None, container=fixture("scene_omniscient")["present"][0])
    engine.respond("GET", PROFILE, 200, json=body)
    where = re.search(r'<dd class="where">(.*?)</dd>', page(operator_client, entity_page()), re.S)
    assert where is not None
    assert re.sub(r"<[^>]+>", "", where.group(1)) == "in Mara"


def test_profile_inventory(operator_client: Client, engine: FakeEngine) -> None:
    html = section(page(operator_client, entity_page(), "?viewpoint=omniscient"), "inventory")
    rows = re.findall(r'<tr class="inventory-item">(.*?)</tr>', html, re.S)
    assert len(rows) == 2
    assert ">brass key</a>" in rows[0]
    assert f'<a href="/stories/{STORY}/inspect/canon/{FD1}/lineage/">' in rows[0]
    assert '<td class="source-fact">—</td>' in rows[1]


def test_empty_sections_say_none_visible(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", PROFILE, 200, json=profile_body(relationships=[], placement=None, inventory=[])
    )
    html = page(operator_client, entity_page())
    for name in ("relationships", "placement", "inventory"):
        assert '<p class="none">None visible to this viewpoint</p>' in section(html, name)


def test_a_character_viewpoint_shows_no_source_facts(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE, 200, json=profile_body("entity_profile_character"))
    html = page(operator_client, entity_page())
    assert sent(engine) == {"viewpoint": AS_MARA}
    assert "Source fact" not in html
    assert "source-fact" not in html
    assert "/lineage/" not in html
    assert '<p class="context-line">Turn 3 · as Mara</p>' in html


def test_the_omniscient_viewpoint_shows_source_facts(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, entity_page(), "?viewpoint=omniscient")
    assert html.count("<th>Source fact</th>") == 2
    assert "<dt>Source fact</dt>" in html
    assert html.count("/lineage/") == 3


def test_the_source_column_shows_for_omniscient_when_every_fact_is_null(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = profile_body("entity_profile_character", viewpoint="omniscient")
    engine.respond("GET", PROFILE, 200, json=body)
    html = page(operator_client, entity_page(), "?viewpoint=omniscient")
    assert html.count("<th>Source fact</th>") == 2
    assert "/lineage/" not in html
    assert '<td class="source-fact">—</td>' in html


def test_profile_links_carry_the_responses_turn_and_viewpoint(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE, 200, json=profile_body("entity_profile_character"))
    html = page(operator_client, entity_page(), "?as_of=")
    found = links(html)
    assert set(found) == {MARA_E, GHOST, CELLAR, INN, TOWN, KEY, COIN}
    for href in found.values():
        assert params_of(href) == {"as_of": ["3"], "viewpoint": [AS_MARA]}


# --- errors ---------------------------------------------------------------------------------------


def test_a_profile_404_says_the_entity_isnt_the_storys(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PROFILE, 404, json=error_json("not_found", "no such entity"))
    html = page(operator_client, entity_page(), "?as_of=2&viewpoint=omniscient")
    assert "That entity isn&#x27;t one of this story&#x27;s" in html
    assert "scene-form" in html
    assert '<h2 class="entity-header">Entity <code title="' in html
    assert ">…4P7R0TB1</code></h2>" in html
    # "← Scene" keeps the submitted values.
    assert f'href="{TAB}?as_of=2&amp;viewpoint=omniscient">← Scene</a>' in html


def test_a_story_404_is_the_story_level_not_found_with_no_scene_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 404, json=error_json("not_found", "no story"))
    html = page(operator_client)
    assert reads(engine) == []
    assert "Not found (it may have been deleted or truncated)" in html


def test_a_failed_story_read_degrades_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, query=f"?as_of=9&viewpoint=character:{TOBIN}")
    assert sent(engine) == {"as_of": "9", "viewpoint": f"character:{TOBIN}"}  # no upper bound
    assert options(html) == [
        ("omniscient", "Omniscient"),
        (f"character:{TOBIN}", f"character:{TOBIN}"),
    ]
    assert '<datalist id="scene-turns">' not in html
    engine.reset()
    page(operator_client)
    assert sent(engine) == {"viewpoint": "omniscient"}
    engine.reset()
    html = page(operator_client, query="?as_of=-2")
    assert reads(engine) == []


@pytest.mark.parametrize("url", [TAB, entity_page()])
def test_a_422_on_as_of_lands_beside_turn(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    route = SCENE if url == TAB else PROFILE
    engine.respond(
        "GET", route, 422, json=error_json("validation_error", "past", field="query.as_of", head=2)
    )
    html = page(operator_client, url, "?as_of=3&viewpoint=omniscient")
    assert "Turn must be 0\u20132" in field_errors(html, "as_of")
    assert "scene-form" in html


@pytest.mark.parametrize("url", [TAB, entity_page()])
def test_viewpoint_unresolved_lands_beside_viewpoint(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    route = SCENE if url == TAB else PROFILE
    engine.respond(
        "GET",
        route,
        422,
        json=error_json("viewpoint_unresolved", "no", field="query.viewpoint"),
    )
    html = page(operator_client, url, f"?viewpoint=character:{TOBIN}")
    assert NOT_IN_CAST in field_errors(html, "viewpoint")
    assert "scene-form" in html


def test_another_422_lands_above_the_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SCENE, 422, json=error_json("validation_error", "odd request"))
    html = page(operator_client)
    assert '<div class="errors" role="alert">' in html
    assert "odd request" in html


@pytest.mark.parametrize("url", [TAB, entity_page()])
def test_a_503_is_the_error_panel_below_the_form(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    route = SCENE if url == TAB else PROFILE
    engine.respond("GET", route, 503, json=error_json("ledger_unavailable", "down"))
    html = page(operator_client, url)
    assert "Engine error 503 ledger_unavailable" in html
    assert html.index("scene-form") < html.index("Engine error 503")


@pytest.mark.parametrize("url", [TAB, entity_page()])
def test_erasure_pending_is_the_held_wrapper_with_no_form(
    operator_client: Client, engine: FakeEngine, url: str
) -> None:
    route = SCENE if url == TAB else PROFILE
    engine.respond(
        "GET", route, 503, json=error_json("erasure_pending", "held"), headers={"Retry-After": "30"}
    )
    response = operator_client.get(url)
    assert response.status_code == 503
    html = response.content.decode()
    assert held_text("30") in html
    assert "scene-form" not in html


# --- the partial on its own (#152 includes it) ----------------------------------------------------


def test_the_profile_partial_renders_standalone() -> None:
    profile = EntityProfileBody.from_json(profile_body())
    assert profile is not None
    html = render_to_string(
        "console/inspect/_entity_profile.html",
        {"profile": profile, "story_id": STORY, "link_as_of": 3, "link_viewpoint": "omniscient"},
    )
    found = links(html)
    assert set(found) == {MARA_E, GHOST, CELLAR, INN, TOWN, KEY, COIN}
    for href in found.values():
        assert params_of(href) == {"as_of": ["3"], "viewpoint": ["omniscient"]}
    assert "<h2>Relationships</h2>" in html
    assert "<h2>Placement</h2>" in html
    assert "<h2>Inventory</h2>" in html
