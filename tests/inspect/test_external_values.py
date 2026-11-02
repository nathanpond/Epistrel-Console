"""The World tab's External values section (#248).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: the player Mara, Alice and Bob). Fixtures:

- ``m6/external_facts.json`` (#232; revision 12, ``next_after_id`` ``RD02``): ``RD01`` a ``live``
  ``bank`` · ``balance`` read of 4200 at turn 3, story-scoped, citing one current fact ``FA01``;
  ``RD02`` a ``last_known`` read with ``value: null`` and no facts;
- ``m6/external_facts_page2.json`` (keyed on ``after_id=RD02``, C14; no cursor): ``RD03`` an
  ``unknown`` read with ``turn``/``read_at`` null; ``RD04`` a ``registry`` · ``licensed`` read of
  ``true`` at turn 5 · v1, scoped to a character outside the cast, cited by the closed fact
  ``FA02`` whose proposition doesn't parse (an ``at`` of ``130.0``); ``RD05`` a ``weather`` ·
  ``sky`` read of "sunny" at turn 0, scoped to Mara;
- ``m6/external_facts_empty.json``: no items.
"""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from console.canon import lineage_url
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import AS_OF_INVALID, OMNISCIENT_TEXT, TURN_TOO_HIGH
from console.inspect import history_turn_url
from console.paging import CAST_UNAVAILABLE, CURSOR_DROPPED
from console.views import inspect as views
from console.views.inspect_external import external_value_text
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
OUTSIDER = "01JM6CH0000000000000000099"
URL = f"/stories/{STORY}/inspect/world/external-values/"
EXTERNAL = "/stories/{story_id}/inspect/external-facts"
EXTERNAL_PATH = f"/stories/{STORY}/inspect/external-facts"
PREDICATES = "/stories/{story_id}/predicates"
PREDICATES_PATH = f"/stories/{STORY}/predicates"
RD = {n: f"01JM6RD{n:019d}" for n in range(1, 6)}
FA1 = "01JM6FA0000000000000000001"
FA2 = "01JM6FA0000000000000000002"
STATE_ID = "01JM6SD0000000000000000004"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
UNLINKED = "tests.inspect.urls_without_providers"
STUBBED = "tests.urls_with_engine_operation"
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "console" / "inspect"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond(
        "GET", PREDICATES, 200, json=json.loads((FIXTURES / "predicates_list.json").read_text())
    )
    fake.respond("GET", EXTERNAL, 200, json=fixture("external_facts"))
    fake.respond(
        "GET", EXTERNAL, 200, json=fixture("external_facts_page2"), query={"after_id": RD[2]}
    )
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == EXTERNAL_PATH]


def registry_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == PREDICATES_PATH]


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("GET", EXTERNAL, status, json=body)


def row(html: str, event_id: str) -> str:
    found = re.search(rf'<tr id="read-{event_id}" class="external-read">(.*?)</tr>', html, re.S)
    assert found is not None, event_id
    return found.group(1)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="read-([0-9A-Z]+)"', html)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def more(row_html: str) -> str:
    found = re.search(r"<details><summary>More</summary>(.*?)</details>", row_html, re.S)
    assert found is not None
    return found.group(1)


def count(html: str) -> str | None:
    found = re.search(r'<p class="external-values-count">(.*?)</p>', html)
    return found.group(1) if found else None


def header(html: str) -> str:
    found = re.search(r'<p class="external-values-header">(.*?)</p>', html)
    assert found is not None
    return found.group(1)


def as_of_field(html: str) -> str:
    found = re.search(r'<div class="field field-as_of">(.*?)</div>', html, re.S)
    assert found is not None
    return found.group(1)


def page2(engine: FakeEngine) -> None:
    answer(engine, 200, fixture("external_facts_page2"))


# --- the section and its reads -------------------------------------------------------------------


def test_section_registered_and_never_greyed(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = page(operator_client)
    assert f'<a class="active" aria-current="page" href="{URL}">External values</a>' in html
    assert "<h2>External values</h2>" in html
    assert len(reads(engine)) == 1
    assert '<div class="panel unavailable">' not in html
    assert reverse("inspect:external-values", args=[STORY]) == URL


def test_reads_in_order(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        EXTERNAL_PATH,
        PREDICATES_PATH,
    ]
    (call,) = reads(engine)
    assert dict(call.url.params) == {"viewpoint": "omniscient", "limit": "100"}


def test_registry_skipped_without_facts(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, fixture("external_facts_empty"))
    page(operator_client)
    assert len(reads(engine)) == 1
    assert registry_reads(engine) == []
    body = fixture("external_facts")
    body["items"][0]["facts"] = []
    answer(engine, 200, body)
    page(operator_client)
    assert len(reads(engine)) == 2
    assert registry_reads(engine) == []


def test_read_only(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    assert {r.method for r in engine.requests} == {"GET"}
    before = len(engine.requests)
    assert operator_client.post(URL).status_code == 405
    assert len(engine.requests) == before
    for name in ("external_values.html", "_external_read_row.html"):
        assert '<form method="post"' not in (TEMPLATES / name).read_text()


# --- rows ----------------------------------------------------------------------------------------


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert row_ids(html) == [RD[1], RD[2]]
    first = row(html, RD[1])
    provider = reverse("engine:provider", args=["bank"])
    assert cell(first, "value-read") == (
        f'<a class="provider-link" href="{provider}"><code>bank</code></a> · <code>balance</code>'
    )
    assert cell(first, "scope") == "Story"
    assert cell(first, "value") == "4,200"
    assert cell(first, "source") == '<span title="live">Live</span>'
    assert 'datetime="2026-10-31T18:02:11Z"' in cell(first, "read-at")
    assert cell(first, "turn") == f'<a href="{escape(history_turn_url(STORY, 3, 0))}">turn 3</a>'
    second = row(html, RD[2])
    assert cell(second, "value") == '<span class="undefined">unknown</span>'
    assert cell(second, "source") == '<span title="last_known">Last known</span>'
    assert cell(second, "read-at") == "—"
    assert cell(second, "cited-by") == "—"

    page2(engine)
    html = page(operator_client)
    assert row_ids(html) == [RD[3], RD[4], RD[5]]
    unknown = row(html, RD[3])
    assert cell(unknown, "value") == '<span class="undefined">unknown</span>'
    assert cell(unknown, "source") == '<span title="unknown">Unknown</span>'
    assert cell(unknown, "turn") == "—"
    assert cell(unknown, "read-at") == "—"
    variant = row(html, RD[4])
    assert cell(variant, "scope") == f'<code title="{OUTSIDER}">…00000099</code>'
    assert cell(variant, "value") == "true"
    assert cell(variant, "turn") == (
        f'<a href="{escape(history_turn_url(STORY, 5, 1))}">turn 5 · v1</a>'
    )
    text = row(html, RD[5])
    assert cell(text, "scope").startswith("Mara")
    assert cell(text, "value") == "sunny"
    assert cell(text, "turn") == f'<a href="{escape(history_turn_url(STORY, 0, 0))}">turn 0</a>'


def test_external_value_text() -> None:
    assert external_value_text(None) == ("unknown", True)
    assert external_value_text(1200) == ("1,200", False)
    assert external_value_text(2.5) == ("2.5", False)
    assert external_value_text(False) == ("false", False)
    assert external_value_text("") == ('""', False)
    assert external_value_text([1, "a"]) == ('[1, "a"]', False)


def test_blank_provider_and_source_read_dash(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("external_facts")
    body["items"][0].update(provider="", value_name="", source="", scope_key="")
    answer(engine, 200, body)
    first = row(page(operator_client), RD[1])
    assert cell(first, "value-read") == "— · —"
    assert cell(first, "source") == "—"
    assert cell(first, "scope") == "Story"


def test_cited_fact_links_lineage_with_subject_and_predicate(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    cited = cell(row(html, RD[1]), "cited-by")
    url = lineage_url(STORY, FA1, MARA, "01JM6FA0000000000000000090")
    assert "subject=" in url
    assert "predicate=" in url
    assert f'<a class="lineage-link" href="{escape(url)}">' in cited
    assert '<span class="subject">' in cited  # the proposition, through _proposition.html
    assert cited.endswith("· current</li></ul>")
    # **Complement:** the unparsed proposition links by id only and shows its JSON in More.
    page2(engine)
    html = page(operator_client)
    closed = row(html, RD[4])
    assert f'<a class="lineage-link" href="{lineage_url(STORY, FA2)}">' in cell(closed, "cited-by")
    assert "?subject=" not in cell(closed, "cited-by")
    assert '<span class="subject">' not in cell(closed, "cited-by")
    assert "Proposition (as sent)" in more(closed)
    assert "&quot;at&quot;: 130.0" in more(closed)


def test_closed_fact_marked(operator_client: Client, engine: FakeEngine) -> None:
    page2(engine)
    cited = cell(row(page(operator_client), RD[4]), "cited-by")
    assert '· closed <time datetime="2026-10-31T19:05:00Z"' in cited
    assert "current" not in cited


def test_more_holds_ids_and_raw_value(operator_client: Client, engine: FakeEngine) -> None:
    first = more(row(page(operator_client), RD[1]))
    assert f"<dt>Event id</dt><dd><code>{RD[1]}</code></dd>" in first
    assert f"<dt>State id</dt><dd><code>{STATE_ID}</code></dd>" in first
    assert '<pre class="raw-value">4200</pre>' in first
    assert f"<dt>Fact id</dt><dd><code>{FA1}</code></dd>" in first
    assert '<span title="objective">Objective</span>' in first
    assert '<span title="external">External</span>' in first
    assert '<span title="provider">Provider</span>' in first
    assert "[130, …) min" in first
    assert 'datetime="2026-10-31T18:02:11Z"' in first
    second = more(row(page(operator_client), RD[2]))
    assert '<pre class="raw-value">null</pre>' in second
    assert "Fact id" not in second


# --- filters and paging --------------------------------------------------------------------------


def test_as_of_sent_and_count_qualified(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=0")
    (call,) = reads(engine)
    assert call.url.params["as_of"] == "0"
    assert header(html) == "Values read from providers · through turn 0 · revision 12"
    assert count(html) == "2 reads on this page, matching filters"
    assert "Reads by turns up to and including this one" in as_of_field(html)


@pytest.mark.parametrize("typed", ["-1", "1.5", "٣", "2147483648"])
def test_invalid_as_of_makes_no_call(
    operator_client: Client, engine: FakeEngine, typed: str
) -> None:
    html = page(operator_client, f"?as_of={typed}")
    assert reads(engine) == []
    assert registry_reads(engine) == []
    error = TURN_TOO_HIGH if typed == "2147483648" else AS_OF_INVALID
    assert escape(error) in as_of_field(html)
    assert header(html) == "Values read from providers"
    assert count(html) is None


def test_bad_cursor_dropped_not_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?after_id=nope")
    assert f'<p class="notice cursor-dropped">{CURSOR_DROPPED}</p>' in html
    (call,) = reads(engine)
    assert "after_id" not in call.url.params
    assert "nope" not in html[html.index("<h2>External values</h2>") :]
    assert all("nope" not in str(r.url) for r in engine.requests)


def test_paging_links(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=7")
    assert f'<a class="next-page" href="?as_of=7&amp;after_id={RD[2]}">Next page</a>' in html
    assert "First page" not in html
    second = page(operator_client, f"?as_of=7&after_id={RD[2]}")
    call = reads(engine)[-1]
    assert dict(call.url.params) == {
        "viewpoint": "omniscient",
        "limit": "100",
        "as_of": "7",
        "after_id": RD[2],
    }
    assert '<a class="first-page" href="?as_of=7">First page</a>' in second
    assert "Next page" not in second
    assert row_ids(second) == [RD[3], RD[4], RD[5]]
    form = second[second.index('<form method="get"') : second.index("</form>")]
    assert "after_id" not in form  # Apply carries no cursor


def test_count_line(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert header(html) == "Values read from providers · now · revision 12"
    assert count(html) == "2 reads on this page"
    assert count(page(operator_client, f"?after_id={RD[2]}")) == "3 reads on this page"
    page2(engine)
    assert count(page(operator_client)) == "3 reads"
    body = fixture("external_facts_page2")
    body["items"] = body["items"][:1]
    answer(engine, 200, body)
    assert count(page(operator_client)) == "1 read"


def test_cursor_page_empty_wording(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", EXTERNAL, 200, json=fixture("external_facts_empty"), query={"after_id": RD[2]}
    )
    html = page(operator_client, f"?after_id={RD[2]}")
    assert (
        '<p class="none external-values-none">'
        f"{escape('No reads on this page — the list may have changed since the last page.')}</p>"
    ) in html
    assert count(html) == "0 reads on this page"


@pytest.mark.parametrize(
    ("query", "line"),
    [
        (f"?as_of=3&after_id={RD[2]}", "No reads on this page — the list may have changed"),
        ("?as_of=3", "No provider values were read through turn 3"),
        ("", "No provider values have been read for this story yet — a turn reads them when"),
    ],
)
def test_empty_states_precedence(
    operator_client: Client, engine: FakeEngine, query: str, line: str
) -> None:
    empty = fixture("external_facts_empty")
    answer(engine, 200, empty)
    engine.respond("GET", EXTERNAL, 200, json=empty, query={"after_id": RD[2]})
    html = page(operator_client, query)
    found = re.search(r'<p class="none external-values-none">(.*?)</p>', html)
    assert found is not None
    assert found.group(1).startswith(escape(line))
    assert "<table" not in html


def test_unreadable_entry_row(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("external_facts")
    bad = copy.deepcopy(body["items"][1])
    bad["event_id"] = "01JM6RD0000000000000000009"
    bad["turn"] = 4.0  # schema-valid, but not an int to the Console
    body["items"].append(bad)
    answer(engine, 200, body)
    html = page(operator_client)
    assert row_ids(html) == [RD[1], RD[2]]
    assert html.count('<tr class="external-read-unreadable">') == 1
    assert "This read couldn&#x27;t be read" in html
    assert "01JM6RD0000000000000000009" in html
    assert count(html) == "2 reads on this page"
    body["items"] = [bad]
    answer(engine, 200, body)
    html = page(operator_client)
    assert html.count('<tr class="external-read-unreadable">') == 1
    assert "external-values-none" not in html


# --- errors --------------------------------------------------------------------------------------


def test_validation_error_beside_as_of(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, error_json("validation_error", "as_of too far", field="query.as_of"))
    html = page(operator_client, "?as_of=5")
    assert "as_of too far" in as_of_field(html)
    assert count(html) is None
    assert header(html) == "Values read from providers · through turn 5"


def test_after_id_error_first_page_link(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        EXTERNAL,
        422,
        json=error_json("validation_error", "stale cursor", field="query.after_id"),
        query={"after_id": RD[2]},
    )
    html = page(operator_client, f"?as_of=5&after_id={RD[2]}")
    errors = html[html.index('<div class="errors" role="alert">') :]
    assert "stale cursor" in errors
    assert '<a class="first-page" href="?as_of=5">First page</a>' in errors
    assert html.index("stale cursor") < html.index('<form method="get"')
    # Any other 422 lands above the filters without the link.
    answer(engine, 422, error_json("validation_error", "odd", field="query.limit"))
    other = page(operator_client)
    assert "odd" in other[other.index('<div class="errors"') : other.index('<form method="get"')]
    assert 'class="first-page"' not in other


@pytest.mark.parametrize(
    ("status", "code"), [(422, "viewpoint_unresolved"), (403, "omniscient_required")]
)
def test_omniscient_required_text(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    answer(engine, status, error_json(code, "omniscient please"))
    html = page(operator_client)
    assert f'<p class="omniscient-required">{OMNISCIENT_TEXT}</p>' in html
    assert count(html) is None
    assert as_of_field(html)


def test_404_line(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 404, error_json("not_found", "gone"))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert count(html) is None
    assert "Harbour Ledger" in html


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 503, error_json("erasure_pending", "held"))
    response = operator_client.get(URL)
    assert response.status_code == 503


@pytest.mark.parametrize("code", ["database_unavailable", "ledger_unavailable"])
def test_503_panel_keeps_filters(operator_client: Client, engine: FakeEngine, code: str) -> None:
    answer(engine, 503, error_json(code, "down"))
    html = page(operator_client)
    assert 'class="engine-error"' in html
    assert html.index('<form method="get"') < html.index('class="engine-error"')
    assert count(html) is None
    assert "Harbour Ledger" in html


def test_story_read_failed_shows_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    page2(engine)
    html = page(operator_client)
    assert CAST_UNAVAILABLE in html
    assert cell(row(html, RD[5]), "scope") == f'<code title="{MARA}">…00000001</code>'


# --- links to other stories' pages (D19) ---------------------------------------------------------


def test_providers_link_only_when_registered(operator_client: Client, engine: FakeEngine) -> None:
    with override_settings(ROOT_URLCONF=UNLINKED):
        html = page(operator_client)
    assert "Bindings:" not in html
    assert cell(row(html, RD[1]), "value-read") == "<code>bank</code> · <code>balance</code>"
    # **Complement:** the shared stub urlconf links both.
    with override_settings(ROOT_URLCONF=STUBBED):
        html = page(operator_client)
        providers = reverse("stories:providers", args=[STORY])
        provider = reverse("engine:provider", args=["bank"])
    assert (
        f'<p class="external-values-bindings">Bindings: <a href="{providers}">'
        "this story&#x27;s Providers page</a></p>"
    ) in html
    assert f'<a class="provider-link" href="{provider}">' in html
    assert html.index("external-values-bindings") < html.index('<form method="get"')


def test_registry_failure_shows_short_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert len(registry_reads(engine)) == 1
    cited = cell(row(html, RD[1]), "cited-by")
    assert "…00000090" in cited
    assert count(html) == "2 reads on this page"
