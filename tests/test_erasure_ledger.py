"""The erasure ledger, "Re-apply now" and the erasure boundary (#87)."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineUnreachable
from console.engine.models import ErasureBoundary, ErasureStatus, ReconcileResult
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.templatetags.erasure import code_spans
from console.views import erasure
from console.views.erasure import (
    BANNER_ONE,
    NOT_RAN,
    REAPPLY_LEDGER_DOWN,
    REAPPLY_NO_ANSWER,
    age_text,
    last_run_text,
)
from tests.fakes.engine import FakeEngine
from tests.fakes.erasure_ledger import (
    BOUNDARY,
    RECONCILE,
    STATUS,
    answer_status,
    boundary_body,
    reconcile_body,
    status_body,
)
from tests.ratings.helpers import error, text

pytestmark = pytest.mark.django_db

INDEX = "/erasure/"
PARTIAL = "/erasure/status/"
REAPPLY = "/erasure/reconcile/"
BOUNDARY_PAGE = "/erasure/boundary/"
STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
E_STORY = "01JE0000000000000000ERAS01"
E_CATALOG = "01JE0000000000000000ERAS02"
E_GONE = "01JE0000000000000000ERAS09"
ARI = "01JP0000000000000000PERSA1"
HTMX = {"HX-Request": "true"}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(erasure, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def pending(*rows: tuple[str, str | None]) -> list[dict[str, Any]]:
    return [{"erasure_id": e, "story_id": s} for e, s in rows]


def ledger_html(html: str) -> str:
    start = html.index('id="erasure-ledger"')
    return html[start : html.index("</section>", start)]


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def run_row(**changes: Any) -> EngineOperation:
    """A recorded real run (#86's ``erase`` row) of a story-scope erasure."""
    fields: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "kind": "erase",
        "http_method": "POST",
        "path": "/admin/erasure",
        "request": {
            "body": {
                "subject": {"kind": "persona", "id": ARI},
                "scope": {"story": STORY},
                "dry_run": False,
            }
        },
        "http_status": 200,
        "outcome": "ok",
        "response": {"erasure_id": E_STORY, "residue": 0},
    }
    fields.update(changes)
    return EngineOperation.objects.create(**fields)


# --- the ledger -----------------------------------------------------------------------------------


def test_the_ledger_renders_every_status_field(operator_client: Client, engine: FakeEngine) -> None:
    run_row()  # this Console ran E_STORY, so its pending row links to the result
    answer_status(
        engine,
        pending=pending((E_CATALOG, None), (E_STORY, STORY)),
        pending_count=2,
        applied_count=7,
        last_run_at="2026-10-25T09:00:00Z",
        last_error="OSError",
        attempts={E_STORY: 3},
    )
    section = ledger_html(operator_client.get(INDEX).content.decode())
    shown = text(section)
    assert '<dd class="pending-count">2</dd>' in section
    assert '<dd class="applied-count">7</dd>' in section
    assert '<dd class="ledger-state">Idle</dd>' in section
    assert "2026-10-25 09:00:00 UTC (" in shown
    assert " ago)" in shown
    assert '<dd class="last-error"><code>OSError</code></dd>' in section
    rows = re.findall(r"<tr><td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>(\d+)</td></tr>", section, re.S)
    # The Engine's order (catalog first), attempts 0 when absent.
    assert rows == [
        (f"<code>{E_CATALOG}</code>", "catalog", "0"),
        (
            f'<a href="/erasure/{E_STORY}/"><code>{E_STORY}</code></a>',
            f'<a href="/stories/{STORY}/"><code>{STORY}</code></a>',
            "3",
        ),
    ]
    # Idle: a static read, no polling, "Re-apply now" enabled.
    assert "hx-trigger" not in section
    assert '<button type="submit">Re-apply now</button>' in section
    assert '<a href="/erasure/">Refresh</a>' in section


def test_an_idle_ledger_with_no_run_reads_never_and_none(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_status(engine)
    section = ledger_html(operator_client.get(INDEX).content.decode())
    assert '<dd class="last-run">never</dd>' in section
    assert '<dd class="last-error">none</dd>' in section
    assert "pending-erasures" not in section
    # Re-apply stays enabled at 0 pending.
    assert '<button type="submit">Re-apply now</button>' in section


@pytest.mark.parametrize(
    ("count", "banner"),
    [
        (1, BANNER_ONE),
        (
            3,
            "3 erasures are waiting to be re-applied after a restore; affected records are held "
            "until then",
        ),
    ],
)
def test_the_pending_banner(
    operator_client: Client, engine: FakeEngine, count: int, banner: str
) -> None:
    answer_status(
        engine,
        pending=pending(*[(f"{E_GONE[:-1]}{i}", None) for i in range(count)]),
        pending_count=count,
    )
    html = operator_client.get(INDEX).content.decode()
    assert f'<p class="banner banner-warning pending-banner" role="status">{banner}</p>' in html


def test_no_banner_at_zero_pending(operator_client: Client, engine: FakeEngine) -> None:
    answer_status(engine)
    html = operator_client.get(INDEX).content.decode()
    assert "pending-banner" not in html
    assert "waiting to be re-applied" not in html


def test_a_running_reapply_polls_and_disables_the_button(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_status(engine, in_progress=True)
    section = ledger_html(operator_client.get(INDEX).content.decode())
    assert 'hx-get="/erasure/status/" hx-trigger="every 2s" hx-swap="outerHTML"' in section
    assert '<dd class="ledger-state">Re-apply running now</dd>' in section
    assert '<button type="submit" disabled>Re-apply now</button>' in section


def test_the_poll_partial_stops_once_idle(operator_client: Client, engine: FakeEngine) -> None:
    answer_status(engine, in_progress=True)
    running = operator_client.get(PARTIAL, headers=HTMX).content.decode()
    assert running.lstrip().startswith('<section id="erasure-ledger"')
    assert 'hx-trigger="every 2s"' in running
    assert "Erasures this Console ran" not in running  # the section alone
    answer_status(engine, in_progress=False)
    idle = operator_client.get(PARTIAL, headers=HTMX).content.decode()
    assert "hx-trigger" not in idle


def test_the_poll_keeps_polling_through_a_503(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 503, json=error("database_unavailable", "down"))
    html = operator_client.get(PARTIAL, headers=HTMX).content.decode()
    assert "Engine unavailable — retrying" in html
    assert 'hx-trigger="every 2s"' in html


def test_more_holds_attempts_for_erasures_not_pending(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_status(
        engine,
        pending=pending((E_STORY, STORY)),
        pending_count=1,
        attempts={E_STORY: 1, E_GONE: 4},
    )
    section = ledger_html(operator_client.get(INDEX).content.decode())
    more = section[section.index("<summary>More</summary>") :]
    assert "attempts (not pending)" in more
    assert E_GONE in more
    assert E_STORY not in more


def test_rows_past_the_first_hundred_are_counted(
    operator_client: Client, engine: FakeEngine
) -> None:
    rows = pending(*[(f"01JE000000000000000000{i:04d}", STORY) for i in range(101)])
    answer_status(engine, pending=rows, pending_count=140)
    section = ledger_html(operator_client.get(INDEX).content.decode())
    assert section.count("<tr><td>") == 100
    assert '<p class="pending-more">…and 40 more</p>' in section


def test_unknown_status_keys_are_kept_for_more() -> None:
    status = ErasureStatus.from_json({**status_body(), "next_run_at": "soon"})
    assert status is not None
    assert status.extra == {"next_run_at": "soon"}
    plain = ErasureStatus.from_json(status_body())
    assert plain is not None
    assert plain.extra == {}
    boundary = ErasureBoundary.from_json({**boundary_body(), "notes": []})
    assert boundary is not None
    assert boundary.extra == {"notes": []}
    run = ReconcileResult.from_json({**reconcile_body(), "elapsed_ms": 4})
    assert run is not None
    assert run.extra == {"elapsed_ms": 4}


def test_a_failed_status_read_replaces_only_the_ledger(
    operator_client: Client, engine: FakeEngine
) -> None:
    run_row()
    engine.respond("GET", STATUS, 503, json=error("database_unavailable", "down"))
    html = operator_client.get(INDEX).content.decode()
    section = ledger_html(html)
    assert "Engine error 503 database_unavailable" in section
    assert "pending-count" not in section
    # The rest still renders: Re-apply now, New erasure, and the Console's runs.
    assert '<button type="submit">Re-apply now</button>' in section
    assert "New erasure" in html
    assert f'<a href="/erasure/{E_STORY}/"><code>{E_STORY}</code></a>' in html
    # Complement: no "pending re-apply" badge without a status read.
    assert "pending re-apply" not in html


def test_the_console_runs_list_every_outcome(operator_client: Client, engine: FakeEngine) -> None:
    run_row()
    run_row(
        outcome="error",
        http_status=503,
        error_code="ledger_unavailable",
        response={"error": {"code": "ledger_unavailable", "message": "x", "details": {}}},
        request={
            "body": {
                "subject": {"kind": "catalog_character", "id": ARI},
                "scope": {"catalog": True},
                "dry_run": False,
            }
        },
    )
    run_row(  # a dry run is not listed
        request={"body": {"subject": {"kind": "persona", "id": ARI}, "scope": {}, "dry_run": True}},
        response={"erasure_id": E_GONE},
    )
    answer_status(engine, pending=pending((E_STORY, STORY)), pending_count=1)
    html = operator_client.get(INDEX).content.decode()
    body = html[html.index("Erasures this Console ran") :]
    rows = re.findall(r"<tr><td>.*?</tr>", body, re.S)
    assert len(rows) == 2
    failed, ok = rows  # newest first
    assert "error <code>ledger_unavailable</code>" in failed
    assert "The catalog" in failed
    assert "/erasure/" not in failed.split("</td>")[-2]  # no erasure id: unlinked
    assert f'<a href="/erasure/{E_STORY}/"><code>{E_STORY}</code></a>' in ok
    assert '<span class="badge-pending">pending re-apply</span>' in ok
    assert f'<a href="/stories/{STORY}/"><code>{STORY}</code></a>' in ok
    assert "This story" in ok
    assert "console:nate" in ok
    assert E_GONE not in body


# --- Re-apply now ---------------------------------------------------------------------------------


def test_reapply_ran(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", RECONCILE, 202, json=reconcile_body(settled=2, failed=0, pending_count=1)
    )
    response = operator_client.post(REAPPLY)
    assert response.status_code == 302
    assert response["Location"] == INDEX
    assert flashes(response) == [
        ("success", "Re-apply ran: settled 2, failed 0, pending 1 (run 01JR0000000000000000RUN001)")
    ]
    row = EngineOperation.objects.get(kind="reconcile_erasures")
    assert row.outcome == "ok"  # answered when the run ended: no job follows
    assert row.job_id is None
    assert row.idempotency_key == ""
    assert (row.entity_type, row.entity_id, row.story_id) == (None, None, "")
    (sent,) = engine.requests
    assert "Idempotency-Key" not in sent.headers


def test_reapply_with_failures_is_a_warning(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", RECONCILE, 202, json=reconcile_body(settled=1, failed=2, pending_count=2)
    )
    response = operator_client.post(REAPPLY)
    assert flashes(response)[0][0] == "warning"
    assert "failed 2" in flashes(response)[0][1]


def test_reapply_already_running(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RECONCILE, 202, json=reconcile_body(ran=False))
    response = operator_client.post(REAPPLY)
    assert flashes(response) == [("info", NOT_RAN)]
    assert NOT_RAN == "A re-apply was already running — see status below"


def test_reapply_ledger_unavailable(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", RECONCILE, 503, json=error("ledger_unavailable", "gone"))
    response = operator_client.post(REAPPLY)
    assert flashes(response) == [("error", REAPPLY_LEDGER_DOWN)]


def test_reapply_without_an_answer(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", RECONCILE, EngineUnreachable("timeout"))
    response = operator_client.post(REAPPLY)
    assert flashes(response) == [("error", REAPPLY_NO_ANSWER)]


def test_reapply_is_post_only(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.get(REAPPLY).status_code == 405
    assert engine.requests == []


# --- the boundary ---------------------------------------------------------------------------------


def test_the_boundary_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        BOUNDARY,
        200,
        json=boundary_body(in_scope=["events and beats", "<script>alert(1)</script> **bold**"]),
    )
    html = operator_client.get(BOUNDARY_PAGE).content.decode()
    assert "As stated by this Engine." in html
    in_scope = re.search(r'<ul class="in-scope">(.*?)</ul>', html, re.S)
    out = re.search(r'<ul class="out-of-scope">(.*?)</ul>', html, re.S)
    assert in_scope
    assert out
    assert re.findall(r"<li>(.*?)</li>", in_scope[1]) == [
        "events and beats",
        "&lt;script&gt;alert(1)&lt;/script&gt; **bold**",  # escaped; other markdown literal
    ]
    assert re.findall(r"<li>(.*?)</li>", out[1]) == [
        "exports already delivered",
        "candidates (<code>candidates_remaining</code>)",
    ]
    assert (
        '<p class="logs">Logs never carry story content (<code>EPISTREL_LOG_CONTENT</code> off).'
        "</p>"
    ) in html
    for heading in ("In scope", "Out of scope", "Logs"):
        assert f"<h2>{heading}</h2>" in html


def test_the_boundary_is_read_on_every_view(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BOUNDARY, 200, json=boundary_body())
    operator_client.get(BOUNDARY_PAGE)
    operator_client.get(BOUNDARY_PAGE)
    assert len(engine.requests) == 2


def test_a_failed_boundary_read_is_the_generic_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", BOUNDARY, 503, json=error("database_unavailable", "down"))
    html = operator_client.get(BOUNDARY_PAGE).content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert "In scope" not in html


def test_the_boundary_is_linked_from_the_ledger(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_status(engine)
    html = operator_client.get(INDEX).content.decode()
    assert f'href="{BOUNDARY_PAGE}"' in html


def test_code_spans_escapes_before_formatting() -> None:
    assert code_spans("`<b>` and `x`") == "<code>&lt;b&gt;</code> and <code>x</code>"
    assert code_spans(None) == ""


# --- wording helpers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(2, "just now"), (42, "42 s ago"), (300, "5 min ago"), (7200, "2 h ago"), (90000, "1 d ago")],
)
def test_age_text(seconds: int, shown: str) -> None:
    assert age_text(seconds) == shown


def test_last_run_text() -> None:
    now = datetime(2026, 10, 25, 9, 5, tzinfo=UTC)
    assert last_run_text("2026-10-25T09:00:00Z", now) == "2026-10-25 09:00:00 UTC (5 min ago)"
    assert last_run_text(None, now) == "never"
    assert last_run_text("not a time", now) == "not a time"
