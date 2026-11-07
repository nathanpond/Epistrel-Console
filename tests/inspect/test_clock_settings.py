"""Change clock settings (#243): ``inspect:clock-settings`` and ``PATCH /stories/{id}/clock``.

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239).
The page is read from ``m6/clock.json`` (or ``clock_external.json``); a save answers
``m6/clock_settings_written.json`` (``config_revision`` 5). Each test posts back what the
rendered form holds (:func:`form_data`, scraped from the HTML), with its changes."""

import copy
import json
import re
from collections.abc import Iterator
from html.parser import HTMLParser
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import inspect as views
from console.views import inspect_clock
from console.world import ENGINE_NOTHING_CHANGED
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
URL = f"/stories/{STORY}/inspect/world/clock/settings/"
SECTION = f"/stories/{STORY}/inspect/world/clock/"
CLOCK = "/stories/{story_id}/clock"
CLOCK_PATH = f"/stories/{STORY}/clock"
NO_CHANGES = "No changes — nothing was sent."
TABLE_ERRORS = re.compile(r'<div class="errors table-errors" role="alert">(.*?)</div>', re.S)


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
    fake.respond("GET", CLOCK, 200, json=fixture("clock"))
    fake.respond("PATCH", CLOCK, 200, json=fixture("clock_settings_written"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


class _FormScraper(HTMLParser):
    """The settings form's successful controls: inputs, the selected option of each select and
    each textarea's text (buttons left out)."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, str] = {}
        self.inputs: list[str] = []
        self._in_form = False
        self._select: str | None = None
        self._textarea: str | None = None
        self._first: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form" and "clock-settings-form" in a.get("class", ""):
            self._in_form = True
        if not self._in_form:
            return
        if tag == "input" and a.get("name"):
            self.inputs.append(a["name"])
            if a.get("type") != "submit":
                self.data[a["name"]] = a.get("value", "")
        elif tag == "select":
            self._select = a.get("name")
            self._first = None
            self.inputs.append(a.get("name", ""))
        elif tag == "option" and self._select is not None:
            if self._first is None:
                self._first = a.get("value", "")
                self.data.setdefault(self._select, self._first)
            if "selected" in a:
                self.data[self._select] = a.get("value", "")
        elif tag == "textarea":
            self._textarea = a.get("name")
            self.inputs.append(a.get("name", ""))
            self.data[self._textarea or ""] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self.data[self._textarea] += data


def scrape(html: str) -> _FormScraper:
    scraper = _FormScraper()
    scraper.feed(html)
    if "calendar" in scraper.data:
        scraper.data["calendar"] = scraper.data["calendar"].removeprefix("\n")
    return scraper


def form_data(html: str, **changes: str) -> dict[str, str]:
    data = scrape(html).data
    data.update(changes)
    return data


def get(client: Client) -> str:
    response = client.get(URL)
    assert response.status_code == 200
    return response.content.decode()


def patches(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "PATCH" and r.url.path == CLOCK_PATH]


def clock_reads(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method == "GET" and r.url.path == CLOCK_PATH]


def sent(engine: FakeEngine) -> Any:
    (request,) = patches(engine)
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def save(client: Client, data: dict[str, str]) -> Any:
    return client.post(URL, {**data, "op": "save"})


def field_errors(html: str, field: str) -> str:
    found = re.search(rf'<div class="field field-{field}">(.*?)</div>', html, re.S)
    assert found is not None, field
    return found.group(1)


# --- the form ------------------------------------------------------------------------------------


def test_form_initialised_from_the_read(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    data = scrape(html).data
    assert data["mode"] == "paced"
    assert data["provider_value"] == ""
    assert data["max_unprompted_skip_minutes"] == "1440"
    assert data["stop_at_due_events"] == "true"
    assert data["time_awareness"] == "approximate"
    assert json.loads(data["calendar"]) == {"kind": "gregorian"}
    assert data["calendar"] == json.dumps({"kind": "gregorian"}, indent=2)
    pacing = [
        [data[f"pacing-{i}-{f}"] for f in ("from_words", "to_words", "minutes")] for i in range(3)
    ]
    assert pacing == [["0", "5", "0"], ["5", "51", "1"], ["51", "", "2"]]
    assert "pacing-3-from_words" not in data
    bands = [[data[f"bands-{i}-{f}"] for f in ("name", "from", "to")] for i in range(5)]
    assert bands[0] == ["night", "00:00", "05:00"]
    assert bands[4] == ["night", "21:00", "24:00"]
    assert "bands-5-name" not in data
    # The read-only settings are text only, not inputs; there is no anchor control.
    inputs = scrape(html).inputs
    for name in ("grace_minutes", "time_statement_tolerance_minutes", "elapsed_tolerance_factor"):
        assert name not in inputs
    assert not any("anchor" in name for name in inputs)
    assert "Set at creation — this page can't change them" in html
    assert "<li>Grace: 30 min</li>" in html
    assert "<li>Elapsed tolerance factor: 2.0</li>" in html
    # C10: no min/max/pattern on the digit inputs.
    assert 'inputmode="numeric"' in html
    assert " min=" not in html
    assert " max=" not in html
    assert " pattern=" not in html
    assert "<title>Inspect · Change clock settings · " in html
    assert f'<a class="active" aria-current="page" href="{SECTION}">Clock</a>' in html
    assert len(clock_reads(engine)) == 1


def test_kept_mode_and_awareness_extras(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("clock")
    body["settings"]["mode"] = "tidal"
    body["settings"]["time_awareness"] = "vague"
    engine.side_effect("GET", CLOCK, lambda request: httpx.Response(200, json=body))
    html = get(operator_client)
    data = scrape(html).data
    assert data["mode"] == "tidal"
    assert data["time_awareness"] == "vague"
    response = save(operator_client, data)
    assert response.status_code == 302
    assert patches(engine) == []


def test_row_buttons_make_no_call(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), **{"pacing-2-minutes": "3"})
    engine.reset()
    response = operator_client.post(URL, {**data, "op": "add:pacing"})
    assert response.status_code == 200
    added = scrape(response.content.decode()).data
    assert added["pacing-3-from_words"] == ""
    assert added["pacing-2-minutes"] == "3"
    assert 'class="errorlist' not in response.content.decode()  # re-rendered unvalidated
    response = operator_client.post(URL, {**added, "op": "remove:bands-2"})
    removed = scrape(response.content.decode()).data
    assert [removed[f"bands-{i}-name"] for i in range(4)] == [
        "night",
        "morning",
        "evening",
        "night",
    ]
    assert "bands-4-name" not in removed
    assert removed["pacing-2-minutes"] == "3"
    # Add stops at 24 band rows.
    full = dict(removed)
    for i in range(4, 24):
        full.update({f"bands-{i}-name": "", f"bands-{i}-from": "", f"bands-{i}-to": ""})
    full["bands-TOTAL_FORMS"] = "24"
    response = operator_client.post(URL, {**full, "op": "add:bands"})
    capped = response.content.decode()
    assert "bands-23-name" in capped
    assert "bands-24-name" not in capped
    assert 'value="add:bands" disabled' in capped
    # Complement: zero PATCH requests and no clock read, the posted values kept.
    assert patches(engine) == []
    assert clock_reads(engine) == []


def test_row_op_keeps_the_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client))
    response = operator_client.post(URL, {**data, "op": "add:bands"})
    assert scrape(response.content.decode()).data["idempotency_key"] == data["idempotency_key"]


def test_untouched_form_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client))
    response = save(operator_client, data)
    assert response.status_code == 302
    assert response["Location"] == SECTION
    assert flashes(response) == [NO_CHANGES]
    assert patches(engine) == []
    # Complement: the calendar re-indented is still the same JSON.
    data["calendar"] = '{"kind":    "gregorian"}'
    response = save(operator_client, data)
    assert response.status_code == 302
    assert flashes(response)[-1] == NO_CHANGES
    assert patches(engine) == []


def test_patch_holds_only_changed_members(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), **{"pacing-2-minutes": "3"})
    save(operator_client, data)
    assert sent(engine) == {"pacing_table": [[0, 5, 0], [5, 51, 1], [51, None, 3]]}
    engine.reset()
    data = form_data(get(operator_client), **{"bands-0-to": "04:00", "time_awareness": "exact"})
    response = save(operator_client, data)
    body = sent(engine)
    assert set(body) == {"bands", "time_awareness"}
    assert body["time_awareness"] == "exact"
    assert body["bands"][0] == ["night", "00:00", "04:00"]
    # Complement: no key is null except an emptied table.
    assert None not in body.values()
    assert flashes(response) == ["Time awareness, Bands: saved — Configuration revision now 5"]


def test_calendar_change_sends_the_whole_object(
    operator_client: Client, engine: FakeEngine
) -> None:
    calendar = fixture("clock_custom_calendar")["settings"]["calendar"]
    data = form_data(get(operator_client), calendar=json.dumps(calendar))
    save(operator_client, data)
    assert sent(engine) == {"calendar": calendar}


def test_emptied_table_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client))
    for i in range(5):
        for name in ("name", "from", "to"):
            data[f"bands-{i}-{name}"] = ""
    response = save(operator_client, data)
    assert sent(engine) == {"bands": None}
    assert flashes(response) == ["Bands: saved — Configuration revision now 5"]
    # Complement: no [] is ever sent, and the form has no anchor control.
    assert "[]" not in patches(engine)[0].content.decode()
    assert not any("anchor" in name for name in scrape(get(operator_client)).inputs)


def test_wholly_blank_row_is_dropped_partial_row_required(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form_data(get(operator_client))
    data.update({"pacing-3-from_words": "", "pacing-3-to_words": "", "pacing-3-minutes": ""})
    data["pacing-TOTAL_FORMS"] = "4"
    response = save(operator_client, data)
    assert flashes(response) == [NO_CHANGES]
    data["pacing-3-from_words"] = "90"
    response = save(operator_client, data)
    assert response.status_code == 200
    assert "This field is required." in response.content.decode()
    assert patches(engine) == []


@pytest.mark.parametrize("revision", [5, 1, 0, 999])
def test_save_always_flashes_saved(
    operator_client: Client, engine: FakeEngine, revision: int
) -> None:
    # D8 amended by #329 (#270): the clock read carries no configuration revision, so there's no
    # base to compare against; whatever the answer's revision, the saved flash shows.
    written = engine_fixture("m6/clock_settings_written")
    engine.respond("PATCH", CLOCK, 200, json={**written, "config_revision": revision})
    data = form_data(get(operator_client), mode="manual")
    response = save(operator_client, data)
    assert len(patches(engine)) == 1
    assert flashes(response) == [f"Mode: saved — Configuration revision now {revision}"]
    # Complement: ENGINE_NOTHING_CHANGED is never flashed, and the base helper is gone.
    assert ENGINE_NOTHING_CHANGED not in flashes(response)
    assert not hasattr(inspect_clock, "base_revision")


def test_switch_to_external_sends_provider_value(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form_data(get(operator_client), mode="external", provider_value=" bank.story_minutes ")
    response = save(operator_client, data)
    assert sent(engine) == {"mode": "external", "provider_value": "bank.story_minutes"}
    assert flashes(response) == ["Mode, Provider value: saved — Configuration revision now 5"]


def test_external_initial_and_unchanged(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_external"))
    data = form_data(get(operator_client))
    assert data["provider_value"] == "bank.story_minutes"
    assert data["mode"] == "external"
    response = save(operator_client, data)
    assert flashes(response) == [NO_CHANGES]
    response = save(operator_client, {**data, "provider_value": "bank.other"})
    assert sent(engine) == {"provider_value": "bank.other"}


def test_leaving_external_sends_mode_only(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 200, json=fixture("clock_external"))
    data = form_data(get(operator_client), mode="paced")
    save(operator_client, data)
    assert sent(engine) == {"mode": "paced"}


def test_provider_value_under_paced_is_a_field_error_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = form_data(get(operator_client), provider_value="bank.story_minutes")
    response = save(operator_client, data)
    assert response.status_code == 200
    errors = field_errors(response.content.decode(), "provider_value")
    assert "Only an external clock reads a provider value — choose External or clear it" in errors
    assert patches(engine) == []


@pytest.mark.parametrize(
    ("changes", "field", "message"),
    [
        (
            {"max_unprompted_skip_minutes": "-1"},
            "max_unprompted_skip_minutes",
            "Enter a whole number of minutes, 0 or more.",
        ),
        (
            {"max_unprompted_skip_minutes": "5.0"},
            "max_unprompted_skip_minutes",
            "Enter a whole number of minutes, 0 or more.",
        ),
        (
            {"max_unprompted_skip_minutes": ""},
            "max_unprompted_skip_minutes",
            "Enter a whole number of minutes, 0 or more.",
        ),
        (
            {"max_unprompted_skip_minutes": "2147483648"},
            "max_unprompted_skip_minutes",
            "At most 2,147,483,647.",
        ),
        ({"pacing-1-minutes": "x"}, None, "Enter a whole number, 0 or more."),
        ({"bands-1-from": "7:00"}, None, "Enter a time as HH:MM (24:00 for the end of the day)"),
        ({"bands-1-from": "24:00"}, None, "Enter a time as HH:MM (24:00 for the end of the day)"),
        ({"calendar": "{"}, "calendar", "Enter valid JSON"),
        ({"calendar": "[]"}, "calendar", "Enter a JSON object"),
        (
            {"calendar": ""},
            "calendar",
            "Enter the calendar as JSON, e.g. {&quot;kind&quot;: &quot;gregorian&quot;}",
        ),
    ],
)
def test_invalid_inputs_make_no_call(
    operator_client: Client,
    engine: FakeEngine,
    changes: dict[str, str],
    field: str | None,
    message: str,
) -> None:
    data = form_data(get(operator_client), **changes)
    response = save(operator_client, data)
    assert response.status_code == 200
    html = response.content.decode()
    assert message in (field_errors(html, field) if field else html)
    # The posted values and key are kept (local invalid input, D8).
    kept = scrape(html).data
    for name, value in changes.items():
        if name != "calendar":
            assert kept[name] == value
    assert kept["idempotency_key"] == data["idempotency_key"]
    # Complement: zero PATCH requests.
    assert patches(engine) == []


def test_band_to_accepts_24_00(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), **{"bands-3-to": "24:00", "bands-4-from": "24:00"})
    response = save(operator_client, data)
    html = response.content.decode()
    assert response.status_code == 200
    # bands-4-from 24:00 is refused; bands-3-to isn't.
    assert html.count("Enter a time as HH:MM") == 1


def test_saved_flash_and_redirect(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), **{"pacing-2-minutes": "3"})
    response = save(operator_client, data)
    assert response.status_code == 302
    assert response["Location"] == SECTION
    n = fixture("clock_settings_written")["config_revision"]
    assert flashes(response) == [f"Pacing table: saved — Configuration revision now {n}"]
    (request,) = patches(engine)
    assert request.headers["Idempotency-Key"] == data["idempotency_key"]
    # Complement: no expected revision (a configuration write, D8).
    assert "X-Epistrel-Expected-Revision" not in request.headers


def _refuse(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("PATCH", CLOCK, status, json=body)


def test_engine_422_lands_on_its_table(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(
        engine,
        422,
        error_json(
            "validation_error",
            "the bands tile the day: no gap and no overlap",
            field="bands",
            reason="tiling",
        ),
    )
    data = form_data(get(operator_client), **{"bands-0-to": "04:00"})
    response = save(operator_client, data)
    html = response.content.decode()
    assert response.status_code == 200
    errors = TABLE_ERRORS.findall(html)
    assert len(errors) == 1
    assert "the bands tile the day" in errors[0]
    assert html.index("the bands tile the day") > html.index('class="bands-table"')
    kept = scrape(html).data
    assert kept["bands-0-to"] == "04:00"
    assert kept["bands-4-name"] == "night"
    assert kept["idempotency_key"] != data["idempotency_key"]  # D8: an answered refusal

    _refuse(
        engine,
        422,
        error_json(
            "validation_error",
            "a pacing table is rows",
            field="body.pacing_table.2.1",
            reason="shape",
        ),
    )
    response = save(operator_client, form_data(get(operator_client), **{"pacing-2-minutes": "3"}))
    html = response.content.decode()
    errors = TABLE_ERRORS.findall(html)
    assert len(errors) == 1
    assert "a pacing table is rows" in errors[0]
    assert html.index("a pacing table is rows") > html.index('class="pacing-table"')
    assert html.index("a pacing table is rows") < html.index('class="bands-table"')

    _refuse(
        engine,
        422,
        error_json(
            "validation_error",
            "request validation failed",
            errors=[
                {
                    "field": "body.max_unprompted_skip_minutes",
                    "message": "Input should be less than or equal to 2147483647",
                }
            ],
        ),
    )
    response = save(operator_client, form_data(get(operator_client), mode="manual"))
    html = response.content.decode()
    assert "Input should be less than or equal to 2147483647" in field_errors(
        html, "max_unprompted_skip_minutes"
    )
    assert scrape(html).data["mode"] == "manual"


def test_engine_422_beside_a_field(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(
        engine,
        422,
        error_json(
            "validation_error",
            "a year's days are its months' days",
            field="calendar",
            reason="year_days",
        ),
    )
    calendar = {
        "kind": "custom",
        "day_minutes": 600,
        "months": [{"name": "A", "days": 3}],
        "weekdays": ["x"],
        "year_days": 4,
    }
    response = save(operator_client, form_data(get(operator_client), calendar=json.dumps(calendar)))
    assert "a year&#x27;s days are its months&#x27; days" in field_errors(
        response.content.decode(), "calendar"
    )


@override_settings(ROOT_URLCONF="tests.stories.urls_without_providers")
def test_provider_unbound_names_the_binding_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    _refuse(
        engine,
        422,
        error_json("provider_unbound", "not bound", providers=["bank"]),
    )
    data = form_data(get(operator_client), mode="external", provider_value="bank.story_minutes")
    response = save(operator_client, data)
    html = response.content.decode()
    assert "This story has no binding to bank — bind it first" in html
    assert scrape(html).data["provider_value"] == "bank.story_minutes"
    # Without stories:providers (a urlconf lacking #247's page): plain text, no link.
    assert "Story bindings</a>" not in html


@override_settings(ROOT_URLCONF="tests.urls_with_engine_operation")
def test_provider_unbound_links_when_registered(
    operator_client: Client, engine: FakeEngine
) -> None:
    from django.urls import reverse

    _refuse(engine, 422, error_json("provider_unbound", "not bound", providers=["bank"]))
    data = form_data(get(operator_client), mode="external", provider_value="bank.story_minutes")
    html = save(operator_client, data).content.decode()
    url = reverse("stories:providers", args=[STORY])
    assert f'This story has no binding to bank — bind it first <a href="{url}">' in html


def test_archived_refusal(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(engine, 409, error_json("story_archived", "archived"))
    response = save(operator_client, form_data(get(operator_client), mode="manual"))
    html = response.content.decode()
    assert "This story is archived — unarchive it to change the clock&#x27;s settings." in html
    assert scrape(html).data["mode"] == "manual"


def test_story_busy_refusal(operator_client: Client, engine: FakeEngine) -> None:
    _refuse(engine, 423, error_json("story_busy", "busy"))
    response = save(operator_client, form_data(get(operator_client), mode="manual"))
    assert response.status_code == 200
    assert 'class="errorlist clock-refusal"' in response.content.decode()


def test_transport_error_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), mode="manual")
    engine.fail("PATCH", CLOCK, httpx.ConnectError("down"))
    response = save(operator_client, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert scrape(html).data["idempotency_key"] == data["idempotency_key"]
    assert scrape(html).data["mode"] == "manual"


def test_bad_snapshot_reads_the_clock_once(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), **{"pacing-2-minutes": "3"})
    data["snapshot"] = data["snapshot"][:-2] + "xx"
    # The current settings differ from the page's: the diff is against the fresh read.
    current = fixture("clock")
    current["settings"]["pacing_table"][2][2] = 3
    current["settings"]["mode"] = "manual"
    engine.respond("GET", CLOCK, 200, json=current)
    engine.reset()
    response = save(operator_client, data)
    assert len(clock_reads(engine)) == 1
    assert sent(engine) == {"mode": "paced"}
    assert response.status_code == 302


def test_bad_snapshot_and_failed_read(operator_client: Client, engine: FakeEngine) -> None:
    data = form_data(get(operator_client), mode="manual")
    data["snapshot"] = "tampered"
    engine.respond("GET", CLOCK, 503, json=error_json("engine_unavailable", "down"))
    response = save(operator_client, data)
    assert response.status_code == 302
    assert response["Location"] == SECTION
    assert flashes(response) == ["The clock's settings could not be read; nothing was sent."]
    assert patches(engine) == []


def test_failed_read_shows_no_form(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", CLOCK, 404, json=error_json("not_found", "gone"))
    html = get(operator_client)
    assert "Not found (it may have been deleted or truncated)" in html
    assert "clock-settings-form" not in html
    assert 'aria-label="World sections"' in html


def test_not_gated(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = get(operator_client)
    assert "clock-settings-form" in html
    assert "disabled" not in html.split("clock-settings-form")[1].split("add:pacing")[0]
    response = save(operator_client, form_data(html, mode="manual"))
    assert response.status_code == 302
    assert sent(engine) == {"mode": "manual"}
