"""The Effects tab's Reconciliation section (#249).

Runs on the schema-validating fake Engine, under Structured v3 (#239) and #232's M6 cast
(``m6/story.json``, ``timeline_revision`` 12). Fixtures:

- ``m6/reconciliation.json`` (#232): ``BX01`` unconfirmed (status ``unknown``); ``BX02`` a
  ``late_success_after_expiry`` with ``reverification: failed, ["precondition:ownership"]``;
  ``BX03`` compensated (``confirmed_discarded_origin``) by ``BX04``, which isn't listed; ``BX05``
  redacted (``failed``: ids, status, reason and since only);
- ``m6/reconciliation_actions.json`` (#249): ``BX11`` to ``BX19``, one row per remaining schema
  reason, every effect on ``gold`` with a ``delta`` but ``settlement_conflict``'s
  ``value: "sword"``; ``BX13`` (``reconciliation_required``) is ``claimed``, "gold \u2212500 — “buy
  sword”";
- ``m6/reconciliation_empty.json``: no listings;
- ``m6/reconciliation_bad_item.json`` (#232): ``BX01`` and a ``BX02`` without ``provider``.
"""

import copy
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.reconciliation import (
    EMPTY_TEXT,
    GATE_ID,
    REASON_TEXT,
    REDACTED,
    SCOPE_LINE,
    SETTLEABLE_REASONS,
)
from console.views import inspect as views
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
URL = f"/stories/{STORY}/inspect/effects/reconciliation/"
MINUS = "\u2212"
RECONCILIATION = "/stories/{story_id}/reconciliation"
RECONCILIATION_PATH = f"/stories/{STORY}/reconciliation"
BX = {n: f"01JM6BX{n:019d}" for n in (*range(1, 6), *range(11, 20))}
ACTIONS = "tests.inspect.urls_reconciliation_actions"
UNLINKED = "tests.inspect.urls_without_providers"
NO_ACTIONS = "tests.inspect.urls_without_reconciliation_actions"
NOT_FOUND = "Not found (the story may have been deleted)"
REASON_ROWS = {
    "failed": 11,
    "unknown": 12,
    "reconciliation_required": 13,
    "cancelled_on_reactivation": 14,
    "confirmed_unsettled": 15,
    "confirmed_discarded_origin": 16,
    "settlement_conflict": 17,
    "outcome_conflict": 18,
    "reservation_expired": 19,
}


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
    fake.respond("GET", RECONCILIATION, 200, json=fixture("reconciliation"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("GET", RECONCILIATION, status, json=body)


def page(client: Client) -> str:
    response = client.get(URL)
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == RECONCILIATION_PATH]


def row(html: str, outbox_id: str) -> str:
    found = re.search(rf'<tr id="outbox-{outbox_id}" class="listing">(.*?)</tr>', html, re.S)
    assert found is not None, outbox_id
    return found.group(1)


def row_ids(html: str) -> list[str]:
    return re.findall(r'<tr id="outbox-([0-9A-Z]+)"', html)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def links(row_html: str) -> dict[str, str]:
    """The Actions cell's live links, by label."""
    return {
        label: href.replace("&amp;", "&")
        for href, label in re.findall(r'<a class="action" href="([^"]+)">([^<]+)</a>', row_html)
    }


def count(html: str) -> str | None:
    found = re.search(r'<p class="reconciliation-count">(.*?)</p>', html)
    return found.group(1) if found else None


# --- the section and its read ---------------------------------------------------------------------


def test_one_read_after_the_shell(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("GET", f"/stories/{STORY}/effective-config"),
        ("GET", f"/stories/{STORY}"),
        ("GET", RECONCILIATION_PATH),
    ]
    assert reverse("inspect:reconciliation", args=[STORY]) == URL
    assert f'<a class="active" aria-current="page" href="{URL}">Reconciliation</a>' in html
    assert "<h2>Reconciliation</h2>" in html
    assert SCOPE_LINE.replace("'", "&#x27;") in html


def test_effects_landing_redirects_here(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"/stories/{STORY}/inspect/effects/?x=1")
    assert response.status_code == 302
    assert response["Location"] == URL


def test_rows_render_each_column(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, fixture("reconciliation_actions"))
    html = page(operator_client)
    claimed = row(html, BX[13])
    assert cell(claimed, "outbox") == f'<code class="outbox-id">{BX[13]}</code>'
    assert len(BX[13]) == 26
    assert cell(claimed, "value") == "<code>balance</code>"
    assert cell(claimed, "status") == '<span title="claimed">Claimed</span>'
    assert cell(claimed, "effect") == f"gold {MINUS}500 — “buy sword”"
    for reason, n in REASON_ROWS.items():
        reason_cell = cell(row(html, BX[n]), "reason")
        explained = REASON_TEXT[reason].replace("'", "&#x27;")
        assert reason_cell.startswith(f'<span title="{reason}">')
        assert f"<small>{explained}</small>" in reason_cell
    assert cell(row(html, BX[17]), "effect") == "gold &quot;sword&quot; — “Trade for the sword”"
    outcome = cell(row(html, BX[18]), "outcome")
    assert outcome.startswith('<span title="failed · ingest">Failed · via Ingest · ')
    assert "2026-10-31 19:11:00 UTC" in outcome
    assert cell(claimed, "outcome") == "—"
    # #232's file: the re-verification, and an unlisted compensating row (unlinked).
    answer(engine, 200, fixture("reconciliation"))
    html = page(operator_client)
    assert cell(row(html, BX[2]), "reverification") == (
        '<span title="failed">Failed: the seller no longer owns it</span>'
    )
    assert cell(row(html, BX[1]), "reverification") == "—"
    assert cell(row(html, BX[3]), "compensated-by") == f'<code class="outbox-id">{BX[4]}</code>'
    assert cell(row(html, BX[3]), "status") == '<span title="compensated">Compensated</span>'


def test_compensated_by_links_a_row_on_the_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("reconciliation")
    body["listings"][2]["compensated_by"] = BX[1]
    answer(engine, 200, body)
    html = page(operator_client)
    assert cell(row(html, BX[3]), "compensated-by") == (
        f'<a href="#outbox-{BX[1]}"><code class="outbox-id">{BX[1]}</code></a>'
    )
    assert cell(row(html, BX[1]), "compensated-by") == "—"


def test_rows_keep_the_engine_order(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("reconciliation_actions")
    body["listings"].reverse()
    answer(engine, 200, body)
    assert row_ids(page(operator_client)) == [BX[n] for n in range(19, 10, -1)]


def test_each_row_has_its_outbox_anchor(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert row_ids(html) == [BX[1], BX[2], BX[3], BX[5]]
    for n in (1, 2, 3, 5):
        assert html.count(f'id="outbox-{BX[n]}"') == 1


def test_redacted_row_shows_ids_only(operator_client: Client, engine: FakeEngine) -> None:
    redacted = row(page(operator_client), BX[5])
    assert cell(redacted, "outbox") == f'<code class="outbox-id">{BX[5]}</code>'
    assert cell(redacted, "provider").endswith("<code>bank</code></a>")
    assert cell(redacted, "value") == "—"
    assert cell(redacted, "effect") == f'<span class="redacted">{REDACTED}</span>'
    assert cell(redacted, "outcome") == "—"
    assert cell(redacted, "status") == '<span title="failed">Failed</span>'
    assert "2026-10-31 18:11:00 UTC" in cell(redacted, "since")
    # **Complement:** no effect JSON in its More.
    assert "<dt>Effect</dt>" not in redacted
    assert "raw-effect" not in redacted


def test_provider_unlinked_until_registered(operator_client: Client, engine: FakeEngine) -> None:
    with override_settings(ROOT_URLCONF=UNLINKED):
        html = page(operator_client)
    assert cell(row(html, BX[1]), "provider") == "<code>bank</code>"
    # #240 has registered ``engine:provider`` in the real urlconf, so it links there.
    html = page(operator_client)
    provider = reverse("engine:provider", args=["bank"])
    assert cell(row(html, BX[1]), "provider") == (
        f'<a class="provider-link" href="{provider}"><code>bank</code></a>'
    )


# --- actions --------------------------------------------------------------------------------------


def test_actions_only_for_settleable_reasons(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, fixture("reconciliation_actions"))
    with override_settings(ROOT_URLCONF=ACTIONS):
        html = page(operator_client)
        settle = {n: reverse("inspect:settle-now", args=[STORY, BX[n]]) for n in BX}
        compensate = {n: reverse("inspect:compensate", args=[STORY, BX[n]]) for n in BX}
    for reason, n in REASON_ROWS.items():
        found = links(row(html, BX[n]))
        if reason in SETTLEABLE_REASONS:
            assert set(found) == {"Settle now", "Compensate"}, reason
            assert found["Settle now"].startswith(settle[n] + "?")
            assert found["Compensate"].startswith(compensate[n] + "?")
            settle_q = parse_qs(urlsplit(found["Settle now"]).query)
            compensate_q = parse_qs(urlsplit(found["Compensate"]).query)
            for query in (settle_q, compensate_q):
                assert query["expected_revision"] == ["12"]
                assert query["listed_reason"] == [reason]
                assert query["provider"] == ["bank"]
                assert query["value"] == ["balance"]
                assert query["next"] == [f"{URL}#outbox-{BX[n]}"]
            assert compensate_q["state"] == ["gold"]
            assert "state" not in settle_q
        else:
            # **Complement:** the other reasons' rows carry neither link.
            assert found == {}, reason
            assert cell(row(html, BX[n]), "actions") == ""
    assert "unconfirmed" not in SETTLEABLE_REASONS
    assert GATE_ID not in html  # States are on: nothing greyed


def test_unconfirmed_and_redacted_rows(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("reconciliation")
    body["listings"].append(
        {
            "outbox_id": BX[11],
            "provider": "bank",
            "status": "confirmed",
            "reason": "late_success_after_expiry",
            "since": "2026-10-31T18:12:00Z",
        }
    )
    answer(engine, 200, body)
    with override_settings(ROOT_URLCONF=ACTIONS):
        html = page(operator_client)
    assert links(row(html, BX[1])) == {}  # unconfirmed
    assert links(row(html, BX[5])) == {}  # failed
    redacted = links(row(html, BX[11]))
    assert set(redacted) == {"Settle now", "Compensate"}
    query = parse_qs(urlsplit(redacted["Compensate"]).query)
    assert "value" not in query  # absent, never sent empty
    assert "state" not in query
    assert query["listed_reason"] == ["late_success_after_expiry"]


def test_no_action_links_while_the_routes_are_unregistered(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, 200, fixture("reconciliation_actions"))
    with override_settings(ROOT_URLCONF=NO_ACTIONS):
        html = page(operator_client)
    for n in REASON_ROWS.values():
        assert cell(row(html, BX[n]), "actions") == ""
    assert "Settle now" not in html


def test_settle_now_link_once_registered(operator_client: Client, engine: FakeEngine) -> None:
    """#256: under the real urlconf the late-success row links ``inspect:settle-now`` with
    #249's query; the unconfirmed row doesn't."""
    html = page(operator_client)
    settle = reverse("inspect:settle-now", args=[STORY, BX[2]])
    found = links(row(html, BX[2]))
    assert found["Settle now"].startswith(settle + "?")
    query = parse_qs(urlsplit(found["Settle now"]).query)
    assert query == {
        "expected_revision": ["12"],
        "provider": ["bank"],
        "value": ["balance"],
        "listed_reason": ["late_success_after_expiry"],
        "next": [f"{URL}#outbox-{BX[2]}"],
    }
    # **Complement:** the unconfirmed row carries no Settle now.
    assert "Settle now" not in links(row(html, BX[1]))


def test_actions_greyed_when_states_off(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    answer(engine, 200, fixture("reconciliation_actions"))
    with override_settings(ROOT_URLCONF=ACTIONS):
        html = page(operator_client)
    for n in (16, 17):
        actions = cell(row(html, BX[n]), "actions")
        assert links(row(html, BX[n])) == {}
        assert actions.count('<span class="disabled action" aria-disabled="true" disabled') == 2
        assert actions.count(f'aria-describedby="{GATE_ID}"') == 2
        assert "Settle now</span>" in actions
        assert "Compensate</span>" in actions
    reason = (
        "Needs <code>states.level</code> at <code>prompt_only</code> or above "
        "(this story runs <code>off</code>)"
    )
    assert html.count(f'<small class="gate-reason" id="{GATE_ID}">{reason}</small>') == 1
    # **Complement:** the list itself renders, and nothing was posted.
    assert '<div class="panel unavailable">' not in html
    assert len(row_ids(html)) == 9
    assert [r for r in engine.requests if r.method == "POST"] == []


def test_section_never_gated(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    with override_settings(ROOT_URLCONF=NO_ACTIONS):
        html = page(operator_client)
    assert len(reads(engine)) == 1
    assert row_ids(html) == [BX[1], BX[2], BX[3], BX[5]]
    assert '<div class="panel unavailable">' not in html
    assert GATE_ID not in html  # no action route registered: nothing greyed either


# --- header, count and empty state ----------------------------------------------------------------


def test_empty_list(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, fixture("reconciliation_empty"))
    html = page(operator_client)
    assert f'<p class="none">{EMPTY_TEXT}</p>' in html
    assert "<table" not in html
    assert count(html) is None


def test_count_line(operator_client: Client, engine: FakeEngine) -> None:
    assert count(page(operator_client)) == "4 effects listed"
    body = fixture("reconciliation")
    body["listings"] = body["listings"][:1]
    answer(engine, 200, body)
    assert count(page(operator_client)) == "1 effect listed"


def test_a_malformed_listing_is_kept_raw(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("reconciliation_bad_item")
    engine.side_effect("GET", RECONCILIATION, lambda request: httpx.Response(200, json=body))
    html = page(operator_client)
    assert row_ids(html) == [BX[1]]
    assert (
        '<p class="notice reconciliation-unreadable">1 listing couldn&#x27;t be read — see More</p>'
    ) in html
    assert count(html) == "1 effect listed"
    rejected = re.search(r'<details class="reconciliation-rejected">(.*?)</details>', html, re.S)
    assert rejected is not None
    assert BX[2] in rejected.group(1)
    assert BX[1] not in rejected.group(1)  # only the rejected entry
    # Every entry rejected: the notice replaces the empty line.
    only_bad = {"listings": [body["listings"][1]]}
    engine.side_effect("GET", RECONCILIATION, lambda request: httpx.Response(200, json=only_bad))
    html = page(operator_client)
    assert EMPTY_TEXT not in html
    assert count(html) is None
    assert "1 listing couldn&#x27;t be read" in html


# --- errors ---------------------------------------------------------------------------------------


def test_404_text(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 404, error_json("not_found", "gone"))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert count(html) is None
    assert "Harbour Ledger" in html


def test_503_panel_keeps_the_subnav(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 503, error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert 'class="engine-error"' in html
    assert html.index('class="subtabs"') < html.index('class="engine-error"')
    assert "Harbour Ledger" in html
    assert "<table" not in html


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 503, error_json("erasure_pending", "held"))
    response = operator_client.get(URL)
    assert response.status_code == 503
