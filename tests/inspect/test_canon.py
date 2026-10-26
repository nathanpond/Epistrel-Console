"""The Canon tab (#137): a story's canon facts as true then and as recorded then.

Runs against the real urlconf on the schema-validating fake Engine. The page is
``tests/fixtures/inspect/facts_list.json`` (revision 7):

- ``FA1``: ``located_at(Tobin, …PA1)``, valid ``[10 (inner 12), 40 (inner 38))``, closed
  (``tx_to`` set, ``superseded_by`` ``FB1``), turn 3;
- ``FB1``: ``located_at(Tobin, …PB1)`` ``[40, …)``, current, ``supersedes`` ``FA1``;
- ``FC1``: ``holds(Mara, 40 (int))``, open both ends, closed by ``FX1`` (not on the page: a
  truncated closer), author-locked;
- ``FD1``: ``NOT sworn_to(Mara, {Tobin, …PA1})`` with qualifiers, an Instant valid-time and
  ``characters`` visibility.

The registry is #136's ``predicates_list.json``; the cast is Mara (player) and Tobin."""

import copy
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.canon import (
    CURRENT_STATE,
    CurrentSet,
    bound_text,
    fact_states,
    literal_view,
    proposition_time_text,
    qualifier_items,
    question_text,
)
from console.engine.client import EngineClient
from console.engine.models import FactBody, FactListBody, Instant, Interval, TypedLiteral
from console.engine_access import engine_client_for
from console.forms.inspect import CanonForm, record_moment
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, lite, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

TAB = f"/stories/{STORY}/inspect/canon/"
FACTS = "/admin/stories/{story_id}/facts"
FACTS_PATH = f"/admin/stories/{STORY}/facts"
PREDICATES = "/stories/{story_id}/predicates"
P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
FA1, FB1, FC1, FD1, FX1 = (P + s for s in ("FA1", "FB1", "FC1", "FD1", "FX1"))
MARA, TOBIN = P + "T3A", P + "T3B"
LOC, HOLDS, SWORN, UNKNOWN = P + "PR1", P + "PR2", P + "PR4", P + "PX9"
CURSOR = P + "FZ9"
NOT_FOUND = "Not found (it may have been deleted or truncated)"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


def page(
    *facts: dict[str, Any], next_cursor: str | None = None, revision: int = 7
) -> dict[str, Any]:
    """A ``FactListBody``: ``facts`` (default: the fixture's four)."""
    shown = list(facts) if facts else fixture("facts_list")["facts"]
    return {"facts": shown, "next_cursor": next_cursor, "timeline_revision": revision}


def fact(fid: str, **over: Any) -> dict[str, Any]:
    """The fixture's fact ``fid`` with fields changed."""
    found: dict[str, Any]
    (found,) = [f for f in fixture("facts_list")["facts"] if f["id"] == fid]
    found.update(over)
    return found


def current(*ids: str, next_cursor: str | None = None, revision: int = 7) -> dict[str, Any]:
    """A current-set page holding versions with ``ids`` (the fixture's FB1 shape)."""
    return page(
        *(fact(FB1, id=i, supersedes=[]) for i in ids), next_cursor=next_cursor, revision=revision
    )


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json())
    fake.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    fake.respond("GET", FACTS, 200, json=page())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def answer(engine: FakeEngine, *bodies: dict[str, Any]) -> None:
    """The facts route answers ``bodies`` in order (the page, then current-set pages)."""
    engine.respond_sequence("GET", FACTS, [{"status": 200, "json": b} for b in bodies])


def tab(client: Client, query: str = "") -> str:
    response = client.get(f"{TAB}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == FACTS_PATH]


def sent(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


def row(html: str, fid: str) -> str:
    found = re.search(rf'<tr id="f-{fid}" class="fact-row[^"]*">(.*?)</tr>', html, re.S)
    assert found is not None, fid
    return found.group(0)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="f-([^"]+)" class="fact-row', html)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}[^"]*"[^>]*>(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(0)


def text(html: str) -> str:
    """The visible text: ``<br>`` a space, other tags dropped, whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.replace("<br>", " "))).strip()


def field_errors(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def non_field(html: str) -> str:
    found = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    return found.group(1) if found else ""


# --- the default read and the header -------------------------------------------------------------


def test_the_default_page_is_current_canon_as_the_record_stands_now(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    (call,) = reads(engine)
    assert sent(call) == {"viewpoint": "omniscient", "limit": "100"}
    # Convention 2: one registry read for the page's predicate names.
    assert [r.url.path for r in engine.requests].count(f"/stories/{STORY}/predicates") == 1
    assert '<h2 class="canon-question">Current canon · as the record stands now</h2>' in html
    assert row_ids(html) == [FA1, FB1, FC1, FD1]  # the Engine's id order
    assert "<title>Inspect · Canon · Harbour</title>" in html
    assert "<th>State</th>" not in html
    # Without "include superseded" every row is current: a closed tx_to isn't styled.
    assert " fact-superseded" not in html
    assert 'class="state' not in html


@pytest.mark.parametrize(
    ("story_time", "record", "superseded", "expected"),
    [
        (None, None, False, "Current canon · as the record stands now"),
        (0, None, False, "True at minute 0 · as recorded now"),
        (
            None,
            datetime(2026, 10, 24, 14, 3, 5, 999999, UTC),
            False,
            "Current canon · as recorded at 2026-10-24 14:03:05 UTC",
        ),
        (
            12,
            datetime(2026, 10, 24, 14, 3, 5, 999999, UTC),
            False,
            "True at minute 12 · as recorded at 2026-10-24 14:03:05 UTC",
        ),
        (None, None, True, "Every version in the record (current and superseded)"),
        (12, None, True, "Every version whose valid-time may cover minute 12"),
    ],
)
def test_the_header_names_the_question(
    story_time: int | None, record: datetime | None, superseded: bool, expected: str
) -> None:
    assert question_text(story_time, record, superseded) == expected


# --- filters → query params ----------------------------------------------------------------------


def test_filters_map_to_the_engines_query_params(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = (
        f"?subject=+{TOBIN.lower()}+&predicate={LOC}&as_of_story_time=12"
        "&as_of_record_time=2026-10-24T14:03:05"
    )
    html = tab(operator_client, query)
    (call,) = reads(engine)
    assert sent(call) == {
        "viewpoint": "omniscient",
        "limit": "100",
        "subject": TOBIN,
        "predicate": LOC,
        "as_of_story_time": "12",
        "as_of_record_time": "2026-10-24T14:03:05.999999Z",
    }
    assert f'name="subject" value="{TOBIN}"' in html  # the normalised value echoed
    assert f'<option value="{LOC}" selected>located_at (core)</option>' in html
    assert "True at minute 12 · as recorded at 2026-10-24 14:03:05 UTC" in html


def test_a_record_time_without_seconds_is_the_end_of_its_first_second(
    operator_client: Client, engine: FakeEngine
) -> None:
    tab(operator_client, "?as_of_record_time=2026-10-24T14:03")
    (call,) = reads(engine)
    assert sent(call)["as_of_record_time"] == "2026-10-24T14:03:00.999999Z"


def test_blank_filters_are_left_out(operator_client: Client, engine: FakeEngine) -> None:
    tab(operator_client, "?subject=&predicate=&as_of_story_time=&as_of_record_time=")
    (call,) = reads(engine)
    assert sent(call) == {"viewpoint": "omniscient", "limit": "100"}


@pytest.mark.parametrize("value", ["on", "1", "true", "TRUE", "On"])
def test_include_superseded_counts_these_as_ticked(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    answer(engine, page(fact(FB1)))
    html = tab(operator_client, f"?include_superseded={value}")
    (call,) = reads(engine)
    assert sent(call)["include_superseded"] == "true"
    assert 'name="include_superseded" id="id_include_superseded" checked' in html


@pytest.mark.parametrize("value", ["0", "false", "no", ""])
def test_include_superseded_other_values_are_unticked(
    operator_client: Client, engine: FakeEngine, value: str
) -> None:
    tab(operator_client, f"?include_superseded={value}")
    (call,) = reads(engine)
    assert "include_superseded" not in sent(call)


@pytest.mark.parametrize(
    ("query", "field", "message"),
    [
        (
            f"?subject={P}T3l",
            "subject",
            "Enter a 26-character entity id (digits and capital letters, no I, L, O or U)",
        ),
        (f"?subject={P[:-1]}OT3A", "subject", "Enter a 26-character entity id"),
        ("?subject=abc", "subject", "Enter a 26-character entity id"),
        ("?as_of_story_time=-1", "as_of_story_time", "Enter a whole number of minutes, 0 or more"),
        ("?as_of_story_time=1.5", "as_of_story_time", "Enter a whole number of minutes, 0 or more"),
        (f"?as_of_story_time={2**63}", "as_of_story_time", "That minute is too large"),
        ("?as_of_record_time=yesterday", "as_of_record_time", "Enter a date and time"),
    ],
)
def test_a_local_error_makes_no_facts_call(
    operator_client: Client, engine: FakeEngine, query: str, field: str, message: str
) -> None:
    html = tab(operator_client, query)
    assert reads(engine) == []
    assert message in field_errors(html, field)
    # The shell's reads and the registry read still run: the form needs the registry.
    assert any(r.url.path == f"/stories/{STORY}/predicates" for r in engine.requests)


def test_the_largest_minute_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    tab(operator_client, f"?as_of_story_time={2**63 - 1}")
    (call,) = reads(engine)
    assert sent(call)["as_of_story_time"] == str(2**63 - 1)


def test_as_recorded_at_with_include_superseded_is_a_non_field_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client, "?as_of_record_time=2026-10-24T14:03&include_superseded=on")
    assert reads(engine) == []
    assert (
        "The Engine lists every version only in today&#x27;s record — clear &#x27;as recorded "
        "at&#x27; or &#x27;include superseded&#x27;"
    ) in non_field(html)
    assert html.index('<div class="errors"') < html.index('<form method="get"')


def test_a_bad_cursor_is_dropped_with_a_notice(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client, f"?cursor={CURSOR.lower()}")
    (call,) = reads(engine)
    assert "cursor" not in sent(call)
    assert "That page link was invalid — showing the first page" in html
    assert "First page" not in html


def test_a_valid_cursor_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = tab(operator_client, f"?cursor={CURSOR}")
    (call,) = reads(engine)
    assert sent(call)["cursor"] == CURSOR
    assert "page link was invalid" not in html


# --- paging --------------------------------------------------------------------------------------


def test_next_page_carries_the_filters_and_first_page_drops_the_cursor(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page(fact(FB1), next_cursor=FB1))
    html = tab(operator_client, f"?subject={TOBIN.lower()}&as_of_story_time=5")
    assert (
        f'<a class="next-page" href="?subject={TOBIN}&amp;as_of_story_time=5&amp;cursor={FB1}">'
        "Next page</a>"
    ) in html
    assert "First page" not in html
    html = tab(operator_client, f"?subject={TOBIN}&as_of_story_time=5&cursor={FB1}")
    assert (
        f'<a class="first-page" href="?subject={TOBIN}&amp;as_of_story_time=5">First page</a>'
    ) in html
    # The Apply form carries no cursor: a changed filter starts over.
    form = re.search(r'<form method="get".*?</form>', html, re.S)
    assert form is not None
    assert 'name="cursor"' not in form.group(0)


# --- cells ---------------------------------------------------------------------------------------


def test_a_row_renders_its_proposition_times_and_flags(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    a = row(html, FA1)
    assert text(cell(a, "proposition")) == "Tobin located_at …4P7R0PA1"
    assert f'<a href="/stories/{STORY}/inspect/predicates/?layer=all#p-{LOC}" title="{LOC}">' in a
    assert '<td class="valid-time">[10 (inner 12), 40 (inner 38))</td>' in a
    assert (
        '<time datetime="2026-10-24T15:00:00.000001Z" title="First closure (never rewritten): '
        '2026-10-24T15:00:00.000001Z">2026-10-24 15:00:00 UTC</time>'
    ) in cell(a, "tx-time")
    assert text(cell(a, "tx-time")) == "2026-10-24 14:03:05 UTC → 2026-10-24 15:00:00 UTC"
    assert '<span title="render">Render</span>' in a
    assert '<span title="narrator">Narrator</span>' in a
    assert '<td class="lock"></td>' in a
    assert f'<a href="/stories/{STORY}/history/?before_turn=4#turn-3-0">turn 3</a>' in a
    b = row(html, FB1)
    assert '<td class="valid-time">[40, …)</td>' in b
    assert text(cell(b, "tx-time")).endswith("→ current")
    assert text(cell(b, "links")) == "supersedes …4P7R0FA1"
    assert '<span title="objective">Objective</span>' in b
    c = row(html, FC1)
    assert text(cell(c, "proposition")) == "Mara (player) holds 40 (int)"
    assert '<td class="valid-time">[…, …)</td>' in c
    assert '<span title="Author-locked" aria-label="locked">🔒</span>' in c
    assert text(cell(c, "links")) == "—"


def test_a_set_object_polarity_qualifiers_and_visibility(
    operator_client: Client, engine: FakeEngine
) -> None:
    d = row(tab(operator_client), FD1)
    assert text(cell(d, "proposition")) == (
        "Mara (player) NOT sworn_to {Tobin, …4P7R0PA1} count=2 oath=blood public=false "
        "tags=[old, new] weight=1.5 witness=Tobin"
    )
    assert '<strong class="polarity">NOT</strong>' in d
    assert text(cell(d, "visibility")) == "Characters: Mara (player), …4P7R0PA1"
    assert "instant at 60 min (granularity day, until 1500 min)" in d
    assert f"<code>{P}EV1</code>" in d
    assert '<span title="latent">Latent</span>' in d
    assert f'<dt>Anchored to</dt><dd><code title="{FB1}">…4P7R0FB1</code></dd>' in d


def test_characters_visibility_without_characters_reads_none_listed(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page(fact(FB1, visibility={"scope": "characters", "characters": []})))
    b = row(tab(operator_client), FB1)
    assert text(cell(b, "visibility")) == "Characters: none listed"


def test_an_object_less_proposition_shows_a_dash(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fact(FB1)
    body["proposition"].pop("object_id")
    answer(engine, page(body))
    assert text(cell(row(tab(operator_client), FB1), "proposition")) == "Tobin located_at —"


@pytest.mark.parametrize(
    ("literal", "shown", "kind"),
    [
        ({"kind": "int", "value": 40}, "40", "int"),
        ({"kind": "float", "value": 1.25}, "1.25", "float"),
        ({"kind": "float", "value": 2.0}, "2", "float"),
        ({"kind": "str", "value": "the red door"}, '"the red door"', "str"),
        ({"kind": "bool", "value": True}, "true", "bool"),
        ({"kind": "bool", "value": False}, "false", "bool"),
        ({"kind": "date", "value": "1887-03-04"}, "1887-03-04", "date"),
        ({"kind": "duration", "value": 90}, "90 min", "duration"),
        ({"kind": "quantity", "value": 3.5, "unit": "kg"}, "3.5 kg", "quantity"),
        ({"kind": "quantity", "value": 12, "unit": "m"}, "12 m", "quantity"),
    ],
)
def test_literal_kinds(literal: dict[str, Any], shown: str, kind: str) -> None:
    parsed = TypedLiteral.from_json(literal)
    assert parsed is not None
    view = literal_view(parsed)
    assert (view.text, view.kind, view.full) == (shown, kind, "")


def test_a_long_string_literal_is_cut_with_the_full_text_in_title(
    operator_client: Client, engine: FakeEngine
) -> None:
    long = "x" * 81
    body = fact(FC1)
    body["proposition"]["literal"] = {"kind": "str", "value": long}
    answer(engine, page(body))
    c = row(tab(operator_client), FC1)
    assert f'<span class="literal" title="&quot;{long}&quot;">&quot;{"x" * 80}…&quot;</span>' in c
    assert '<span class="muted literal-kind">(str)</span>' in c


def test_inner_bounds_show_only_where_they_differ() -> None:
    assert bound_text(10, 10) == "10"
    assert bound_text(10, 12) == "10 (inner 12)"
    assert bound_text(None, None) == "…"
    assert bound_text(None, 5) == "… (inner 5)"
    interval = Interval.from_json({"start": {"outer": 0, "inner": 2}, "end": None})
    assert interval is not None
    assert proposition_time_text(interval) == "interval [0 (inner 2), …) min"
    instant = Instant.from_json({"kind": "instant", "at": 5, "granularity": "hour", "until": 65})
    assert instant is not None
    assert proposition_time_text(instant) == "instant at 5 min (granularity hour, until 65 min)"


def test_qualifiers_sort_by_key_and_name_cast_ids() -> None:
    items = qualifier_items({"b": [MARA, "x"], "a": MARA, "c": UNKNOWN}, {MARA: "Mara"})
    assert [(q.key, q.text) for q in items] == [("a", MARA), ("b", f"[{MARA}, x]"), ("c", UNKNOWN)]
    assert items[0].entity is not None
    assert items[0].entity.label == "Mara"
    assert items[2].entity is not None
    assert items[2].entity.label is None


# --- names ---------------------------------------------------------------------------------------


def test_the_predicate_select_lists_the_registry_in_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client)
    select = re.search(r'<select name="predicate"[^>]*>(.*?)</select>', html, re.S)
    assert select is not None
    options = re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select.group(1))
    assert options[0] == ("", "Any predicate")
    assert options[1:] == [
        (P + "PR1", "located_at (core)"),
        (P + "PR2", "holds (core)"),
        (P + "PR3", "contained_in (core)"),
        (P + "PR4", "sworn_to (author)"),
        (P + "PR5", "guards (auto)"),
    ]
    assert f'<option value="{MARA}">Mara (player)</option>' in html  # the subject datalist


def test_a_predicate_not_in_the_registry_is_kept_selected_and_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = tab(operator_client, f"?predicate={UNKNOWN}")
    (call,) = reads(engine)
    assert sent(call)["predicate"] == UNKNOWN
    assert f'<option value="{UNKNOWN}" selected>…4P7R0PX9 (not in registry)</option>' in html


def test_a_failed_registry_read_gives_a_free_text_predicate_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    html = tab(operator_client, f"?predicate={LOC.lower()}")
    (call,) = reads(engine)
    assert sent(call)["predicate"] == LOC
    assert f'<input type="text" name="predicate" value="{LOC}"' in html
    assert "Predicate names unavailable — enter a predicate id" in html
    # The facts still render, predicate ids as short ids.
    assert text(cell(row(html, FA1), "proposition")) == "Tobin …4P7R0PR1 …4P7R0PA1"


def test_a_bad_free_text_predicate_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    html = tab(operator_client, "?predicate=located_at")
    assert reads(engine) == []
    assert "Enter a 26-character predicate id" in field_errors(html, "predicate")


def test_a_failed_story_read_shows_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 404, json=error_json("not_found", "gone"))
    html = tab(operator_client)
    assert "Cast names unavailable — showing ids" in html
    assert text(cell(row(html, FA1), "proposition")) == "…4P7R0T3B located_at …4P7R0PA1"


# --- the state rule ------------------------------------------------------------------------------


def test_with_superseded_a_closed_row_named_by_a_row_on_the_page_is_superseded(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page(fact(FA1), fact(FB1)))
    html = tab(operator_client, "?include_superseded=on")
    assert len(reads(engine)) == 1  # no row needed the current-set read
    a = row(html, FA1)
    assert 'class="fact-row fact-superseded"' in a
    assert text(cell(a, "state")) == "superseded"
    assert text(cell(a, "links")) == "superseded by …4P7R0FB1"
    b = row(html, FB1)
    assert "fact-superseded" not in b
    assert text(cell(b, "state")) == "current"
    assert "<th>State</th>" in html
    assert "Every version in the record (current and superseded)" in html


def test_a_closed_row_in_the_current_set_is_current_closure_undone(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page(fact(FB1), fact(FC1)), current(FB1, FC1))
    html = tab(operator_client, f"?include_superseded=on&subject={MARA}&predicate={HOLDS}")
    first, check = reads(engine)
    assert sent(first)["include_superseded"] == "true"
    assert sent(check) == {
        "viewpoint": "omniscient",
        "limit": "500",
        "subject": MARA,
        "predicate": HOLDS,
    }
    c = row(html, FC1)
    assert "fact-superseded" not in c
    state = cell(c, "state")
    assert 'title="Closed at 2026-10-24 16:30:00 UTC by an operation that has since left the ' in (
        state
    )
    assert text(state) == "current (closure undone) its closing operation left the timeline"
    assert text(cell(c, "links")) == "—"
    # The tx column still shows the stored first closure.
    assert "2026-10-24 16:30:00 UTC" in cell(c, "tx-time")


def test_a_closed_row_missing_from_a_complete_current_set_is_superseded(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page(fact(FB1), fact(FC1)), current(FB1))
    html = tab(operator_client, "?include_superseded=on")
    c = row(html, FC1)
    assert 'class="fact-row fact-superseded"' in c
    # No active closer on the page: the stored superseded_by is only "first closed by".
    assert text(cell(c, "links")) == "first closed by …4P7R0FX1"


def test_the_current_set_read_follows_cursors(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, page(fact(FC1)), current(FB1, next_cursor=FB1), current(FC1))
    html = tab(operator_client, "?include_superseded=on&as_of_story_time=5")
    _, one, two = reads(engine)
    assert "as_of_story_time" not in sent(one)
    assert sent(two)["cursor"] == FB1
    assert "current (closure undone)" in row(html, FC1)


@pytest.mark.parametrize(
    ("answers", "title", "reason"),
    [
        (
            [current(FB1, next_cursor=FB1)] * 4,
            "More than 2,000 current versions match; couldn&#x27;t check whether this closure "
            "still stands",
            "more than 2,000 current versions match",
        ),
        (
            [current(FC1, revision=8)],
            "Canon changed while this page loaded — reload",
            "canon changed while this page loaded — reload",
        ),
    ],
)
def test_an_unsettled_current_set_leaves_the_closure_not_confirmed(
    operator_client: Client,
    engine: FakeEngine,
    answers: list[dict[str, Any]],
    title: str,
    reason: str,
) -> None:
    answer(engine, page(fact(FA1), fact(FB1), fact(FC1)), *answers)
    html = tab(operator_client, "?include_superseded=on")
    assert len(reads(engine)) == 1 + len(answers)
    c = row(html, FC1)
    assert "fact-superseded" not in c
    assert f'<td class="state state-unconfirmed" title="{title}">closed · not confirmed</td>' in c
    assert (
        f'<p class="notice unconfirmed">Couldn&#x27;t confirm whether 1 closed version is still '
        f"closed: {reason}</p>"
    ) in html
    assert html.index("notice unconfirmed") < html.index('<table class="canon-facts">')


def test_a_failed_current_set_read_leaves_the_closure_not_confirmed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        FACTS,
        [
            {"status": 200, "json": page(fact(FC1))},
            {"status": 422, "json": error_json("validation_error", "bad")},
        ],
    )
    html = tab(operator_client, "?include_superseded=on")
    assert "closed · not confirmed" in row(html, FC1)
    assert "the check of current versions failed" in html


def test_the_state_rule_complement_without_include_superseded() -> None:
    body = FactListBody.from_json(page())
    assert body is not None
    calls: list[int] = []

    def never() -> CurrentSet:
        calls.append(1)
        return CurrentSet()

    states = fact_states(body.facts, include_superseded=False, current=never)
    assert set(states.by_id.values()) == {CURRENT_STATE}
    assert calls == []


def test_superseded_by_names_active_rows_on_the_page_not_the_stored_closer(
    operator_client: Client, engine: FakeEngine
) -> None:
    # FA1's stored superseded_by names a truncated first closer; FB1 (active) re-closed it.
    answer(engine, page(fact(FA1, superseded_by=FX1), fact(FB1)))
    a = row(tab(operator_client, "?include_superseded=on"), FA1)
    links = text(cell(a, "links"))
    assert links == "superseded by …4P7R0FB1"
    assert "first closed by" not in links


def test_the_permanent_predicate_note_needs_include_superseded_and_a_minute(
    operator_client: Client, engine: FakeEngine
) -> None:
    note = "Facts of permanent predicates are listed here only when their valid-time covers minute"
    answer(engine, page(fact(FB1)))
    assert note in tab(operator_client, "?include_superseded=on&as_of_story_time=3")
    assert note not in tab(operator_client, "?include_superseded=on")
    assert note not in tab(operator_client, "?as_of_story_time=3")


# --- empty and failures --------------------------------------------------------------------------


def test_an_empty_answer(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", FACTS, 200, json={"facts": [], "next_cursor": None, "timeline_revision": 7}
    )
    assert '<p class="canon-none">None</p>' in tab(operator_client)
    assert "No facts match these filters" in tab(operator_client, "?as_of_story_time=3")


def test_module_off_renders_the_greyed_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", FACTS, 409, json=error_json("module_off", "lite", module="pipeline"))
    html = tab(operator_client)
    assert '<div class="panel unavailable">' in html
    assert "Needs the structured pipeline" in html
    assert 'class="config-changed"' in html


def test_a_lite_story_greys_the_tab_with_no_facts_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite())
    html = tab(operator_client)
    assert reads(engine) == []
    assert '<div class="panel unavailable">' in html


def test_a_validation_error_lands_beside_the_named_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        FACTS,
        422,
        json=error_json("validation_error", "unknown entity", field="query.subject"),
    )
    html = tab(operator_client, f"?subject={MARA}")
    assert "unknown entity" in field_errors(html, "subject")
    assert row_ids(html) == []


def test_a_validation_error_on_another_field_goes_above_the_filters(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", FACTS, 422, json=error_json("validation_error", "too big", field="query.limit")
    )
    assert "too big" in non_field(tab(operator_client))


def test_viewpoint_unresolved_and_omniscient_required(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", FACTS, 422, json=error_json("viewpoint_unresolved", "who?"))
    assert "This read needs the omniscient viewpoint" in non_field(tab(operator_client))
    engine.respond("GET", FACTS, 403, json=error_json("omniscient_required", "no"))
    assert '<p class="omniscient-required">This read needs the omniscient viewpoint</p>' in tab(
        operator_client
    )


def test_not_found_and_other_errors(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", FACTS, 404, json=error_json("not_found", "gone"))
    assert NOT_FOUND in tab(operator_client)
    engine.respond("GET", FACTS, 503, json=error_json("ledger_unavailable", "down"))
    html = tab(operator_client)
    assert "ledger_unavailable" in html
    assert '<form method="get"' in html


# --- the form ------------------------------------------------------------------------------------


def test_record_moment_reads_utc_to_the_end_of_the_second() -> None:
    assert record_moment("2026-10-24T14:03:05") == datetime(2026, 10, 24, 14, 3, 5, 999999, UTC)
    assert record_moment("2026-10-24T14:03") == datetime(2026, 10, 24, 14, 3, 0, 999999, UTC)
    assert record_moment("2026-02-30T14:03") is None
    assert record_moment("2026-10-24 14:03") is None


def test_the_form_never_normalises_letters_the_pattern_refuses() -> None:
    form = CanonForm({"subject": f" {P}T3o "}, registry=None)
    assert not form.is_valid()
    assert form.data["subject"] == f"{P}T3O"
    assert "subject" in form.errors


def test_a_parsed_fact_keeps_unknown_enum_values() -> None:
    body = fact(FB1)
    body["proposition"]["polarity"] = "hedged"
    body["visibility"] = {"scope": "rumour"}
    parsed = FactBody.from_json(body)
    assert parsed is not None
    assert parsed.proposition.polarity == "hedged"
    assert parsed.visibility.scope == "rumour"
