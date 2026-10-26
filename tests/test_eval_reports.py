"""The eval reports page (#149): escalation yield and claim detection's shadow report.

Runs on the schema-validating fake Engine with the clock fixed at 2026-10-24 14:03:27 UTC (the
prefill is 2026-10-17 14:03 .. 2026-10-24 14:03). ``tests/fixtures/inspect/eval_yield.json``:
312 claims, 41 escalated; two gate reasons (``low_margin``, ``no_rule``), two shapes (the second
with no valid verdicts: null rates) and two gate-reason-and-shape clusters.
``eval_shadow_page1.json``: 12 sampled, 1 failed, misses ``age`` 2, ``place`` 1,
``inner_state`` 1; samples ``SA1`` (the listed story ``S00``, turn 3, logged op ``QP1``),
``SA2`` (failed, the unlisted ``SX9``, no turn) and ``SA3`` (``S00``, turn 4, unlogged
``QP3``); ``next`` is ``SA3``."""

import copy
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest, QueryDict
from django.test import Client, override_settings
from django.utils import timezone

from console.engine.client import EngineClient
from console.engine.models import EscalationYield
from console.engine_access import engine_client_for
from console.evals import (
    BLANK_HINT,
    DATETIME_INVALID,
    NO_SAMPLES,
    STORIES_UNAVAILABLE,
    categories,
    misses_text,
    yield_summary,
)
from console.models import EngineOperation
from console.templatetags.console_format import percent
from console.views import operations
from tests.fakes.engine import FakeEngine

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).parent / "fixtures" / "inspect"
PAGE = "/operations/evals/"
YIELD = "/admin/evals/escalation-yield"
SHADOW = "/admin/evals/extraction-shadow"
P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
S00, S01, SX9 = P + "S00", P + "S01", P + "SX9"
SA1, SA2, SA3 = P + "SA1", P + "SA2", P + "SA3"
QP1, QP2, QP3 = P + "QP1", P + "QP2", P + "QP3"
NOW = datetime(2026, 10, 24, 14, 3, 27, tzinfo=UTC)
FROM_SENT, TO_SENT = "2026-10-17T14:03:00Z", "2026-10-24T14:03:00Z"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


def summary(sid: str, name: str | None, status: str = "active") -> dict[str, Any]:
    return {
        "id": sid,
        "name": name,
        "owner_principal": "console:nate",
        "status": status,
        "timeline_revision": 3,
        "next_turn_index": 6,
        "content_rating": {"id": P + "R01", "version_id": P + "R02", "name": "General"},
        "created_at": "2026-10-20T08:15:30Z",
        "archived_at": "2026-10-21T08:00:00Z" if status == "archived" else None,
    }


STORIES = {"items": [summary(S00, "The lighthouse"), summary(S01, "Old harbour", "archived")]}


def error(status: int, code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(operations, "engine_client_for", client_with_fake)
    monkeypatch.setattr(timezone, "now", lambda: NOW)
    fake.respond("GET", "/stories", 200, json=STORIES)
    fake.respond("GET", YIELD, 200, json=fixture("eval_yield"))
    fake.respond("GET", SHADOW, 200, json=fixture("eval_shadow_page1"))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{PAGE}{query}")
    assert response.status_code == 200
    return response.content.decode()


def calls(engine: FakeEngine, path: str) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == path]


def sent(engine: FakeEngine, path: str) -> dict[str, str]:
    (call,) = calls(engine, path)
    return dict(call.url.params)


def section(html: str, sid: str) -> str:
    found = re.search(rf'<section id="{sid}">(.*?)</section>', html, re.S)
    assert found is not None, sid
    return found.group(1)


def form(html: str, cls: str) -> str:
    found = re.search(rf'<form [^>]*class="eval-form {cls}">(.*?)</form>', html, re.S)
    assert found is not None, cls
    return found.group(1)


def hidden(form_html: str) -> dict[str, str]:
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form_html))


def value(html: str, name: str) -> str:
    found = re.search(rf'name="{name}" id="id_{name}" value="([^"]*)"', html)
    assert found is not None, name
    return found.group(1)


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def table(html: str, cls: str) -> str:
    found = re.search(rf'<table class="{cls}">(.*?)</table>', html, re.S)
    assert found is not None, cls
    return found.group(1)


def rows(table_html: str) -> list[str]:
    found = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S)
    return [text(re.sub(r"</t[hd]>", " ", r)) for r in found]


def sample(html: str, sid: str) -> str:
    found = re.search(rf'<tr id="sample-{sid}" class="([^"]*)">(.*?)</tr>', html, re.S)
    assert found is not None, sid
    return f'class="{found.group(1)}"' + found.group(2)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def href(html: str, cls: str) -> dict[str, list[str]]:
    found = re.search(rf'<a class="{cls}" href="([^"]*)"', html)
    assert found is not None, cls
    return parse_qs(urlsplit(found.group(1).replace("&amp;", "&")).query, keep_blank_values=True)


def log_operation(op_id: str) -> int:
    row = EngineOperation.objects.create(
        operator_username="nate",
        principal="operator",
        kind="respond",
        http_method="POST",
        path=f"/stories/{S00}/respond",
        story_id=S00,
        outcome="ok",
        http_status=200,
        request={"body": {}, "headers": {}},
        engine_operation_id=op_id,
    )
    return int(row.pk)


# --- the window and the params ---------------------------------------------------------------


def test_no_params_send_the_last_7_days_to_the_minute_and_prefill_them(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert sent(engine, YIELD) == {"from": FROM_SENT, "to": TO_SENT}
    assert sent(engine, SHADOW) == {"from": FROM_SENT, "to": TO_SENT, "limit": "100"}
    for name in ("y_from", "s_from"):
        assert value(html, name) == "2026-10-17T14:03"
    for name in ("y_to", "s_to"):
        assert value(html, name) == "2026-10-24T14:03"
    assert 'type="datetime-local" step="60" name="y_from"' in html
    assert html.count(BLANK_HINT.replace("'", "&#x27;")) == 2


def test_a_blank_field_is_left_out_of_the_call(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?y_from=&y_to=2026-10-20T08:00")
    assert sent(engine, YIELD) == {"to": "2026-10-20T08:00:00Z"}
    assert value(html, "y_from") == ""


def test_seconds_are_accepted(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, "?s_from=2026-10-01T00:00:30&s_to=")
    assert sent(engine, SHADOW) == {"from": "2026-10-01T00:00:30Z", "limit": "100"}


def test_an_unparseable_value_makes_no_call_for_that_report_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, "?s_to=next+tuesday")
    assert calls(engine, SHADOW) == []
    assert DATETIME_INVALID in field(section(html, "extraction-shadow"), "s_to")
    assert value(html, "s_to") == "next tuesday"
    assert len(calls(engine, YIELD)) == 1
    assert len(calls(engine, "/stories")) == 1  # the story list still reads
    assert "312 claims" in html


def test_the_story_the_cursor_and_the_limit_are_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    page(operator_client, f"?s_story={S00}&s_after={SA1}")
    params = sent(engine, SHADOW)
    assert params["story_id"] == S00
    assert params["after"] == SA1
    assert params["limit"] == "100"


def test_the_yield_form_keeps_the_shadow_params_and_the_cursor(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"?s_from=&s_to=2026-10-20T08:00&s_story={S00}&s_after={SA1}")
    assert hidden(form(html, "yield-form")) == {
        "s_from": "",
        "s_to": "2026-10-20T08:00",
        "s_story": S00,
        "s_after": SA1,
    }


def test_the_shadow_form_never_sends_the_cursor(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"?y_from=&y_to=2026-10-20T08:00&s_after={SA1}")
    carried = hidden(form(html, "shadow-form"))
    assert carried == {"y_from": "", "y_to": "2026-10-20T08:00"}
    assert "s_after" not in form(html, "shadow-form")


def test_a_blank_but_present_from_stays_blank_through_the_other_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    page(operator_client, "?y_from=&y_to=&s_from=2026-10-01T00:00")
    assert "from" not in sent(engine, YIELD)
    assert value(page(operator_client, "?y_from=&s_story="), "y_from") == ""


def test_absent_params_are_not_carried(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert hidden(form(html, "yield-form")) == {}
    assert hidden(form(html, "shadow-form")) == {}


def test_last_7_days_drops_only_that_sections_params(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = f"?y_from=2026-10-01T00:00&y_to=&s_from=&s_story={S00}&s_after={SA1}"
    html = page(operator_client, query)
    assert href(section(html, "escalation-yield"), "last-days") == {
        "s_from": [""],
        "s_story": [S00],
        "s_after": [SA1],
    }
    assert href(section(html, "extraction-shadow"), "last-days") == {
        "y_from": ["2026-10-01T00:00"],
        "y_to": [""],
    }


def test_the_windows_the_engine_echoed(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert "Window 2026-10-17 14:03 UTC \u2013 2026-10-24 14:03 UTC, end exclusive" in text(
        section(html, "escalation-yield")
    )
    assert "Window 2026-10-17 14:03 UTC \u2013 2026-10-24 14:03 UTC, end inclusive" in text(
        section(html, "extraction-shadow")
    )


def test_from_after_to_is_still_sent_and_its_422_shows_beside_that_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    message = "the window's from is not after its to"
    engine.respond(
        "GET",
        YIELD,
        422,
        json=error(422, "validation_error", message, field="from", reason="after_to"),
    )
    html = page(operator_client, "?y_from=2026-10-24T00:00&y_to=2026-10-01T00:00")
    assert sent(engine, YIELD) == {"from": "2026-10-24T00:00:00Z", "to": "2026-10-01T00:00:00Z"}
    escaped = message.replace("'", "&#x27;")
    assert escaped in field(section(html, "escalation-yield"), "y_from")
    assert escaped not in section(html, "extraction-shadow")
    assert "SA1" in section(html, "extraction-shadow")


def test_a_422_on_query_to_lands_on_the_to_input(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", SHADOW, 422, json=error(422, "validation_error", "bad bound", field="query.to")
    )
    html = page(operator_client)
    assert "bad bound" in field(section(html, "extraction-shadow"), "s_to")
    assert "312 claims" in section(html, "escalation-yield")


def test_a_422_on_another_field_shows_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET", SHADOW, 422, json=error(422, "validation_error", "bad cursor", field="query.after")
    )
    html = section(page(operator_client, f"?s_after={SA1}"), "extraction-shadow")
    assert '<div class="errors" role="alert"><ul class="errorlist"><li>bad cursor' in html
    assert href(html, "first-page") == {}


def test_a_503_renders_the_panel_in_that_section_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", SHADOW, 503, json=error(503, "database_unavailable", "down"))
    html = page(operator_client)
    shadow = section(html, "extraction-shadow")
    assert 'class="engine-error"' in shadow
    assert "database_unavailable" in shadow
    assert '<select name="s_story"' in shadow  # the form still renders
    assert 'class="engine-error"' not in section(html, "escalation-yield")
    assert "312 claims" in html


def test_an_unreachable_yield_report_still_renders_the_shadow_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", YIELD, httpx.ConnectError("refused"))
    html = page(operator_client)
    assert 'class="engine-error"' in section(html, "escalation-yield")
    assert "SA1" in section(html, "extraction-shadow")


# --- escalation yield --------------------------------------------------------------------------


def test_the_yield_summary(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert "312 claims, 41 escalated, rate 13.1% · reference 20.0%, not a gate" in html


def test_percent_has_one_decimal() -> None:
    assert percent(0.1309) == "13.1%"
    assert percent(0.2) == "20.0%"
    assert percent(None) == "—"


def test_a_null_escalation_rate_keeps_the_counts() -> None:
    body = fixture("eval_yield") | {"claims": 0, "escalated": 0, "escalation_rate": None}
    report = EscalationYield.from_json(body)
    assert report is not None
    assert yield_summary(report) == (
        "0 claims, 0 escalated · No claims checked in this window · reference 20.0%, not a gate"
    )
    gated = EscalationYield.from_json(body | {"is_gate": True})
    assert gated is not None
    assert yield_summary(gated).endswith("reference 20.0%, gate")


def test_the_cluster_tables_render_every_field_and_close_with_totals(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    reasons = table(html, "yield-clusters by-gate-reason")
    assert rows(reasons) == [
        "Gate reason Escalations Skipped (live tightening) Valid verdicts Flips Flip rate "
        "Weighted flip rate",
        "Low margin 30 2 28 6 21.4% 16.1%",
        "No rule 11 0 9 1 11.1% 2.8%",
        "Totals 41 2 37 7 18.9% 12.9%",
    ]
    assert '<span title="low_margin">Low margin</span>' in reasons
    assert '<th title="consequence-weighted">Weighted flip rate</th>' in reasons
    shapes = table(html, "yield-clusters by-shape")
    assert rows(shapes)[1:] == [
        "core|functional|fluent|affirmed 12 0 12 4 33.3% 25.0%",
        "author|set|permanent|negated 2 2 0 0 — —",
        "Totals 41 2 37 7 18.9% 12.9%",
    ]
    assert (
        '<code title="layer|cardinality|temporal|polarity">core|functional|fluent|affirmed</code>'
        in shapes
    )
    both = table(html, "yield-clusters by-gate-reason-shape")
    assert rows(both)[1:] == [
        "Low margin core|functional|fluent|affirmed 10 0 10 3 30.0% 22.5%",
        "No rule — 1 0 1 0 0.0% 0.0%",
        "Totals — 41 2 37 7 18.9% 12.9%",
    ]


def test_an_empty_cluster_table_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", YIELD, 200, json=fixture("eval_yield") | {"by_shape": []})
    html = section(page(operator_client), "escalation-yield")
    assert "by-shape" not in html
    assert re.search(r"<h3>By predicate shape</h3>\s*<p class=\"none\">None</p>", html)


# --- the shadow report -----------------------------------------------------------------------


def test_the_shadow_summary_covers_the_whole_window(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert (
        "Whole window: 12 sampled, 1 failed (extraction call failed) · 340 claims detected, "
        "298 checked · shadow extraction made 55 claims, 4 missed, miss rate 7.3%"
    ) in text(html)


def test_a_null_miss_rate(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SHADOW, 200, json=fixture("eval_shadow_page1") | {"miss_rate": None})
    assert "4 missed, no claims extracted in this window" in text(page(operator_client))


def test_misses_by_category_count_descending_then_name(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert rows(table(html, "miss-categories"))[1:] == ["Age 2", "Inner state 1", "Place 1"]
    assert '<span title="inner_state">Inner state</span>' in html
    assert categories({}) == []
    assert misses_text({}) == "—"


def test_no_misses_reads_none(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", SHADOW, 200, json=fixture("eval_shadow_page1") | {"missed_by_category": {}}
    )
    html = section(page(operator_client), "extraction-shadow")
    assert re.search(r"<h3>Misses by category</h3>\s*<p class=\"none\">None</p>", html)


def test_the_sample_rows(operator_client: Client, engine: FakeEngine) -> None:
    pk = log_operation(QP1)
    html = page(operator_client)
    first = sample(html, SA1)
    assert cell(first, "story") == (
        f'<a href="/stories/{S00}/" title="{S00}">The lighthouse (…4P7R0S00)</a>'
    )
    assert cell(first, "turn") == (
        f'<a href="/stories/{S00}/history/?before_turn=4#turn-3-0">turn 3</a>'
    )
    assert cell(first, "operation") == (
        f'<a href="/operations/{pk}/"><code title="{QP1}">…4P7R0QP1</code></a>'
    )
    assert cell(first, "misses") == "Age \u00d72, Place \u00d71"
    assert '<span title="sampled">Sampled</span>' in first
    assert 'class="sample"' in first
    assert f"<code>{SA1}</code>" in first  # the sample id in More
    assert text(cell(first, "created-at")) == "2026-10-23 09:15:42 UTC"
    # The Engine's order.
    assert html.index(f"sample-{SA1}") < html.index(f"sample-{SA2}") < html.index(f"sample-{SA3}")


def test_a_failed_sample_with_no_turn_and_an_unlisted_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    failed = sample(html, SA2)
    assert 'class="sample failed"' in failed
    assert cell(failed, "turn") == "—"
    assert cell(failed, "story") == f'<code title="{SX9}">…4P7R0SX9</code>'
    assert f"/stories/{SX9}/" not in html
    assert cell(failed, "misses") == "—"


def test_an_unlogged_operation_is_plain_code(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert cell(sample(html, SA3), "operation") == f'<code title="{QP3}">…4P7R0QP3</code>'


def test_no_samples(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET", SHADOW, 200, json=fixture("eval_shadow_page1") | {"items": [], "next": None}
    )
    html = section(page(operator_client), "extraction-shadow")
    assert NO_SAMPLES in html
    assert 'class="paging"' not in html


def test_the_story_select(operator_client: Client, engine: FakeEngine) -> None:
    html = form(page(operator_client, f"?s_story={S00}"), "shadow-form")
    options = re.findall(r'<option value="([^"]*)"( selected)?>([^<]*)</option>', html)
    assert options == [
        ("", "", "All stories"),
        (S00, " selected", "The lighthouse (…4P7R0S00)"),
        (S01, "", "Old harbour (…4P7R0S01) (archived)"),
    ]


def test_a_story_id_not_in_the_list_is_still_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"?s_story={SX9}")
    assert sent(engine, SHADOW)["story_id"] == SX9
    assert f'<option value="{SX9}" selected>…4P7R0SX9 (not in the list)</option>' in html


def test_a_failed_story_list_falls_back_to_a_free_text_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", "/stories", 503, json=error(503, "database_unavailable", "down"))
    html = page(operator_client, f"?s_story={S00}")
    story = field(html, "s_story")
    assert f'<input type="text" name="s_story" id="id_s_story" value="{S00}"' in story
    assert STORIES_UNAVAILABLE in story
    assert sent(engine, SHADOW)["story_id"] == S00
    assert cell(sample(html, SA1), "story") == f'<code title="{S00}">…4P7R0S00</code>'
    assert "312 claims" in html


def test_paging_by_the_cursor(operator_client: Client, engine: FakeEngine) -> None:
    html = section(page(operator_client, f"?s_story={S00}&y_from="), "extraction-shadow")
    following = href(html, "next-page")
    assert following == {
        "s_story": [S00],
        "y_from": [""],
        "s_from": ["2026-10-17T14:03"],  # the window this page sent, pinned
        "s_to": ["2026-10-24T14:03"],
        "s_after": [SA3],
    }
    assert 'class="first-page"' not in html
    query = QueryDict(mutable=True)
    for key, values in following.items():
        query.setlist(key, values)
    second = section(page(operator_client, f"?{query.urlencode()}"), "extraction-shadow")
    second_call = dict(calls(engine, SHADOW)[1].url.params)
    assert second_call["after"] == SA3
    assert second_call["from"] == FROM_SENT
    assert href(second, "first-page") == {
        "s_story": [S00],
        "y_from": [""],
        "s_from": ["2026-10-17T14:03"],
        "s_to": ["2026-10-24T14:03"],
    }


# --- links -------------------------------------------------------------------------------------


def test_the_operations_page_links_eval_reports(operator_client: Client) -> None:
    response = operator_client.get("/operations/")
    assert '<a href="/operations/evals/">Eval reports</a>' in response.content.decode()


def test_the_page_notes_eval_runs_are_not_served(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client)
    assert "<title>Eval reports · Epistrel Console</title>" in html
    assert (
        "Eval runs aren't served by the Engine yet "
        '(<a href="https://github.com/nathanpond/Epistrel/issues/532">nathanpond/Epistrel#532</a>)'
    ) in html


# --- the client and the models -----------------------------------------------------------------


def test_the_client_sends_aware_datetimes_in_utc_with_z(fake_engine: FakeEngine) -> None:
    fake_engine.respond("GET", SHADOW, 200, json=fixture("eval_shadow_page1"))
    client = EngineClient("http://engine.test", "key", transport=fake_engine.transport)
    plus_two = timezone.get_fixed_timezone(120)
    report = client.get_extraction_shadow(
        datetime(2026, 10, 17, 16, 3, tzinfo=plus_two),
        datetime(2026, 10, 24, 14, 3, 0, 500, tzinfo=UTC),
    )
    (call,) = fake_engine.requests
    assert dict(call.url.params) == {
        "from": "2026-10-17T14:03:00Z",
        "to": "2026-10-24T14:03:00.000500Z",
        "limit": "100",
    }
    assert report.window_from == datetime(2026, 10, 17, 14, 3, tzinfo=UTC)
    assert report.next_cursor == SA3
    assert report.items[0].created_at == datetime(2026, 10, 23, 9, 15, 42, tzinfo=UTC)


def test_a_datetime_without_an_offset_fails_the_model() -> None:
    body = fixture("eval_yield")
    assert EscalationYield.from_json(body) is not None
    assert EscalationYield.from_json(body | {"from": "2026-10-17T14:03:00"}) is None
    assert EscalationYield.from_json(body | {"from": "yesterday"}) is None
