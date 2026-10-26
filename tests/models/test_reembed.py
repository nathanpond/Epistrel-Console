"""The embedding re-embed run (#150): ``/models/embedding/reembed/`` follows the last model
change's re-embed (running or finished, each story's status and progress, polled every 5 s while
running), **Retry failed** goes through #134's confirmed mode, and an embedding mismatch reads as
a configuration error.

Fixtures ``tests/fixtures/inspect/reembed_page1.json`` (stories ``…R01`` running, ``R02`` done
0/0, ``R03`` failed ``EmbeddingDimensionMismatch``, ``R04`` failed ``TimeoutError``, ``R05``
queued; ``next`` = ``…R05``) and ``reembed_page2.json`` (``R06`` done and archived, ``R07``
failed ``EmbeddingVersionMismatch``, ``R08`` done and unnamed, ``RX9`` not in the story list;
the last page)."""

import copy
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.test import Client

from console.engine.client import EngineError
from console.engine.models import ReembedStatus
from console.engine_errors import (
    Degradation,
    degradation_message,
    error_class_fix,
    panel_for,
)
from console.models import EngineOperation
from console.reembed import Run
from tests.fakes.engine import FakeEngine
from tests.models.helpers import IDEMPOTENCY_KEY, calls, error
from tests.models.test_embedding import answer_put, form, hidden, open_edit
from tests.models.test_embedding import flashes as flash_list

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "inspect"
URL = "/models/embedding/reembed/"
RETRY_URL = "/models/embedding/reembed/retry/"
STATUS = "/admin/reembed/status"
RETRY = "/admin/reembed/retry"
STORIES = "/stories"
P = "01JB2Z8Y5Q3W6K9M1N4P7R0"
R01, R02, R03, R04, R05 = (P + f"R0{n}" for n in range(1, 6))
R06, R07, R08, RX9 = P + "R06", P + "R07", P + "R08", P + "RX9"
MISMATCH = (
    "Embedding configuration mismatch: the deployment&#x27;s pinned embedding model doesn&#x27;t "
    "match the configured one. "
    '<a href="/models/embedding/edit/">Fix it on the embedding edit page</a>'
)
POLL = 'hx-trigger="every 5s"'
HTMX = {"HX-Request": "true"}


def page(name: str = "reembed_page1", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return {**copy.deepcopy(body), **over}


def story(sid: str, status: str = "done", done: int = 1, total: int = 1, **over: Any) -> Any:
    return {"story_id": sid, "total": total, "done": done, "status": status, "error": None, **over}


def summary(sid: str, name: str | None, status: str = "active") -> dict[str, Any]:
    return {
        "id": sid,
        "name": name,
        "owner_principal": "console:nate",
        "status": status,
        "timeline_revision": 3,
        "next_turn_index": 6,
        "content_rating": {"id": P + "T2A", "version_id": P + "T2B", "name": "General"},
        "created_at": "2026-10-20T08:15:30Z",
        "archived_at": "2026-10-22T08:00:00Z" if status == "archived" else None,
    }


STORY_LIST = {
    "items": [
        summary(R01, "The lighthouse"),
        summary(R02, "Harbour"),
        summary(R03, "Mill"),
        summary(R04, "Glass"),
        summary(R05, "Orchard"),
        summary(R06, "Old tower", "archived"),
        summary(R07, "Quay"),
        summary(R08, None),
    ]
}


def answer(engine: FakeEngine, body: dict[str, Any] | None = None, stories: bool = True) -> None:
    engine.respond("GET", STATUS, 200, json=body if body is not None else page())
    if stories:
        engine.respond("GET", STORIES, 200, json=STORY_LIST)


def section(html: str) -> str:
    match = re.search(r'<section id="reembed-run".*?</section>', html, re.S)
    assert match
    return match.group(0)


def opening(html: str) -> str:
    match = re.search(r'<section id="reembed-run"[^>]*>', html)
    assert match
    return match.group(0)


def row(html: str, sid: str) -> str:
    match = re.search(rf'<tr id="story-{sid}"[^>]*>.*?</tr>', html, re.S)
    assert match, sid
    return match.group(0)


def fact(html: str, cls: str) -> str:
    match = re.search(rf'<dd class="{cls}">(.*?)</dd>', html, re.S)
    assert match, cls
    return match.group(1).strip()


def get(client: Client, url: str = URL, **extra: Any) -> str:
    response = client.get(url, **extra)
    assert response.status_code == 200
    return response.content.decode()


# --- The run page ------------------------------------------------------------------------------


def test_a_running_run_shows_header_counts_rows_and_polls(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = get(operator_client)
    assert "<title>Re-embed run — Models — Epistrel Console</title>" in html
    assert "Embedding re-embed run" in html
    assert '<a href="/models/">All purposes</a> · <a href="/engine/">Engine</a>' in html
    assert fact(html, "from-version") == "stub:sha256:64"
    assert fact(html, "to-version") == "stub:embed-v2:sha256:64"
    assert "2026-10-24 14:03:27 UTC" in fact(html, "started")
    assert fact(html, "finished") == "running"
    assert "Queued 1 · Running 1 · Done 1 · Failed 2 on this page" in html
    assert POLL in opening(html)
    assert 'hx-get="/models/embedding/reembed/"' in opening(html)
    assert 'hx-swap="outerHTML"' in opening(html)
    assert '<a href="/models/embedding/reembed/">Refresh</a>' in html
    (status,) = calls(engine, "GET", STATUS)
    assert dict(status.url.params) == {"limit": "100"}
    assert len(calls(engine, "GET", STORIES)) == 1


def test_rows_show_name_status_progress_and_error_class_never_a_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = get(operator_client)
    first = row(html, R01)
    assert f'<a href="/stories/{R01}/">The lighthouse</a>' in first
    assert '<td class="status" title="running">Running</td>' in first
    assert '<progress max="2000" value="1234"></progress> 1,234 / 2,000' in first
    failed = row(html, R04)
    assert 'class="failed"' in failed
    assert "<code>TimeoutError</code>" in failed
    assert "config-fix" not in failed
    assert "retry later" not in html.lower()
    assert re.findall(r"<tr id=\"story-([^\"]+)\"", html) == [R01, R02, R03, R04, R05]


def test_a_zero_total_renders_zero_of_zero_without_a_bar(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    cell = re.search(r'<td class="progress">(.*?)</td>', row(get(operator_client), R02))
    assert cell
    assert cell.group(1) == "0 / 0"


def test_a_mismatch_class_reads_as_a_configuration_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    mismatch = row(get(operator_client), R03)
    assert "<code>EmbeddingDimensionMismatch</code>" in mismatch
    assert f'<p class="config-fix">{MISMATCH}</p>' in mismatch


def test_the_second_page_sends_after_and_names_what_it_can(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page("reembed_page2"))
    html = get(operator_client, f"{URL}?after={R05}")
    (status,) = calls(engine, "GET", STATUS)
    assert dict(status.url.params) == {"limit": "100", "after": R05}
    assert f'<a href="/stories/{R06}/">Old tower</a> (archived)' in row(html, R06)
    assert f'<a href="/stories/{R08}/">(unnamed) <code title="{R08}">…4P7R0R08</code></a>' in row(
        html, R08
    )
    assert f'<td class="story"><code title="{RX9}">…4P7R0RX9</code></td>' in row(html, RX9)
    assert f'<p class="config-fix">{MISMATCH}</p>' in row(html, R07)
    assert "on this page" in html
    assert '<a href="/models/embedding/reembed/">First page</a>' in html
    assert "Next page" not in html


def test_a_failed_story_list_read_renders_every_story_as_its_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, stories=False)
    engine.respond("GET", STORIES, 503, json=error("database_unavailable"))
    html = get(operator_client)
    assert f'<td class="story"><code title="{R01}">…4P7R0R01</code></td>' in row(html, R01)
    assert "The lighthouse" not in html


def test_the_first_page_links_the_next(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = get(operator_client)
    assert f'<a href="/models/embedding/reembed/?after={R05}">Next page</a>' in html
    assert "First page" not in html


def test_finished_at_set_reads_finished_and_stops_polling(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = page(
        "reembed_page2",
        finished_at="2026-10-24T14:20:00Z",
        stories=[story(R01), story(R02, "failed", error="TimeoutError")],
    )
    answer(engine, body)
    html = get(operator_client)
    assert "2026-10-24 14:20:00 UTC" in fact(html, "finished")
    assert "hx-trigger" not in section(html)
    assert "Refresh" not in html
    assert "Queued 0 · Running 0 · Done 1 · Failed 1</p>" in html  # one page: not "on this page"


def test_a_started_run_with_zero_stories_is_finished_and_does_not_poll(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page("reembed_page2", stories=[]), stories=False)
    html = get(operator_client)
    assert fact(html, "finished") == (
        'finished <span class="no-stories">Finished — no story had memories to re-embed.</span>'
    )
    assert "hx-trigger" not in section(html)
    assert "Retry failed" not in html
    assert calls(engine, "GET", STORIES) == []


def test_a_first_and_only_page_with_nothing_active_is_finished(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page("reembed_page2"))
    html = get(operator_client)
    assert fact(html, "finished") == "finished"
    assert "hx-trigger" not in section(html)


def test_a_last_page_with_all_done_but_after_set_still_polls(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page("reembed_page2", stories=[story(R06), story(R08)]))
    html = get(operator_client, f"{URL}?after={R05}")
    assert fact(html, "finished") == "running"
    assert POLL in opening(html)
    assert f'hx-get="/models/embedding/reembed/?after={R05}"' in opening(html)


def test_no_model_change_yet_is_the_empty_state(
    operator_client: Client, engine: FakeEngine
) -> None:
    empty: dict[str, Any] = {
        "from_version": None,
        "to_version": None,
        "started_at": None,
        "finished_at": None,
        "stories": [],
        "next": None,
    }
    answer(engine, empty, stories=False)
    html = get(operator_client)
    assert "No embedding model change yet — nothing to re-embed." in html
    assert "hx-trigger" not in section(html)
    assert "From version" not in html


def test_a_null_from_version_is_the_first_pin(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, page(from_version=None))
    assert fact(get(operator_client), "from-version") == "none (first pin)"


def test_an_unknown_status_is_humanised_and_counted_nowhere(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, page(stories=[story(R01, "being_checked")], next=None))
    html = get(operator_client)
    assert '<td class="status" title="being_checked">Being checked</td>' in html
    assert "Queued 0 · Running 0 · Done 0 · Failed 0" in html


# --- Polling -----------------------------------------------------------------------------------


def test_the_poll_returns_the_section_alone_without_flashes(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    response = operator_client.get(URL, headers=HTMX)
    html = response.content.decode()
    assert html.strip().startswith('<section id="reembed-run"')
    assert "<title>" not in html
    assert 'class="messages"' not in html
    assert POLL in opening(html)


def test_the_poll_keeps_its_cursor(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, page("reembed_page2", stories=[story(R06, "running")]))
    html = get(operator_client, f"{URL}?after={R05}", headers=HTMX)
    (status,) = calls(engine, "GET", STATUS)
    assert status.url.params["after"] == R05
    assert f'hx-get="/models/embedding/reembed/?after={R05}"' in opening(html)


@pytest.mark.parametrize(
    "fail",
    [
        lambda e: e.fail("GET", STATUS, httpx.ConnectError("refused")),
        lambda e: e.respond("GET", STATUS, 503, json=error("database_unavailable")),
    ],
)
def test_an_unavailable_poll_keeps_polling_with_the_notice(
    operator_client: Client, engine: FakeEngine, fail: Any
) -> None:
    fail(engine)
    html = get(operator_client, headers=HTMX)
    assert "Engine unavailable — retrying" in html
    assert POLL in opening(html)
    assert "From version" not in html


def test_any_other_poll_error_stops_on_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STATUS, 422, json=error("validation_error", "bad cursor"))
    html = get(operator_client, headers=HTMX)
    assert "Engine error 422 validation_error" in html
    assert "hx-trigger" not in opening(html)


def test_a_failed_page_read_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 503, json=error("database_unavailable"))
    html = get(operator_client)
    assert "Engine error 503 database_unavailable" in html
    assert "hx-trigger" not in opening(html)


# --- Links -------------------------------------------------------------------------------------


def test_the_url_names() -> None:
    from django.urls import reverse

    assert reverse("models:reembed") == URL
    assert reverse("models:reembed-retry") == RETRY_URL


def test_the_embedding_row_links_the_run(operator_client: Client, engine: FakeEngine) -> None:
    from tests.models.helpers import answer_list

    answer_list(engine)
    html = get(operator_client, "/models/")
    embedding = re.search(r'<tr id="purpose-embedding">(.*?)</tr>', html, re.S)
    assert embedding
    assert f'<a href="{URL}">Re-embed run</a>' in embedding.group(1)
    default = re.search(r'<tr id="purpose-default">(.*?)</tr>', html, re.S)
    assert default
    assert "Re-embed run" not in default.group(1)
    assert calls(engine, "GET", STATUS) == []  # the list shows the link only


# --- #59's model change lands here ---------------------------------------------------------------


def test_a_confirmed_model_change_lands_on_the_run_page_with_its_flash(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = open_edit(operator_client, engine)
    first = operator_client.post(
        "/models/embedding/edit/", form(snapshot, model="mxbai-embed-large")
    )
    answer_put(engine, model="mxbai-embed-large")
    answer(engine)
    response = operator_client.post(
        "/models/embedding/edit/", hidden(first.content.decode()), follow=True
    )
    assert response.redirect_chain == [(URL, 302)]
    assert flash_list(response) == [
        ("success", "Embedding model changed — re-embedding started (config revision 6)")
    ]


def test_a_timeout_only_save_lands_on_models(operator_client: Client, engine: FakeEngine) -> None:
    from tests.models.helpers import answer_list

    snapshot = open_edit(operator_client, engine)
    answer_put(engine, params={"timeout": 45.0})
    answer_list(engine)
    response = operator_client.post(
        "/models/embedding/edit/", form(snapshot, timeout="45"), follow=True
    )
    assert response.redirect_chain == [("/models/", 302)]
    assert calls(engine, "GET", STATUS) == []


# --- Retry failed ------------------------------------------------------------------------------


def test_retry_shows_with_a_failed_story_or_a_next_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = get(operator_client)
    match = re.search(r'<form method="post" action="([^"]+)" class="reembed-retry">', html)
    assert match
    assert match.group(1) == RETRY_URL
    assert re.search(r'name="idempotency_key" value="[0-9A-Z]{26}"', html)
    engine.reset()
    answer(engine, page(stories=[story(R01)]))  # no failure here, but a next page
    assert "Retry failed" in get(operator_client)
    engine.reset()
    answer(engine, page("reembed_page2", stories=[story(R01)]))  # one page, no failure
    assert "Retry failed" not in get(operator_client)


def test_the_retry_confirm_makes_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, RETRY_URL)
    assert (
        "Retry every story whose latest re-embed job failed? The Engine enqueues a new job for "
        "each, including failed stories on other pages." in html
    )
    assert f'<a href="{URL}">Cancel</a>' in html
    assert "Retry failed</button>" in html
    response = operator_client.post(RETRY_URL, {"idempotency_key": IDEMPOTENCY_KEY})
    carried = hidden(response.content.decode())
    assert carried["confirmed"] == "1"
    assert carried["idempotency_key"] == IDEMPOTENCY_KEY
    again = operator_client.post(RETRY_URL, {**carried, "confirmed": "0"})
    assert "Retry every story" in again.content.decode()
    assert engine.requests == []
    assert EngineOperation.objects.count() == 0


@pytest.mark.parametrize(
    ("retried", "level", "text"),
    [
        ([R03], "success", "Retried 1 story"),
        ([R03, R04, R07], "success", "Retried 3 stories"),
        ([], "info", "Nothing to retry"),
    ],
)
def test_a_confirmed_retry_posts_and_flashes(
    operator_client: Client, engine: FakeEngine, retried: list[str], level: str, text: str
) -> None:
    engine.respond(
        "POST",
        RETRY,
        200,
        json={"retried": retried, "config_revision": 7, "idempotency_key": IDEMPOTENCY_KEY},
    )
    answer(engine)
    response = operator_client.post(
        f"{RETRY_URL}?after={R05}",
        {"idempotency_key": IDEMPOTENCY_KEY, "confirmed": "1"},
        follow=True,
    )
    (post,) = calls(engine, "POST", RETRY)
    assert post.headers["Idempotency-Key"] == IDEMPOTENCY_KEY
    assert post.extensions["timeout"]["read"] == 10.0
    assert response.redirect_chain == [(URL, 302)]  # the run's first page
    assert flash_list(response) == [(level, text)]
    op = EngineOperation.objects.get()
    assert op.kind == "retry_reembed"
    assert op.entity_type is None
    assert op.entity_id is None
    assert op.story_id == ""


def test_a_refused_retry_renders_the_error_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", RETRY, 409, json=error("config_conflict", "config changed"))
    response = operator_client.post(
        RETRY_URL, {"idempotency_key": IDEMPOTENCY_KEY, "confirmed": "1"}
    )
    assert response.status_code == 200
    assert "Engine error 409 config_conflict" in response.content.decode()


# --- engine_errors -----------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["embedding_dimension_mismatch", "embedding_version_mismatch"])
def test_a_mismatch_code_is_a_configuration_panel(code: str) -> None:
    panel = panel_for(EngineError(code, "pinned 768, got 1024", {"pinned": 768}, 409))
    assert panel.kind == "config"
    assert panel.headline == "Configuration error"
    assert (panel.http_status, panel.code, panel.message) == (409, code, "pinned 768, got 1024")
    assert panel.fix is not None
    assert panel.fix.url == "/models/embedding/edit/"
    assert "retry later" not in f"{panel.headline} {panel.fix.text}".lower()


def test_the_configuration_panel_renders_its_fix_line(rf: Any) -> None:
    from django.template.loader import render_to_string

    panel = panel_for(EngineError("embedding_version_mismatch", "m", {}, 409))
    html = render_to_string("console/_engine_error.html", {"engine_error": panel})
    assert 'class="engine-error config-error"' in html
    assert "<strong>Configuration error</strong>" in html
    assert "<code>embedding_version_mismatch</code>" in html
    assert f'<p class="config-fix">{MISMATCH}</p>' in html


def test_other_codes_stay_engine_panels() -> None:
    panel = panel_for(EngineError("config_conflict", "m", {}, 409))
    assert panel.kind == "engine"
    assert panel.fix is None


@pytest.mark.parametrize("cls", ["EmbeddingDimensionMismatch", "EmbeddingVersionMismatch"])
def test_a_mismatch_class_has_the_fix(cls: str) -> None:
    fix = error_class_fix(cls)
    assert fix is not None
    assert fix.text == (
        "Embedding configuration mismatch: the deployment's pinned embedding model doesn't match "
        "the configured one."
    )
    assert fix.link == "Fix it on the embedding edit page"
    assert error_class_fix("TimeoutError") is None
    assert error_class_fix(None) is None


def test_embedding_pending_is_the_re_embedding_text_and_run_url() -> None:
    assert degradation_message("embedding_pending") == Degradation(
        "Re-embedding — this story's search is lexical-only until it finishes", URL
    )


@pytest.mark.parametrize("cause", ["embedding_unavailable", "something_new"])
def test_other_degradations_are_none(cause: str) -> None:
    assert degradation_message(cause) is None


# --- The finished rule (unit) ---------------------------------------------------------------------


def status(**over: Any) -> ReembedStatus:
    parsed = ReembedStatus.from_json(page(**over))
    assert parsed is not None
    return parsed


@pytest.mark.parametrize(
    ("over", "after", "finished"),
    [
        ({}, None, False),  # running and queued stories, a next page
        ({"finished_at": "2026-10-24T14:20:00Z"}, None, True),
        ({"stories": [], "next": None}, None, True),
        ({"stories": [], "next": None}, R05, False),  # not the first page
        ({"stories": [story(R01)], "next": None}, None, True),
        ({"stories": [story(R01)]}, None, False),  # a next page exists
        ({"stories": [story(R01, "queued")], "next": None}, None, False),
        ({"started_at": None, "stories": [story(R01)], "next": None}, None, False),
    ],
)
def test_the_finished_rule(over: dict[str, Any], after: str | None, finished: bool) -> None:
    run = Run(after=after, status=status(**over))
    assert run.finished is finished
