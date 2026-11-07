"""The Effects tab's Outbox section (#325).

Runs on the schema-validating fake Engine, under Full v4 (E16) and the M7 cast
(``m7/story.json``). Fixtures (#292):

- ``m7/outbox.json``: ``XB01`` intended (turn 4, active); ``XB02`` confirmed with its outcome
  (turn 3); ``XB03`` compensated (discarded) by ``XB04``, which compensates it; ``XB05``
  redacted (failed: ids, provider, status and position only);
- ``m7/outbox_bad_item.json``: ``XB01``, an ``XB09`` with only ids and provider, and ``XB03``.

One-off rows (dormant, contingent, imported unsettled, a ``next_after`` body) are inline dicts
(E16).
"""

import copy
import logging
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse

from console.effects import OUTBOX_SECTION_LABEL, OUTBOX_SECTION_NOTICE
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.inspect import Tab, sections
from console.outbox import (
    CONTINGENT_TEXT,
    IMPORTED_UNSETTLED_TITLE,
    NO_MORE_ROWS,
    NO_ROWS,
    NO_ROWS_MATCH,
    REJECTED_ROW_TEXT,
    SCOPE_LINE,
)
from console.paging import CURSOR_DROPPED
from console.positions import knowledge_position_url
from console.reconciliation import NOT_FOUND, REDACTED
from console.rewrites import OMNISCIENT_REQUIRED_TEXT, OUTBOX_OMNISCIENT_LINE
from console.views import inspect as views
from console.world import five_part
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, full_v4, lite_m6
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM7ST0000000000000000001"
EFFECTS = f"/stories/{STORY}/inspect/effects/"
URL = f"{EFFECTS}outbox/"
RECONCILIATION = f"{EFFECTS}reconciliation/"
OUTBOX = "/stories/{story_id}/outbox"
OUTBOX_PATH = f"/stories/{STORY}/outbox"
MINUS = "\u2212"
XB = {n: f"01JM7XB{n:019d}" for n in range(1, 10)}


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m7/{name}"))
    return body


def inline_row(n: int, **changes: Any) -> dict[str, Any]:
    """A full outbox row ``XB<n>`` (confirmed at turn 3 by default) with ``changes``."""
    row: dict[str, Any] = copy.deepcopy(fixture("outbox")["rows"][1])
    row["outbox_id"] = XB[n]
    row.update(changes)
    return row


def body(*rows: dict[str, Any], next_after: str | None = None) -> dict[str, Any]:
    return {"rows": list(rows), "next_after": next_after, "output_viewpoint": "omniscient"}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=full_v4())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("GET", OUTBOX, 200, json=fixture("outbox"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def answer(engine: FakeEngine, status: int, json: Any) -> None:
    engine.respond("GET", OUTBOX, status, json=json)


def page(client: Client, query: str = "", status: int = 200) -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == status
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == OUTBOX_PATH]


def sent(engine: FakeEngine) -> list[tuple[str, str]]:
    """The one outbox read's query, in order."""
    found = reads(engine)
    assert len(found) == 1
    return list(found[0].url.params.multi_items())


def row(html: str, outbox_id: str) -> str:
    found = re.search(rf'<tr id="outbox-{outbox_id}" class="outbox-row">(.*?)</tr>', html, re.S)
    assert found is not None, outbox_id
    return found.group(1)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="outbox-([0-9A-Z]+)" class="outbox-row"', html)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def subnav(html: str) -> list[str]:
    found = re.search(r'<nav class="subtabs" aria-label="Effects sections">(.*?)</nav>', html, re.S)
    assert found is not None
    return re.findall(r">([^<>]+)</(?:a|span)>", found.group(1))


# --- reads ----------------------------------------------------------------------------------------


def test_one_read_after_the_shell(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        OUTBOX_PATH,
    ]
    assert reads(engine)[0].url.query.decode() == "viewpoint=omniscient&limit=50"


def test_omniscient_line_and_no_picker(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert f'<p class="viewpoint">{OUTBOX_OMNISCIENT_LINE}</p>' in html
    assert OUTBOX_OMNISCIENT_LINE == "Viewpoint: Omniscient — every outbox row"
    assert SCOPE_LINE in html
    assert html.index(OUTBOX_OMNISCIENT_LINE) < html.index(SCOPE_LINE)
    # Complement: no picker, and a viewpoint on the URL is never sent.
    assert '<select name="viewpoint"' not in html
    engine.requests.clear()
    html = page(operator_client, f"?viewpoint=character:{XB[1]}&status=confirmed")
    assert sent(engine) == [("viewpoint", "omniscient"), ("status", "confirmed"), ("limit", "50")]
    # Nor kept in the section's links: Clear filters goes to the bare section.
    assert f'<a class="clear-filters" href="{URL}">Clear filters</a>' in html


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert row_ids(html) == [XB[1], XB[2], XB[3], XB[4], XB[5]]  # the Engine's order
    assert '<p class="outbox-count">5 outbox rows on this page</p>' in html
    confirmed = row(html, XB[2])
    assert cell(confirmed, "outbox") == f'<code class="outbox-id">{XB[2]}</code>'  # full (D11)
    assert cell(confirmed, "provider") == (
        '<a class="provider-link" href="/engine/providers/bank/"><code>bank</code></a>'
    )
    assert cell(confirmed, "value") == "<code>balance</code>"
    assert cell(confirmed, "status") == '<span title="confirmed">Confirmed</span>'
    assert f"bank_balance {MINUS}120 — “Mara buys the lantern”" in cell(confirmed, "effect")
    outcome = cell(confirmed, "outcome")
    assert outcome.startswith('<span title="confirmed · dispatch">Confirmed · via Dispatch · ')
    # Declared at through C1, linked to History at the turn.
    url = knowledge_position_url(
        STORY, five_part({"turn": 3, "variant": 1, "slot_k": 0, "beat": 2, "step": 0})
    )
    assert cell(confirmed, "declared-at") == f'<a href="{url}">turn 3 · v1 · beat 2</a>'
    assert cell(confirmed, "timeline") == '<span title="active">Active</span>'
    assert cell(confirmed, "contingent") == "—"
    attempts = cell(confirmed, "attempts")
    assert attempts.startswith("1 attempt<br>created ")
    assert "<br>claimed " in attempts
    assert "<br>sent " in attempts
    # The compensated pair link each other on this page.
    assert cell(row(html, XB[3]), "compensation") == (
        f'Compensated by <a href="#outbox-{XB[4]}"><code class="outbox-id">{XB[4]}</code></a>'
    )
    assert cell(row(html, XB[4]), "compensation") == (
        f'Compensates <a href="#outbox-{XB[3]}"><code class="outbox-id">{XB[3]}</code></a>'
    )
    assert cell(row(html, XB[3]), "timeline") == '<span title="discarded">Discarded</span>'
    # The redacted row: no effect, and none under More.
    redacted = row(html, XB[5])
    assert cell(redacted, "effect") == f'<span class="redacted">{REDACTED}</span>'
    assert REDACTED == "Redacted by an erasure"
    assert cell(redacted, "value") == "—"
    assert cell(redacted, "outcome") == "—"
    assert cell(redacted, "compensation") == "—"
    assert cell(redacted, "attempts").startswith("created ")
    assert "<dt>Effect</dt>" not in redacted
    assert "<dt>Position</dt>" in redacted
    intended = row(html, XB[1])
    assert "<dt>Effect</dt>" in intended
    assert cell(intended, "attempts").startswith("0 attempts<br>created ")
    assert "claimed" not in cell(intended, "attempts")
    assert cell(intended, "outcome") == "—"


def test_dormant_and_contingent_rows(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, body(inline_row(6, membership="dormant", contingent=True)))
    html = page(operator_client)
    dormant = row(html, XB[6])
    assert cell(dormant, "timeline") == '<span title="dormant">Dormant</span>'
    assert cell(dormant, "contingent") == CONTINGENT_TEXT
    assert CONTINGENT_TEXT == "Yes — a story change waits on its outcome"
    assert '<p class="outbox-count">1 outbox row on this page</p>' in html


# --- filters and paging ---------------------------------------------------------------------------


def test_filters_are_sent_and_blank_ones_left_out(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, "?status=confirmed&provider=%20bank%20")
    assert sent(engine) == [
        ("viewpoint", "omniscient"),
        ("status", "confirmed"),
        ("provider", "bank"),
        ("limit", "50"),
    ]
    assert '<option value="confirmed" selected>Confirmed</option>' in html
    assert f'<a class="clear-filters" href="{URL}">Clear filters</a>' in html
    assert "5 outbox rows on this page, matching filters" in html
    # Complement: blank filters send neither, and there is nothing to clear.
    engine.requests.clear()
    html = page(operator_client, "?status=&provider=")
    assert sent(engine) == [("viewpoint", "omniscient"), ("limit", "50")]
    assert "Clear filters" not in html
    assert '<option value="" selected>Any</option>' in html


def test_status_choices(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    options = re.findall(r'<option value="([a-z_]*)"', html)
    assert options == [
        "",
        "intended",
        "claimed",
        "confirmed",
        "failed",
        "unknown",
        "cancelled",
        "compensated",
        "imported_unsettled",
    ]
    assert '<option value="imported_unsettled">Imported unsettled</option>' in html


def test_unknown_status_is_kept(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, body())
    html = page(operator_client, "?status=later_status")
    assert '<option value="later_status" selected>Later status</option>' in html
    assert sent(engine) == [
        ("viewpoint", "omniscient"),
        ("status", "later_status"),
        ("limit", "50"),
    ]


def test_an_overlong_filter_is_a_field_error(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?provider={'p' * 65}")
    assert "at most 64 characters" in html
    assert reads(engine) == []
    assert "outbox-count" not in html


def test_paging_links(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, body(inline_row(6), next_after=XB[6]))
    html = page(operator_client, "?status=confirmed&viewpoint=character")
    assert f'<a href="{URL}?status=confirmed&amp;after={XB[6]}">Next page</a>' in html
    assert "First page" not in html
    # With ``after``, ``after_id`` is sent and "First page" keeps the filters alone.
    engine.requests.clear()
    answer(engine, 200, body(inline_row(7)))
    html = page(operator_client, f"?status=confirmed&after={XB[6].lower()}%20")
    assert sent(engine) == [
        ("viewpoint", "omniscient"),
        ("status", "confirmed"),
        ("after_id", XB[6]),
        ("limit", "50"),
    ]
    assert f'<a href="{URL}?status=confirmed">First page</a>' in html
    assert "Next page" not in html
    assert CURSOR_DROPPED not in html
    # Complement: a bad cursor is dropped and the first page is read.
    engine.requests.clear()
    html = page(operator_client, "?after=not-an-id")
    assert f'<p class="notice cursor-dropped">{CURSOR_DROPPED}</p>' in html
    assert html.index(CURSOR_DROPPED) < html.index('class="outbox-filter"')
    assert sent(engine) == [("viewpoint", "omniscient"), ("limit", "50")]
    assert "First page" not in html


def test_imported_unsettled_row_links_reconciliation(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, 200, body(inline_row(8, status="imported_unsettled", outcome=None)))
    html = page(operator_client)
    status = cell(row(html, XB[8]), "status")
    link = f"{reverse('inspect:reconciliation', args=[STORY])}#outbox-{XB[8]}"
    assert status == (
        '<span title="imported_unsettled">Imported unsettled</span> '
        f'<small class="imported-unsettled"><a href="{link}">{IMPORTED_UNSETTLED_TITLE}</a></small>'
    )
    assert IMPORTED_UNSETTLED_TITLE == (
        "Imported with no final outcome — never sent; any open listing is in Reconciliation"
    )


def test_empty_and_filtered_empty(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, body())
    html = page(operator_client)
    assert f'<p class="none">{NO_ROWS}</p>' in html
    assert NO_ROWS == "No outbox rows — this story has committed no external effect."
    assert "<table" not in html
    assert "outbox-count" not in html
    html = page(operator_client, "?provider=bank")
    assert f'<p class="none">{NO_ROWS_MATCH}</p>' in html
    assert "<table" not in html
    html = page(operator_client, f"?provider=bank&after={XB[5]}")
    assert f'<p class="none">{NO_MORE_ROWS} <a href="{URL}?provider=bank">First page</a></p>' in (
        html
    )
    assert "<table" not in html


def test_a_malformed_row_is_kept_raw(operator_client: Client, engine: FakeEngine) -> None:
    raw = fixture("outbox_bad_item")
    engine.side_effect("GET", OUTBOX, lambda _r: httpx.Response(200, json=raw))
    html = page(operator_client)
    assert row_ids(html) == [XB[1], XB[3]]
    assert '<p class="outbox-count">2 outbox rows on this page</p>' in html
    assert (
        '<p class="notice outbox-unreadable">1 outbox row couldn&#x27;t be read — see More</p>'
        in (html)
    )
    assert html.index("outbox-unreadable") < html.index("<table")
    rejected = re.search(r'<tr class="rejected">(.*?)</tr>', html, re.S)
    assert rejected is not None
    assert f'<td colspan="12">{REJECTED_ROW_TEXT.replace("'", "&#x27;")}' in rejected.group(1)
    assert XB[9] in rejected.group(1)
    # XB03's compensator isn't on this page: it links Reconciliation's row.
    link = f"{RECONCILIATION}#outbox-{XB[4]}"
    assert f'<a href="{link}"><code class="outbox-id">{XB[4]}</code></a>' in row(html, XB[3])


def test_section_never_gated(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6(**{"states.level": "off"}))
    answer(engine, 200, body())
    html = page(operator_client)
    assert len(reads(engine)) == 1
    assert f'<p class="none">{NO_ROWS}</p>' in html
    assert "<h2>Outbox</h2>" in html


# --- errors ---------------------------------------------------------------------------------------


def test_omniscient_required_is_a_console_bug(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    answer(engine, 403, error_json("omniscient_required", "omniscient only"))
    with caplog.at_level(logging.WARNING, logger="console.engine_errors"):
        html = page(operator_client)
    assert OMNISCIENT_REQUIRED_TEXT in html
    logged = [r for r in caplog.records if "omniscient_required" in r.getMessage()]
    assert len(logged) == 1
    assert '<form method="get"' in html  # the filters still render
    assert "<table" not in html
    assert "Effects sections" in html


def test_viewpoint_unresolved_is_the_same_bug(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, error_json("viewpoint_unresolved", "no viewpoint"))
    html = page(operator_client)
    assert OMNISCIENT_REQUIRED_TEXT in html


def test_a_validation_error_on_a_filter_is_beside_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    error = error_json("validation_error", "bad query")
    error["error"]["details"] = {"errors": [{"loc": ["query", "provider"], "msg": "Too odd"}]}
    answer(engine, 422, error)
    html = page(operator_client, "?provider=odd")
    field = re.search(r'<span class="field field-provider">(.*?)</span>', html, re.S)
    assert field is not None
    assert "Too odd" in field.group(1)
    assert 'class="engine-error' not in html
    assert "outbox-count" not in html
    # An unmapped one (query.limit) is #43's panel, no field error.
    error["error"]["details"] = {"errors": [{"loc": ["query", "limit"], "msg": "Limit is odd"}]}
    answer(engine, 422, error)
    html = page(operator_client)
    assert 'class="engine-error' in html
    assert "bad query" in html
    assert '<ul class="errorlist">' not in html


def test_404_text(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 404, error_json("not_found", "no story"))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert NOT_FOUND == "Not found (the story may have been deleted)"
    assert "<h2>Outbox</h2>" in html


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        OUTBOX,
        503,
        json=error_json("erasure_pending", "held"),
        headers={"Retry-After": "30"},
    )
    html = page(operator_client, status=503)
    assert "Held until the erasure ledger is re-applied" in html


def test_importing_story_is_the_importing_page(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 409, error_json("story_importing", job_id="01JM7JB0000000000000000007"))
    html = page(operator_client, status=409)
    assert "This story is being imported" in html


def test_other_errors_are_43s_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond_undeclared("GET", OUTBOX, 500, json=error_json("internal", "boom"))
    html = page(operator_client)
    assert "boom" in html
    assert "<table" not in html
    assert '<form method="get"' in html


# --- the sub-navigation and the retired notice ----------------------------------------------------


def test_effects_subnav_lists_outbox(operator_client: Client, engine: FakeEngine) -> None:
    """The real urlconf: Reconciliation, Outbox, Effects trail (E12)."""
    real = sections(Tab.EFFECTS, STORY, None, full_v4()["effective"])
    assert [link.label for link in real] == ["Reconciliation", "Outbox", "Effects trail"]
    html = page(operator_client)
    assert subnav(html) == ["Reconciliation", "Outbox", "Effects trail"]
    assert f'<a class="active" aria-current="page" href="{URL}">Outbox</a>' in html
    # Complement: the tab's landing still goes to Reconciliation (D3).
    landing = operator_client.get(EFFECTS)
    assert landing.status_code == 302
    assert landing["Location"] == RECONCILIATION


@override_settings(ROOT_URLCONF="tests.urls_without_outbox")
def test_effects_subnav_leaves_outbox_out_while_unregistered() -> None:
    """**Complement (D19, #341):** without ``inspect:outbox`` the Effects sections are
    Reconciliation and Effects trail, with no Outbox row."""
    links = sections(Tab.EFFECTS, STORY, None, full_v4()["effective"])
    assert [link.label for link in links] == ["Reconciliation", "Effects trail"]
    assert all(link.name != "inspect:outbox" for link in links)


def test_the_notice_links_this_section() -> None:
    with override_settings(ROOT_URLCONF="epistrel_console.urls"):
        from console.effects import outbox_list_notice_html

        assert str(outbox_list_notice_html(story_id=STORY)) == OUTBOX_SECTION_NOTICE.replace(
            "{link}", f'<a href="{URL}">{OUTBOX_SECTION_LABEL}</a>'
        )
