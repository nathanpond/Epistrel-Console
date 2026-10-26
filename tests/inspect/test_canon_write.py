"""Commit, lock, unlock and supersede author canon facts (#146).

Runs against the real urlconf on the schema-validating fake Engine. Fixtures:

- ``predicates_write.json`` (revision 3): ``located_at`` (``PW1``, place, functional,
  ``frame: str`` (key) and ``since: int``), ``holds`` (``PW2``, item, sets), ``age`` (``PW3``,
  an ``int`` literal), ``alive`` (``PW4``, no object), ``tagged`` (``PW5``, auto: any object,
  sets, undeclared qualifiers), ``measured`` (``PW6``, a ``quantity`` literal, one qualifier of
  each kind) and ``noted`` (``PW7``, any object, no qualifiers);
- ``facts_write.json``: the version ``FW1``, ``located_at(Tobin, …PA1)`` with ``frame=inside``,
  ``since=3``, valid ``[10 (inner 12), 40 (inner 38))``, ``characters`` visibility (Mara),
  locked, anchored to ``FB1``, event ``EV1``;
- ``fact_written.json``: the written ``FW2`` (revision 8), superseding ``FB1``, an author
  memory, an ``uncertain`` and a ``contradicted`` warning.

The Canon tab's own page is #137's ``facts_list.json``; the cast is Mara (player) and Tobin."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.canon import (
    SNAPSHOT_EXPIRED,
    literal_value,
    object_forms,
    qualifier_value,
)
from console.models import EngineOperation
from console.views import inspect as views
from console.views import inspect_canon
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, lite, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
LOC, HOLDS, AGE, ALIVE, TAGGED, MEASURED, NOTED = (
    P + s for s in ("PW1", "PW2", "PW3", "PW4", "PW5", "PW6", "PW7")
)
MARA, TOBIN = P + "T3A", P + "T3B"
PA1, PC1 = P + "PA1", P + "PC1"
FW1, FW2, FB1, FA1, FC1 = P + "FW1", P + "FW2", P + "FB1", P + "FA1", P + "FC1"
KEY = P + "K01"
TAB = f"/stories/{STORY}/inspect/canon/"
NEW = f"/stories/{STORY}/inspect/canon/new/"
WRITTEN = f"/stories/{STORY}/inspect/canon/written/"
FACTS = "/admin/stories/{story_id}/facts"
FACTS_PATH = f"/admin/stories/{STORY}/facts"
LOCK = "/admin/stories/{story_id}/facts/{fact_id}/lock"
SUPERSEDE = "/admin/stories/{story_id}/facts/{fact_id}/supersede"
PREDICATES = "/stories/{story_id}/predicates"
PREDICATES_PATH = f"/stories/{STORY}/predicates"
NOT_IN_TIMELINE = "That fact version isn't in the active timeline (it may have been truncated)"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


def written(**over: Any) -> dict[str, Any]:
    body = fixture("fact_written")
    body.update(over)
    return body


def version(**over: Any) -> dict[str, Any]:
    found: dict[str, Any] = fixture("facts_write")["facts"][0]
    found.update(over)
    return found


def page(*facts: dict[str, Any], revision: int = 7) -> dict[str, Any]:
    return {"facts": list(facts), "next_cursor": None, "timeline_revision": revision}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(inspect_canon, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    fake.respond("GET", PREDICATES, 200, json=fixture("predicates_write"))
    fake.respond("GET", FACTS, 200, json=fixture("facts_write"))
    fake.respond("POST", FACTS, 201, json=written())
    fake.respond("POST", SUPERSEDE, 201, json=written())
    fake.respond("POST", LOCK, 200, json=written(superseded=[FW1], memory_id=None, warnings=[]))
    fake.respond("DELETE", LOCK, 200, json=written(superseded=[FW1], memory_id=None, warnings=[]))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method in ("POST", "DELETE")]


def sent(engine: FakeEngine) -> dict[str, Any]:
    (call,) = writes(engine)
    body: dict[str, Any] = json.loads(call.content)
    return body


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.tags, str(m)) for m in get_messages(response.wsgi_request)]


def text(html: str) -> str:
    joined = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return unescape(joined.replace(" ,", ","))


def field_errors(html: str) -> list[str]:
    return [text(e) for e in re.findall(r'<ul class="errorlist[^"]*"[^>]*>(.*?)</ul>', html, re.S)]


def non_field(html: str) -> list[str]:
    found = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    return (
        [text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", found.group(1), re.S)] if found else []
    )


def get(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


ENTITY_FORM = {
    "subject": TOBIN.lower(),
    "predicate": LOC,
    "object_form": "entity",
    "object_id": f" {PA1} ",
    "polarity": "affirmed",
    "scope": "objective",
    "q-TOTAL_FORMS": "2",
    "q-0-key": "frame",
    "q-0-type": "str",
    "q-0-value": "",
    "q-1-key": "since",
    "q-1-type": "int",
    "q-1-value": "",
    "idempotency_key": KEY,
}


def commit(client: Client, predicate: str = LOC, **over: str) -> Any:
    data = {**ENTITY_FORM, "predicate": predicate, **over}
    if predicate != LOC:
        data.update({"q-TOTAL_FORMS": "0"})
        for name in ("q-0-key", "q-0-type", "q-0-value", "q-1-key", "q-1-type", "q-1-value"):
            data.pop(name, None)
        data.update({k: v for k, v in over.items() if k.startswith("q-")})
    return client.post(f"{NEW}?predicate={predicate}", data)


def no_interval() -> dict[str, Any]:
    return {"kind": "interval", "start": None, "end": None}


# --- pure helpers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "typed", "value"),
    [
        ("int", "-42", -42),
        ("int", "4.0", None),
        ("int", "1_000", None),
        ("duration", "+90", 90),
        ("float", "3", 3.0),
        ("float", "nan", None),
        ("float", "inf", None),
        ("bool", "TRUE", True),
        ("bool", "False", False),
        ("bool", "yes", None),
        ("date", "20261024", "2026-10-24"),
        ("date", "2026-13-01", None),
        ("str", "  as typed ", "  as typed "),
        ("quantity", "3", 3),
        ("quantity", "2.5", 2.5),
        ("quantity", "lots", None),
    ],
)
def test_literal_values_coerce_by_kind(kind: str, typed: str, value: object) -> None:
    got = literal_value(kind, typed)
    assert got == value
    assert type(got) is type(value)


@pytest.mark.parametrize(
    ("kind", "typed", "value"),
    [
        ("str", "a b", "a b"),
        ("int", "7", 7),
        ("int", "7.5", None),
        ("float", "7", 7.0),
        ("bool", "true", True),
        ("date", "2026-10-24", "2026-10-24"),
        ("id", f" {PA1.lower()} ", PA1),
        ("id", "not-an-id", None),
        ("ids", f"{PA1}, {PC1.lower()} ,", [PA1, PC1]),
        ("ids", " , ", None),
        ("ids", f"{PA1}, nope", None),
    ],
)
def test_qualifier_values_follow_the_engines_kinds(kind: str, typed: str, value: object) -> None:
    got = qualifier_value(kind, typed)
    assert got == value
    assert type(got) is type(value)


# --- the Canon tab's link and row actions --------------------------------------------------------


def canon_engine(engine: FakeEngine) -> None:
    """The Canon tab's own fixtures (#137's page and registry)."""
    engine.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    engine.respond("GET", FACTS, 200, json=fixture("facts_list"))


def fact_row(html: str, fid: str) -> str:
    found = re.search(rf'<tr id="f-{fid}" class="[^"]*">(.*?)\n</tr>', html, re.S)
    assert found is not None, fid
    return found.group(1)


def actions(row_html: str) -> str:
    found = re.search(r'<td class="actions">(.*?)</td>', row_html, re.S)
    return text(found.group(1)) if found else ""


def test_the_canon_tab_links_new_fact_with_its_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    canon_engine(engine)
    html = get(operator_client, f"{TAB}?subject={TOBIN.lower()}&predicate={P}PR1")
    href = re.search(r'<a class="button new-fact" href="([^"]+)">New fact</a>', html)
    assert href is not None
    query = parse_qs(urlsplit(href.group(1).replace("&amp;", "&")).query)
    assert query == {"subject": [TOBIN], "predicate": [P + "PR1"]}


def test_current_rows_get_lock_or_unlock_and_supersede(
    operator_client: Client, engine: FakeEngine
) -> None:
    canon_engine(engine)
    html = get(operator_client, TAB)
    assert "<th>Actions</th>" in html
    assert actions(fact_row(html, FB1)) == "Lock Supersede…"
    assert actions(fact_row(html, FC1)) == "Unlock Supersede…"  # author-locked
    link = re.search(r'class="button fact-supersede" href="([^"]+)"', fact_row(html, FB1))
    assert link is not None
    target = urlsplit(link.group(1).replace("&amp;", "&"))
    assert target.path == f"/stories/{STORY}/inspect/canon/{FB1}/supersede/"
    assert parse_qs(target.query) == {"next": [TAB]}


def test_two_rows_never_share_a_key(operator_client: Client, engine: FakeEngine) -> None:
    canon_engine(engine)
    html = get(operator_client, TAB)
    keys = re.findall(r'/(?:lock|unlock)/">.*?name="idempotency_key" value="([^"]+)"', html, re.S)
    assert len(keys) == 4
    assert len(set(keys)) == 4


def test_superseded_and_unconfirmed_rows_have_no_actions_and_an_undone_one_has(
    operator_client: Client, engine: FakeEngine
) -> None:
    canon_engine(engine)
    current_set = page(*(f for f in fixture("facts_list")["facts"] if f["id"] == FC1))
    engine.respond_sequence(
        "GET",
        FACTS,
        [
            {"status": 200, "json": fixture("facts_list")},
            {"status": 200, "json": current_set},
        ],
    )
    html = get(operator_client, f"{TAB}?include_superseded=on")
    assert actions(fact_row(html, FA1)) == ""  # superseded
    assert "current (closure undone)" in fact_row(html, FC1)
    assert actions(fact_row(html, FC1)) == "Unlock Supersede…"
    assert actions(fact_row(html, FB1)) == "Lock Supersede…"

    engine.respond_sequence(
        "GET",
        FACTS,
        [
            {"status": 200, "json": fixture("facts_list")},
            {"status": 503, "json": error_json("database_unavailable")},
        ],
    )
    html = get(operator_client, f"{TAB}?include_superseded=on")
    assert "closed · not confirmed" in fact_row(html, FC1)
    assert actions(fact_row(html, FC1)) == ""


def test_no_row_has_actions_as_recorded_at(operator_client: Client, engine: FakeEngine) -> None:
    canon_engine(engine)
    html = get(operator_client, f"{TAB}?as_of_record_time=2026-10-24T15:00")
    assert 'class="actions"' not in html
    assert "<th>Actions</th>" not in html


def test_the_lineage_view_offers_actions_on_its_current_versions_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", FACTS, 200, json=fixture("facts_lineage"))
    kv3, kv5, kv7 = P + "KV3", P + "KV5", P + "KV7"
    url = f"/stories/{STORY}/inspect/canon/{kv3}/lineage/?subject={TOBIN}&predicate={P}PR1"
    html = get(operator_client, url)
    assert "<th>Actions</th>" in html
    assert actions(fact_row(html, kv5)) == "Lock Supersede…"
    assert actions(fact_row(html, kv3)) == ""  # superseded
    assert actions(fact_row(html, kv7)) == ""
    link = re.search(r'class="button fact-supersede" href="([^"]+)"', fact_row(html, kv5))
    assert link is not None
    assert parse_qs(urlsplit(link.group(1).replace("&amp;", "&")).query)["next"] == [url]


# --- the new-fact page ---------------------------------------------------------------------------


def test_the_predicate_comes_first(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"{NEW}?subject={TOBIN}")
    assert "Use this predicate" in html
    assert '<option value="' + LOC + '">located_at (core)</option>' in html
    assert 'name="subject" value="' + TOBIN + '"' in html  # carried by the choice form
    assert "Choose a predicate above to fill in the fact." in html
    assert 'class="fact-form"' not in html
    assert not [r for r in engine.requests if r.url.path == FACTS_PATH]


def radios(html: str) -> list[str]:
    return re.findall(r'name="object_form" value="([^"]+)"', html)


@pytest.mark.parametrize(
    ("predicate", "offered"),
    [
        (LOC, ["entity"]),
        (HOLDS, ["entity", "set"]),
        (AGE, ["literal"]),
        (ALIVE, ["none"]),
        (TAGGED, ["entity", "literal", "set"]),
        (NOTED, ["entity", "literal"]),
    ],
)
def test_the_object_forms_follow_the_predicate(
    operator_client: Client, engine: FakeEngine, predicate: str, offered: list[str]
) -> None:
    html = get(operator_client, f"{NEW}?predicate={predicate}")
    assert radios(html) == offered


def test_a_declared_predicate_gets_exactly_its_qualifier_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, f"{NEW}?predicate={LOC}&subject={TOBIN}")
    assert 'name="q-0-key" value="frame"' in html
    assert 'name="q-1-key" value="since"' in html
    assert 'type="hidden" name="q-0-key"' in html  # fixed
    assert 'name="add"' not in html
    assert f'name="subject" value="{TOBIN}"' in html
    html = get(operator_client, f"{NEW}?predicate={HOLDS}")
    assert "This predicate declares no qualifiers" in html


def test_an_undeclared_predicate_gets_free_rows_with_add_and_remove(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, f"{NEW}?predicate={TAGGED}")
    assert '<input type="text" name="q-0-key" value=""' in html
    assert 'name="add"' in html
    for kind in ("str", "int", "float", "bool", "date", "id", "ids"):
        assert f'<option value="{kind}"' in html
    response = commit(
        operator_client,
        TAGGED,
        **{"q-TOTAL_FORMS": "1", "q-0-key": "colour", "q-0-type": "str", "add": "1"},
    )
    html = response.content.decode()
    assert 'name="q-1-key"' in html
    assert 'name="q-0-key" value="colour"' in html
    assert not writes(engine)


def test_a_literal_predicate_presets_the_kind(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"{NEW}?predicate={AGE}")
    assert '<option value="int" selected>int</option>' in html


def test_without_the_registry_the_predicate_is_free_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable"))
    html = get(operator_client, f"{NEW}?predicate={LOC}")
    assert "Predicate names unavailable — enter a predicate id" in html
    assert f'<input type="text" name="predicate" value="{LOC}"' in html
    assert radios(html) == ["entity", "literal", "set", "none"]
    assert 'name="add"' in html


def test_the_characters_select_lists_the_cast_with_the_player_marked(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, f"{NEW}?predicate={LOC}")
    assert f'<option value="{MARA}">Mara (player)</option>' in html
    assert f'<option value="{TOBIN}">Tobin</option>' in html
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable"))
    html = get(operator_client, f"{NEW}?predicate={LOC}")
    assert '<textarea name="characters"' in html


def test_a_greyed_canon_tab_greys_the_new_page_with_no_calls(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    html = get(operator_client, f"{NEW}?predicate={LOC}")
    assert "Not available for this story" in html
    assert not [r for r in engine.requests if r.url.path in (FACTS_PATH, PREDICATES_PATH)]
    response = commit(operator_client)
    assert "Not available for this story" in response.content.decode()
    assert not writes(engine)


# --- the request the structured form builds ------------------------------------------------------


def test_an_entity_fact_builds_the_exact_request(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = commit(operator_client, **{"q-0-value": "inside", "q-1-value": "3"})
    assert response.status_code == 200
    assert sent(engine) == {
        "proposition": {
            "subject_id": TOBIN,
            "predicate_id": LOC,
            "object_id": PA1,
            "polarity": "affirmed",
            "qualifiers": {"frame": "inside", "since": 3},
            "valid_time": no_interval(),
        },
        "visibility": {"scope": "objective"},
        "locked": False,
    }
    (call,) = writes(engine)
    assert call.headers["Idempotency-Key"] == KEY


def test_valid_time_is_always_sent_in_the_proposition(
    operator_client: Client, engine: FakeEngine
) -> None:
    commit(operator_client, valid_from="10", valid_from_inner="12", valid_to="-40")
    body = sent(engine)
    assert body["proposition"]["valid_time"] == {
        "kind": "interval",
        "start": {"inner": 12, "outer": 10},
        "end": {"inner": -40, "outer": -40},
    }
    assert "valid_from" not in body
    assert "valid_to" not in body


@pytest.mark.parametrize(
    ("kind", "typed", "unit", "literal"),
    [
        ("int", "42", "", {"kind": "int", "value": 42}),
        ("float", "3", "", {"kind": "float", "value": 3.0}),
        ("str", " Rose ", "", {"kind": "str", "value": " Rose "}),
        ("bool", "True", "", {"kind": "bool", "value": True}),
        ("date", "20261024", "", {"kind": "date", "value": "2026-10-24"}),
        ("duration", "90", "", {"kind": "duration", "value": 90}),
        ("quantity", "2.5", " kg ", {"kind": "quantity", "value": 2.5, "unit": "kg"}),
    ],
)
def test_each_literal_kind_builds_its_literal(
    operator_client: Client,
    engine: FakeEngine,
    kind: str,
    typed: str,
    unit: str,
    literal: dict[str, Any],
) -> None:
    commit(
        operator_client,
        NOTED,
        object_form="literal",
        object_id="",
        literal_kind=kind,
        literal_value=typed,
        literal_unit=unit,
    )
    proposition = sent(engine)["proposition"]
    assert proposition["literal"] == literal
    assert json.dumps(proposition["literal"]["value"]) == json.dumps(literal["value"])
    assert "object_id" not in proposition


def test_a_set_is_trimmed_upper_cased_sorted_and_de_duplicated(
    operator_client: Client, engine: FakeEngine
) -> None:
    commit(
        operator_client,
        HOLDS,
        object_form="set",
        object_id="",
        object_set=f"{PC1.lower()}\n\n  {PA1} \n{PC1}",
    )
    assert sent(engine)["proposition"]["object_set"] == [PA1, PC1]


def test_a_one_member_set_stays_a_set(operator_client: Client, engine: FakeEngine) -> None:
    commit(operator_client, HOLDS, object_form="set", object_id="", object_set=PA1)
    assert sent(engine)["proposition"]["object_set"] == [PA1]


def test_an_object_less_predicate_sends_no_object(
    operator_client: Client, engine: FakeEngine
) -> None:
    commit(operator_client, ALIVE, object_form="none", object_id="", polarity="negated")
    proposition = sent(engine)["proposition"]
    assert proposition == {
        "subject_id": TOBIN,
        "predicate_id": ALIVE,
        "polarity": "negated",
        "valid_time": no_interval(),
    }


def test_each_qualifier_kind_is_sent_as_the_engine_expects(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = {
        "s": "a b",
        "i": "-7",
        "f": "3",
        "b": "FALSE",
        "d": "20261024",
        "one": PA1.lower(),
        "many": f"{PC1}, {PA1.lower()}",
    }
    posted = {"q-TOTAL_FORMS": str(len(rows))}
    for index, (key, value) in enumerate(rows.items()):
        posted.update({f"q-{index}-key": key, f"q-{index}-type": "str", f"q-{index}-value": value})
    commit(
        operator_client,
        MEASURED,
        object_form="literal",
        object_id="",
        literal_kind="quantity",
        literal_value="3",
        literal_unit="m",
        **posted,
    )
    proposition = sent(engine)["proposition"]
    assert proposition["literal"] == {"kind": "quantity", "value": 3, "unit": "m"}
    assert proposition["qualifiers"] == {
        "s": "a b",
        "i": -7,
        "f": 3.0,
        "b": False,
        "d": "2026-10-24",
        "one": PA1,
        "many": [PC1, PA1],
    }
    assert '"f": 3.0' in json.dumps(proposition["qualifiers"])


def test_visibility_characters_anchor_and_lock(operator_client: Client, engine: FakeEngine) -> None:
    client = operator_client
    data = {**ENTITY_FORM, "scope": "characters", "locked": "on", "anchored_to": FB1.lower()}
    client.post(f"{NEW}?predicate={LOC}", {**data, "characters": [TOBIN, MARA, TOBIN]})
    body = sent(engine)
    assert body["visibility"] == {"scope": "characters", "characters": [MARA, TOBIN]}
    assert body["locked"] is True
    assert body["anchored_to"] == FB1


def test_characters_under_another_scope_are_dropped(
    operator_client: Client, engine: FakeEngine
) -> None:
    operator_client.post(
        f"{NEW}?predicate={LOC}", {**ENTITY_FORM, "scope": "public", "characters": [MARA]}
    )
    body = sent(engine)
    assert body["visibility"] == {"scope": "public"}
    assert "anchored_to" not in body


def test_json_mode_sends_the_box_verbatim_and_skips_the_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    box = {
        "proposition": {
            "subject_id": TOBIN,
            "predicate_id": LOC,
            "object_id": PA1,
            "valid_time": {"kind": "instant", "at": 5, "until": 6, "granularity": "minute"},
            "event_id": P + "EV9",
        },
        "valid_from": 600,
    }
    commit(operator_client, subject="not an id", object_id="", json_body=json.dumps(box))
    assert sent(engine) == box


def test_a_json_box_that_does_not_parse_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = commit(operator_client, json_body="{nope")
    assert "Enter valid JSON" in response.content.decode()
    assert not writes(engine)


# --- local validation: zero calls ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("predicate", "over", "message"),
    [
        (LOC, {"subject": "01JB2Z8Y5Q3W6K9M1N4P7R0TIL"}, "Enter a 26-character id"),
        (HOLDS, {"object_set": PC1}, "Only the entity object is sent"),
        (ALIVE, {"object_form": "none"}, "alive takes no object"),
        (
            NOTED,
            {
                "object_form": "literal",
                "object_id": "",
                "literal_kind": "int",
                "literal_value": "4.5",
            },
            "Enter a whole number",
        ),
        (
            NOTED,
            {
                "object_form": "literal",
                "object_id": "",
                "literal_kind": "int",
                "literal_value": "4",
                "literal_unit": "kg",
            },
            "Only a quantity has a unit",
        ),
        (
            MEASURED,
            {
                "object_form": "literal",
                "object_id": "",
                "literal_kind": "quantity",
                "literal_value": "4",
            },
            "A quantity needs a unit",
        ),
        (
            AGE,
            {
                "object_form": "literal",
                "object_id": "",
                "literal_kind": "float",
                "literal_value": "4",
            },
            "age takes a int literal",
        ),
        (LOC, {"scope": "characters"}, "Pick at least one character"),
        (
            TAGGED,
            {
                "q-TOTAL_FORMS": "2",
                "q-0-key": "colour",
                "q-0-type": "str",
                "q-0-value": "red",
                "q-1-key": "colour",
                "q-1-type": "str",
                "q-1-value": "blue",
            },
            "Each qualifier key once",
        ),
        (
            TAGGED,
            {"q-TOTAL_FORMS": "1", "q-0-key": "", "q-0-type": "str", "q-0-value": "red"},
            "Give this value a key",
        ),
        (
            LOC,
            {"q-TOTAL_FORMS": "3", "q-2-key": "zone", "q-2-value": "x"},
            "'zone' is not a qualifier of located_at",
        ),
        (LOC, {"q-1-value": "soon"}, "'since' must be int"),
        (
            HOLDS,
            {"object_form": "set", "object_id": "", "object_set": " \n "},
            "A set needs at least one entity id",
        ),
        (LOC, {"valid_to_inner": "38"}, "Give the outer bound too"),
        (LOC, {"valid_from": "1.5"}, "Enter a whole number of minutes"),
        (LOC, {"anchored_to": "nope"}, "Enter a 26-character id"),
    ],
)
def test_local_validation_failures_make_no_call(
    operator_client: Client,
    engine: FakeEngine,
    predicate: str,
    over: dict[str, str],
    message: str,
) -> None:
    response = commit(operator_client, predicate, **over)
    assert response.status_code == 200
    html = response.content.decode()
    assert any(message in e for e in [*field_errors(html), *non_field(html)]), field_errors(html)
    assert not writes(engine)
    assert f'name="idempotency_key" value="{KEY}"' in html  # no call: the same key


def test_a_free_row_with_an_unknown_type_is_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = commit(
        operator_client,
        TAGGED,
        **{"q-TOTAL_FORMS": "1", "q-0-key": "x", "q-0-type": "blob", "q-0-value": "1"},
    )
    assert "Choose a qualifier type" in response.content.decode()
    assert not writes(engine)


# --- the result page -----------------------------------------------------------------------------


def test_the_result_page_lists_what_the_write_did(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = commit(operator_client)
    html = response.content.decode()
    assert '<h2 class="fact-written">Fact committed</h2>' in html
    assert f'<tr id="f-{FW2}" class="fact-row">' in html
    written_row = fact_row(html, FW2)
    assert ">current<" in written_row
    assert 'class="actions"' not in written_row
    superseded = re.search(r'<dd class="superseded">(.*?)</dd>', html, re.S)
    assert superseded is not None
    lineage = f"/stories/{STORY}/inspect/canon/{FB1}/lineage/?subject={TOBIN}&amp;predicate={LOC}"
    assert f'<a href="{lineage}">' in superseded.group(1)
    assert text(superseded.group(1)).startswith("superseded: 1")
    found = re.search(r'<dd class="warnings">(.*?)</dd>', html, re.S)
    assert found is not None
    warnings = text(found.group(1))
    assert "The check couldn't decide: No rule applies" in warnings
    assert "Contradicted — rules not resolved: Exclusion, Inverse cardinality" in warnings
    assert 'title="inverse_cardinality"' in html
    memory = re.search(r'<dd class="memory">(.*?)</dd>', html, re.S)
    assert memory is not None
    assert f"/stories/{STORY}/inspect/retrieval/?memory={P}MW1" in memory.group(1)
    assert "Revision 8" in html
    own = f"/stories/{STORY}/inspect/canon/{FW2}/lineage/?subject={TOBIN}&amp;predicate={LOC}"
    assert f'<a href="{own}">View lineage</a>' in html
    assert f'<a href="{TAB}">Back to Canon</a>' in html
    row = EngineOperation.objects.get(kind="commit_fact")
    assert (row.entity_type, row.entity_id, row.story_id) == ("story", STORY, STORY)
    assert f"{WRITTEN}?report={row.pk}" in html
    assert f'<a class="operation-link" href="/operations/{row.pk}/"><code>{P}XW1</code></a>' in html


def test_the_result_page_without_memory_superseded_or_warnings(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        FACTS,
        201,
        json=written(
            superseded=[],
            memory_id=None,
            warnings=[{"kind": "uncertain", "reason": None, "rules": []}],
        ),
    )
    html = commit(operator_client).content.decode()
    assert "Superseded: none" in html
    assert "No author memory written" in html
    assert "The check couldn't decide (no reason given)" in html


def test_report_renders_the_result_again_with_the_name_reads(
    operator_client: Client, engine: FakeEngine
) -> None:
    commit(operator_client)
    row = EngineOperation.objects.get(kind="commit_fact")
    engine.requests.clear()
    html = get(operator_client, f"{WRITTEN}?report={row.pk}")
    assert '<h2 class="fact-written">Fact committed</h2>' in html
    assert "Revision 8" in html
    paths = [r.url.path for r in engine.requests]
    assert f"/stories/{STORY}" in paths
    assert PREDICATES_PATH in paths
    assert not writes(engine)
    html = get(operator_client, f"{WRITTEN}?report=999999")
    assert NOT_FOUND in html


# --- Engine refusals of a commit -----------------------------------------------------------------


def refuse(engine: FakeEngine, route: str, status: int, code: str, **details: Any) -> None:
    engine.respond("POST", route, status, json=error_json(code, "Refused by the Engine", **details))


def test_a_validation_error_lands_on_the_fields_it_names(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(
        engine,
        FACTS,
        422,
        "validation_error",
        errors=[
            {"field": "body.proposition.subject_id", "message": "unknown subject"},
            {"field": "body.visibility.characters", "message": "unknown character"},
            {"field": "body.proposition.valid_time.end", "message": "ends before it starts"},
            {"field": "body.something_else", "message": "odd"},
        ],
    )
    html = commit(operator_client).content.decode()
    errors = field_errors(html)
    assert "unknown subject" in errors
    assert "unknown character" in errors
    assert "ends before it starts" in errors
    assert "body.something_else: odd" in non_field(html)
    assert f'name="idempotency_key" value="{KEY}"' not in html  # a fresh key after a refusal


def test_unknown_predicate_lands_on_the_predicate(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, FACTS, 422, "unknown_predicate")
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable"))
    html = commit(operator_client).content.decode()
    assert "Refused by the Engine" in field_errors(html)


def test_json_mode_refusals_land_above_the_box(operator_client: Client, engine: FakeEngine) -> None:
    refuse(
        engine,
        FACTS,
        422,
        "validation_error",
        errors=[{"field": "body.proposition", "message": "bad"}],
    )
    html = commit(operator_client, json_body='{"proposition": {}}').content.decode()
    assert "body.proposition: bad" in field_errors(html)


@pytest.mark.parametrize(
    ("status", "code", "shown"),
    [
        (409, "story_archived", "This story is archived — unarchive it to change its canon"),
        (409, "erased_subject_referenced", "This fact refers to an erased subject"),
        (409, "idempotency_conflict", "That form was already used for another request"),
        (423, "story_busy", "A turn is running — try again"),
    ],
)
def test_shared_refusals_render_above_the_form(
    operator_client: Client, engine: FakeEngine, status: int, code: str, shown: str
) -> None:
    refuse(engine, FACTS, status, code)
    html = commit(operator_client).content.decode()
    assert shown in html
    assert 'class="fact-form"' in html


def test_module_off_on_a_commit_renders_the_greyed_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, FACTS, 409, "module_off", module="pipeline")
    html = commit(operator_client).content.decode()
    assert "Not available for this story" in html
    assert "The story's configuration changed since this page loaded" in html


def test_a_missing_story_is_conventions_not_found_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, FACTS, 404, "not_found")
    html = commit(operator_client).content.decode()
    assert NOT_FOUND in html
    assert 'class="fact-form"' not in html


def test_an_unavailable_engine_renders_the_panel_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, FACTS, 503, "database_unavailable")
    html = commit(operator_client).content.decode()
    assert 'class="engine-error"' in html
    assert 'class="fact-form"' in html


# --- lock and unlock -----------------------------------------------------------------------------


def lock_url(action: str, fid: str = FB1) -> str:
    return f"/stories/{STORY}/inspect/canon/{fid}/{action}/"


def post_lock(client: Client, action: str, fid: str = FB1, **data: str) -> Any:
    return client.post(
        lock_url(action, fid), {"idempotency_key": KEY, "next": f"{TAB}?x=1", **data}
    )


def test_lock_posts_and_flashes_the_new_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post_lock(operator_client, "lock")
    assert response.status_code == 302
    assert response["Location"] == f"{TAB}?x=1"
    (call,) = writes(engine)
    assert (call.method, call.url.path) == ("POST", f"{FACTS_PATH}/{FB1}/lock")
    assert call.content == b""
    assert call.headers["Idempotency-Key"] == KEY
    assert flashes(response) == [("success", "Locked: new version …4P7R0FW2")]
    assert not [r for r in engine.requests if r.url.path.endswith("effective-config")]
    row = EngineOperation.objects.get(kind="lock_fact")
    assert (row.entity_type, row.entity_id, row.story_id) == ("story", STORY, STORY)


def test_unlock_deletes_the_lock(operator_client: Client, engine: FakeEngine) -> None:
    response = post_lock(operator_client, "unlock")
    (call,) = writes(engine)
    assert (call.method, call.url.path) == ("DELETE", f"{FACTS_PATH}/{FB1}/lock")
    assert flashes(response) == [("success", "Unlocked: new version …4P7R0FW2")]


@pytest.mark.parametrize(
    ("action", "method", "shown"),
    [
        ("lock", "POST", "Already locked; nothing changed"),
        ("unlock", "DELETE", "Already unlocked; nothing changed"),
    ],
)
def test_an_unchanged_answer_flashes_nothing_changed(
    operator_client: Client, engine: FakeEngine, action: str, method: str, shown: str
) -> None:
    engine.respond(method, LOCK, 200, json=written(operation_id=None, superseded=[]))
    assert flashes(post_lock(operator_client, action)) == [("info", shown)]


def test_lock_refusals_flash_and_return(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, LOCK, 409, "fact_superseded")
    response = post_lock(operator_client, "lock")
    assert response["Location"] == f"{TAB}?x=1"
    ((level, message),) = flashes(response)
    assert level == "safe error"
    assert "That version was already superseded. Supersede the current one:" in message
    assert f'href="/stories/{STORY}/inspect/canon/{FB1}/lineage/"' in message

    # (Each flash stays queued until a page renders it: the newest comes last.)
    refuse(engine, LOCK, 409, "fact_inactive")
    assert flashes(post_lock(operator_client, "lock"))[-1] == (
        "error",
        "That version's operation is no longer on the timeline",
    )
    refuse(engine, LOCK, 409, "story_archived")
    assert flashes(post_lock(operator_client, "lock"))[-1] == (
        "error",
        "This story is archived — unarchive it to change its canon",
    )


def test_a_safe_flash_renders_its_link(operator_client: Client, engine: FakeEngine) -> None:
    canon_engine(engine)
    refuse(engine, LOCK, 409, "fact_superseded")
    response = post_lock(operator_client, "lock")
    html = operator_client.get(response["Location"]).content.decode()
    assert f'<a class="lineage-link" href="/stories/{STORY}/inspect/canon/{FB1}/lineage/">' in html


def test_lock_of_a_missing_version_returns_to_canon(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", LOCK, 404, json=error_json("fact_not_found"))
    response = post_lock(operator_client, "lock")
    assert response["Location"] == TAB
    assert flashes(response) == [("error", NOT_IN_TIMELINE)]


def test_lock_of_a_missing_story_is_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", LOCK, 404, json=error_json("not_found"))
    response = post_lock(operator_client, "lock")
    assert response.status_code == 200
    assert NOT_FOUND in response.content.decode()


def test_lock_under_module_off_flashes_the_reason(
    operator_client: Client, engine: FakeEngine
) -> None:
    refuse(engine, LOCK, 409, "module_off", module="pipeline")
    response = post_lock(operator_client, "lock")
    ((level, message),) = flashes(response)
    assert level == "error"
    assert message.startswith("Needs the structured pipeline")
    assert "`" not in message


def test_lock_refuses_a_get(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.get(lock_url("lock")).status_code == 405


# --- supersede -----------------------------------------------------------------------------------

SUPERSEDE_URL = f"/stories/{STORY}/inspect/canon/{FW1}/supersede/"


def open_supersede(client: Client, query: str = "") -> str:
    return get(client, f"{SUPERSEDE_URL}{query}")


def fact_form(html: str) -> str:
    found = re.search(r'<form method="post"[^>]*class="fact-form.*?</form>', html, re.S)
    assert found is not None
    return found.group(0)


def hidden(html: str, name: str) -> str:
    found = re.search(rf'type="hidden" name="{name}" value="([^"]*)"', fact_form(html))
    assert found is not None, name
    return found.group(1)


def value(html: str, name: str) -> str:
    found = re.search(rf'name="{name}" value="([^"]*)"', html)
    return found.group(1) if found else ""


def unchanged_post(html: str, **over: str) -> dict[str, Any]:
    """The supersede form posted as rendered (plus ``over``)."""
    data: dict[str, Any] = {
        "snapshot": hidden(html, "snapshot"),
        "idempotency_key": hidden(html, "idempotency_key"),
        "next": hidden(html, "next"),
        "object_form": "entity",
        "object_id": value(html, "object_id"),
        "polarity": "affirmed",
        "literal_kind": "int",
        "q-TOTAL_FORMS": hidden(html, "q-TOTAL_FORMS"),
        "q-0-key": value(html, "q-0-key"),
        "q-0-type": value(html, "q-0-type"),
        "q-0-value": value(html, "q-0-value"),
        "scope": "",
        "locked": "",
        "characters": [MARA],
    }
    for name in ("valid_from", "valid_from_inner", "valid_to", "valid_to_inner"):
        data[name] = value(html, name)
    data.update(over)
    return data


def test_the_supersede_page_is_pre_filled_from_the_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = open_supersede(operator_client, f"?next={TAB}")
    (read,) = [r for r in engine.requests if r.url.path == FACTS_PATH]
    assert read.url.params["include_superseded"] == "true"
    assert read.url.params["limit"] == "1"
    key_part = re.search(r'<dl class="supersede-key">(.*?)</dl>', html, re.S)
    assert key_part is not None
    assert "Tobin" in key_part.group(1)
    assert "located_at" in key_part.group(1)
    assert 'name="subject"' not in html
    assert 'name="predicate"' not in html
    assert "Key qualifiers (can't change on a supersede): frame = inside" in text(html)
    assert 'name="q-0-key" value="since"' in html
    assert value(html, "q-0-value") == "3"
    assert 'name="q-1-key"' not in html
    assert value(html, "object_id") == PA1
    assert (value(html, "valid_from"), value(html, "valid_from_inner")) == ("10", "12")
    assert (value(html, "valid_to"), value(html, "valid_to_inner")) == ("40", "38")
    assert "(carry over: characters: Mara (player))" in html
    assert "(carry over: locked)" in html
    assert "(carries over)" in html
    assert f'<option value="{MARA}" selected>' in html
    assert '<pre class="request-body">' in html
    assert hidden(html, "next") == TAB


def test_an_unchanged_supersede_keeps_the_versions_bounds_and_carries_the_rest(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = open_supersede(operator_client, f"?next={TAB}")
    engine.requests.clear()
    response = operator_client.post(SUPERSEDE_URL, unchanged_post(html, object_id=PC1))
    assert response.status_code == 200
    (call,) = writes(engine)
    assert call.url.path == f"{FACTS_PATH}/{FW1}/supersede"
    assert json.loads(call.content) == {
        "proposition": {
            "subject_id": TOBIN,
            "predicate_id": LOC,
            "object_id": PC1,
            "polarity": "affirmed",
            "qualifiers": {"frame": "inside", "since": 3},
            "valid_time": {
                "kind": "interval",
                "start": {"inner": 12, "outer": 10},
                "end": {"inner": 38, "outer": 40},
            },
            "event_id": P + "EV1",
        }
    }
    result = response.content.decode()
    assert '<h2 class="fact-written">Fact superseded</h2>' in result
    assert f'<a href="{TAB}">Back to Canon</a>' in result
    row = EngineOperation.objects.get(kind="supersede_fact")
    assert (row.entity_type, row.entity_id) == ("story", STORY)


def test_a_supersede_sends_visibility_and_lock_when_chosen(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = open_supersede(operator_client)
    engine.requests.clear()
    operator_client.post(
        SUPERSEDE_URL, unchanged_post(html, scope="public", locked="false", **{"q-0-value": ""})
    )
    body = sent(engine)
    assert body["visibility"] == {"scope": "public"}
    assert body["locked"] is False
    assert body["proposition"]["qualifiers"] == {"frame": "inside"}


def test_a_tampered_snapshot_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = open_supersede(operator_client)
    engine.requests.clear()
    data = unchanged_post(html)
    data["snapshot"] = data["snapshot"][:-2] + "xx"
    response = operator_client.post(SUPERSEDE_URL, data)
    body = response.content.decode()
    assert SNAPSHOT_EXPIRED in body
    assert 'name="object_id"' not in body
    assert engine.requests == []


def test_another_versions_snapshot_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    html = open_supersede(operator_client)
    engine.requests.clear()
    other = f"/stories/{STORY}/inspect/canon/{FB1}/supersede/"
    response = operator_client.post(other, unchanged_post(html))
    assert SNAPSHOT_EXPIRED in response.content.decode()
    assert engine.requests == []


def test_a_new_row_naming_a_key_qualifier_is_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = open_supersede(operator_client)
    engine.requests.clear()
    data = unchanged_post(
        html, **{"q-TOTAL_FORMS": "2", "q-1-key": "frame", "q-1-type": "str", "q-1-value": "out"}
    )
    response = operator_client.post(SUPERSEDE_URL, data)
    assert "Key qualifiers can't change on a supersede" in field_errors(response.content.decode())
    assert not writes(engine)


def closed_version() -> dict[str, Any]:
    return version(tx_to="2026-10-24T16:30:00Z", superseded_by=P + "FX1")


def test_a_closed_version_that_is_still_current_gets_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        FACTS,
        [
            {"status": 200, "json": page(closed_version())},
            {"status": 200, "json": page(closed_version())},
        ],
    )
    html = open_supersede(operator_client)
    assert "current (closure undone)" in html
    assert 'class="fact-form supersede-form"' in html
    reads = [r for r in engine.requests if r.url.path == FACTS_PATH]
    assert len(reads) == 2
    assert reads[1].url.params["subject"] == TOBIN
    assert reads[1].url.params["predicate"] == LOC


def test_a_closed_version_found_superseded_gets_the_message_and_no_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        FACTS,
        [{"status": 200, "json": page(closed_version())}, {"status": 200, "json": page()}],
    )
    html = open_supersede(operator_client)
    assert "That version was already superseded. Supersede the current one:" in html
    assert f'href="/stories/{STORY}/inspect/canon/{FW1}/lineage/"' in html
    assert 'class="fact-form supersede-form"' not in html


def test_a_version_not_in_the_timeline_flashes_and_returns_to_canon(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", FACTS, 200, json=page(version(id=P + "FW9")))
    response = operator_client.get(SUPERSEDE_URL)
    assert response.status_code == 302
    assert response["Location"] == TAB
    assert flashes(response) == [("error", NOT_IN_TIMELINE)]


def test_an_instant_version_is_pre_filled_as_its_interval(
    operator_client: Client, engine: FakeEngine
) -> None:
    proposition = version()["proposition"]
    proposition["valid_time"] = {"kind": "instant", "at": 10, "until": 11, "granularity": "minute"}
    engine.respond(
        "GET",
        FACTS,
        200,
        json=page(
            version(proposition=proposition, valid_from_inner=10, valid_to=11, valid_to_inner=11)
        ),
    )
    html = open_supersede(operator_client)
    assert "valid-time was an instant (granularity minute)" in html
    assert value(html, "valid_to") == "11"


def test_without_the_registry_every_qualifier_is_read_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable"))
    html = open_supersede(operator_client)
    assert "frame = inside, since = 3" in text(html)
    assert 'name="q-0-key"' not in html
    engine.requests.clear()
    data = unchanged_post(html, **{"q-TOTAL_FORMS": "0"})
    data.pop("q-0-key")
    data.pop("q-0-type")
    data.pop("q-0-value")
    operator_client.post(SUPERSEDE_URL, data)
    assert sent(engine)["proposition"]["qualifiers"] == {"frame": "inside", "since": 3}


@pytest.mark.parametrize(
    ("status", "code", "shown"),
    [
        (422, "key_mismatch", "A successor must keep the subject, predicate and key qualifiers"),
        (409, "fact_inactive", "That version's operation is no longer on the timeline"),
        (409, "fact_superseded", "That version was already superseded. Supersede the current one:"),
    ],
)
def test_supersede_refusals_render_their_message(
    operator_client: Client, engine: FakeEngine, status: int, code: str, shown: str
) -> None:
    html = open_supersede(operator_client)
    refuse(engine, SUPERSEDE, status, code)
    response = operator_client.post(SUPERSEDE_URL, unchanged_post(html))
    body = unescape(response.content.decode())
    assert shown in body
    assert 'class="fact-form supersede-form"' in body
    if code == "fact_superseded":
        assert f'href="/stories/{STORY}/inspect/canon/{FW1}/lineage/"' in body
    assert '<pre class="request-body">' in body


def test_supersede_of_a_missing_version_flashes_and_returns(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = open_supersede(operator_client)
    engine.respond("POST", SUPERSEDE, 404, json=error_json("fact_not_found"))
    response = operator_client.post(SUPERSEDE_URL, unchanged_post(html))
    assert response.status_code == 302
    assert response["Location"] == TAB
    assert flashes(response) == [("error", NOT_IN_TIMELINE)]


def test_a_greyed_canon_tab_greys_the_supersede_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    html = open_supersede(operator_client)
    assert "Not available for this story" in html
    assert not [r for r in engine.requests if r.url.path in (FACTS_PATH, PREDICATES_PATH)]


def test_the_supersede_back_link_follows_next(operator_client: Client, engine: FakeEngine) -> None:
    lineage = f"/stories/{STORY}/inspect/canon/{FW1}/lineage/"
    html = open_supersede(operator_client, "?" + urlencode({"next": lineage}))
    assert f'<a href="{lineage}">Back</a>' in html
    html = open_supersede(operator_client, "?next=https://elsewhere.example/")
    assert f'<a href="{TAB}">Back</a>' in html


def test_object_forms_without_the_registry_offer_everything() -> None:
    assert object_forms(None) == ("entity", "literal", "set", "none")
