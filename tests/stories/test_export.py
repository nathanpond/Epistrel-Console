"""#319: the story's Export page (``stories:export``) and the export job page
(``stories:export-job``), against the schema-validating fake Engine over #292's ``m7/story.json``
cast, ``full_v4()`` and the ``m7/export_*.json`` answers: the GET (the frame's two reads and the
local #44 list, never an export), the start (one POST, a 303 to the job page), the refusals, the
polled job block (E7) and its wrong-story and not-found renders."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.job_poll import settle_job
from console.models import EngineOperation
from console.views import exports as export_views
from console.views import story_pages
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import full_v4
from tests.play.helpers import error_json
from tests.rewrites.helpers import STORY, m7

pytestmark = pytest.mark.django_db

JOB = "01JM7JB0000000000000000006"
OTHER_JOB = "01JM7JB0000000000000000009"
OTHER_STORY = "01JM7ST0000000000000000002"
URL = f"/stories/{STORY}/export/"
JOB_URL = f"{URL}{JOB}/"
PARTIAL_URL = f"{JOB_URL}?partial=1"
DOWNLOAD_URL = f"{JOB_URL}bundle/"
EXPORT = f"/admin/stories/{STORY}/export"
STATUS = f"/admin/stories/exports/{JOB}"
BUNDLE = f"{STATUS}/bundle"
STORY_READ = f"/stories/{STORY}"
CONFIG_READ = f"/stories/{STORY}/effective-config"
EXPORT_ROUTE = "/admin/stories/{story_id}/export"
STATUS_ROUTE = "/admin/stories/exports/{job_id}"
KEY = "01JM7KY0000000000000000009"
KEY_INPUT = re.compile(r'name="idempotency_key" value="([^"]+)"')
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
FILENAME = "harbour-ledger-20261106.epistrel.tgz"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    fake.respond("POST", EXPORT_ROUTE, 202, json=m7("export_started"))
    fake.respond("GET", STATUS_ROUTE, 200, json=m7("export_job_running"))

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(export_views, "engine_client_for", client_with_fake)
    monkeypatch.setattr("console.portability.timezone.now", lambda: NOW)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


@pytest.fixture
def client(operator_client: Client) -> Client:
    return operator_client


def calls(fake: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in fake.requests]


def exports(fake: FakeEngine) -> list[httpx.Request]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == EXPORT]


def text(response: Any) -> str:
    return unescape(response.content.decode())


def job(name: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7(name)
    body.update(changes)
    return body


def serve_job(fake: FakeEngine, name: str, **changes: Any) -> None:
    fake.respond("GET", STATUS_ROUTE, 200, json=job(name, **changes))


def queued_row(job_id: str = JOB, story_id: str = STORY, **changes: Any) -> EngineOperation:
    fields: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "user:nate",
        "story_id": story_id,
        "kind": "export_story",
        "http_method": "POST",
        "path": f"/admin/stories/{story_id}/export",
        "outcome": EngineOperation.Outcome.QUEUED,
        "job_id": job_id,
        "response": {"job_id": job_id, "story_id": story_id},
        **changes,
    }
    return EngineOperation.objects.create(**fields)


def section(html: str, css: str) -> str:
    start = html.index(f'class="{css}"')
    return html[start : html.index("</section>", start)]


# --- the Export page ------------------------------------------------------------------------------


def test_get_makes_no_engine_write(client: Client, engine: FakeEngine) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ)]
    html = text(response)
    assert export_views.SNAPSHOT_LINE in html
    assert '<button type="submit">Export now</button>' in html
    assert "No exports from this Console yet." in html
    assert export_views.NOT_LISTED_NOTE in html
    # **Complement:** no POST …/export.
    assert exports(engine) == []
    assert EngineOperation.objects.count() == 0


def test_the_snapshot_line_links_an_import_only_while_registered(
    client: Client, engine: FakeEngine
) -> None:
    html = text(client.get(URL))
    assert 'class="snapshot-line">' + export_views.SNAPSHOT_LINE + "</p>" in html  # D19: plain
    with override_settings(ROOT_URLCONF="tests.urls_with_m7_pages"):
        linked = text(client.get(URL))
    before, _, after = export_views.SNAPSHOT_LINE.partition("an import")
    assert f'{before}<a class="import-link" href="/engine/import/">an import</a>{after}' in linked


def test_recent_exports_from_the_log(client: Client, engine: FakeEngine) -> None:
    older = queued_row("01JM7JB0000000000000000001", outcome=EngineOperation.Outcome.OK)
    newer = queued_row("01JM7JB0000000000000000002", outcome=EngineOperation.Outcome.ERROR)
    EngineOperation.objects.filter(pk=older.pk).update(created_at=NOW - timedelta(hours=2))
    EngineOperation.objects.filter(pk=newer.pk).update(created_at=NOW - timedelta(hours=1))
    queued_row(OTHER_JOB, story_id=OTHER_STORY)
    html = text(client.get(URL))
    rows = section(html, "recent-exports").split('<tr class="recent-export">')[1:]
    assert len(rows) == 2
    assert "01JM7JB0000000000000000002" in rows[0]
    assert ">Error<" in rows[0]
    assert "01JM7JB0000000000000000001" in rows[1]
    assert ">Ok<" in rows[1]
    assert f'href="{URL}01JM7JB0000000000000000002/"><code>' in rows[0]
    assert "2026-11-06 11:00:00 UTC" in rows[0]
    assert "nate" in rows[0]
    assert export_views.NOT_LISTED_NOTE in html
    assert "No exports from this Console yet." not in html
    assert "Showing the latest" not in html
    # **Complement:** the other story's job id is absent.
    assert OTHER_JOB not in html
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ)]


def test_exactly_twenty_rows_say_showing_the_latest(client: Client, engine: FakeEngine) -> None:
    for n in range(21):
        queued_row(f"01JM7JB00000000000000001{n:02d}")
    html = text(client.get(URL))
    assert html.count('<tr class="recent-export">') == 20
    assert "Showing the latest 20" in html


def test_export_now_starts_once_and_redirects(client: Client, engine: FakeEngine) -> None:
    response = client.post(URL, {"idempotency_key": KEY})
    assert response.status_code == 303
    assert response["Location"] == JOB_URL
    [sent] = exports(engine)
    assert sent.headers["Idempotency-Key"] == KEY
    assert sent.content == b""
    assert calls(engine) == [("POST", EXPORT)]
    # **Complement:** no expected revision, and the #44 row is queued.
    assert "x-epistrel-expected-revision" not in sent.headers
    row = EngineOperation.objects.get(kind="export_story")
    assert row.outcome == EngineOperation.Outcome.QUEUED
    assert row.job_id == JOB
    assert row.story_id == STORY


def test_busy_refusal_stays_on_the_page(client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        EXPORT_ROUTE,
        423,
        json=error_json("story_busy", reason="in_flight"),
        headers={"Retry-After": "7"},
    )
    response = client.post(URL, {"idempotency_key": KEY})
    assert response.status_code == 200
    html = text(response)
    assert "Story busy — another turn is running · retry after 7 s" in html
    assert html.index("retry after 7 s") < html.index("Export now</button>")
    # **Complement:** no redirect, one POST only, and a fresh key.
    assert len(exports(engine)) == 1
    assert KEY_INPUT.findall(html)
    assert KEY not in KEY_INPUT.findall(html)


@pytest.mark.parametrize(
    ("code", "status", "shown"),
    [
        ("story_archived", 409, "archived"),
        ("story_paused", 423, "Paused"),
    ],
)
def test_archived_and_paused_refusals_use_c5(
    client: Client, engine: FakeEngine, code: str, status: int, shown: str
) -> None:
    details: dict[str, Any] = {"cause": "rating_check"} if code == "story_paused" else {}
    engine.respond("POST", EXPORT_ROUTE, status, json=error_json(code, **details))
    response = client.post(URL, {"idempotency_key": KEY})
    assert response.status_code == 200
    assert 'class="refusal"' in text(response)
    assert shown.lower() in text(response).lower()
    assert len(exports(engine)) == 1


def test_an_unknown_outcome_keeps_the_key(client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", EXPORT_ROUTE, httpx.ConnectError("down"))
    response = client.post(URL, {"idempotency_key": KEY})
    assert response.status_code == 200
    html = text(response)
    assert 'class="engine-error' in html
    assert KEY_INPUT.findall(html) == [KEY]


def test_another_error_is_the_panel_in_the_frame(client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", EXPORT_ROUTE, 404, json=error_json("story_not_found"))
    response = client.post(URL, {"idempotency_key": KEY})
    assert response.status_code == 200
    html = text(response)
    assert "Engine error 404 story_not_found" in html
    assert "Export now</button>" in html


# --- the job page ---------------------------------------------------------------------------------


def test_job_page_polls_while_running(client: Client, engine: FakeEngine) -> None:
    queued_row()
    partial = client.get(PARTIAL_URL)
    assert partial.status_code == 200
    html = text(partial)
    assert "Exporting…" in html
    assert 'hx-trigger="every 2s"' in html
    assert f'hx-get="{PARTIAL_URL}"' in html
    assert "<html" not in html
    assert calls(engine) == [("GET", STATUS)]
    page = text(client.get(JOB_URL))
    assert "<title>Export …00000006 · Harbour Ledger</title>" in page
    assert 'hx-trigger="every 2s"' in page
    assert ">Refresh</a>" in page
    assert EngineOperation.objects.get().outcome == EngineOperation.Outcome.QUEUED


def test_queued_reads_queued(client: Client, engine: FakeEngine) -> None:
    serve_job(engine, "export_job_running", status="queued")
    html = text(client.get(PARTIAL_URL))
    assert ">Queued<" in html
    assert 'hx-trigger="every 2s"' in html


def test_ready_shows_the_facts(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued_row()
    settled: list[int] = []
    real_settle = settle_job

    def spy(job_id: str, **kwargs: Any) -> int:
        settled.append(real_settle(job_id, **kwargs))
        return settled[-1]

    monkeypatch.setattr(export_views, "settle_job", spy)
    serve_job(engine, "export_job_ready")
    response = client.get(JOB_URL)
    assert response.status_code == 200
    html = text(response)
    assert "Ready to download" in html
    assert "<dt>Size</dt><dd>17.9\xa0KB</dd>" in html
    assert f'<dt>SHA-256</dt><dd><code class="hash">{"e" * 64}</code></dd>' in html
    assert "<dt>Exported at revision</dt><dd>20</dd>" in html
    assert "<dt>Expires</dt><dd><time" in html
    assert "2026-11-12 15:00:00 UTC" in html
    assert (
        f'<a class="download-link" href="{DOWNLOAD_URL}">Download</a> — saves as '
        f"<code>{FILENAME}</code>"
    ) in html
    assert '"format_version": "1.0"' in html  # the raw manifest under More
    # **Complement:** no hx-trigger (terminal), and the row settled ok on this first render.
    assert "hx-trigger" not in html
    assert EngineOperation.objects.get().outcome == EngineOperation.Outcome.OK
    client.get(JOB_URL)
    assert settled == [1, 0]  # the first terminal render settles; a later one changes nothing


def test_the_ready_partial_names_the_file(client: Client, engine: FakeEngine) -> None:
    serve_job(engine, "export_job_ready")
    html = text(client.get(PARTIAL_URL))
    assert f"saves as <code>{FILENAME}</code>" in html
    assert "hx-trigger" not in html
    assert calls(engine) == [("GET", STATUS), ("GET", STORY_READ)]


def test_a_ready_jobs_null_facts_read_a_dash(client: Client, engine: FakeEngine) -> None:
    serve_job(engine, "export_job_ready", size=None, sha256=None, exported_revision=None)
    html = text(client.get(JOB_URL))
    assert "<dt>Size</dt><dd>—</dd>" in html
    assert "<dt>SHA-256</dt><dd>—</dd>" in html
    assert "<dt>Exported at revision</dt><dd>—</dd>" in html
    assert 'class="download-link"' in html


def test_failed_shows_the_error(client: Client, engine: FakeEngine) -> None:
    queued_row()
    serve_job(
        engine,
        "export_job_failed",
        error={"code": "secret_detected", "reason": "service_key_match", "member": "events.jsonl"},
    )
    html = text(client.get(JOB_URL))
    assert ">Failed<" in html
    assert "Secret detected" in html
    assert export_views.EXPORT_ERROR_TEXT["secret_detected"] in html
    assert "Service key match" in html
    assert '"member": "events.jsonl"' in html
    assert "Export again</button>" in html
    # **Complement:** no Download link, and the row settled as an error with its code.
    assert "download-link" not in html
    assert "/bundle/" not in html
    row = EngineOperation.objects.get()
    assert row.outcome == EngineOperation.Outcome.ERROR
    assert row.error_code == "secret_detected"


def test_expired_offers_no_download(client: Client, engine: FakeEngine) -> None:
    queued_row()
    serve_job(engine, "export_job_expired")
    html = text(client.get(JOB_URL))
    assert "Expired (download no longer available)" in html
    assert f'<form method="post" action="{URL}" class="export-again">' in html
    assert "Export again</button>" in html
    # **Complement:** no link to stories:export-download.
    assert DOWNLOAD_URL not in html
    assert "download-link" not in html
    assert EngineOperation.objects.get().outcome == EngineOperation.Outcome.OK


def test_an_erased_expired_export_names_its_code(client: Client, engine: FakeEngine) -> None:
    serve_job(engine, "export_job_expired", error={"code": "erased"})
    html = text(client.get(JOB_URL))
    assert "Expired (download no longer available)" in html
    assert ">Erased</strong>" in html


def test_another_storys_job_is_not_found(client: Client, engine: FakeEngine) -> None:
    queued_row()
    serve_job(engine, "export_job_ready", story_id=OTHER_STORY)
    response = client.get(JOB_URL)
    assert response.status_code == 404
    html = text(response)
    assert "Not found (that export isn't this story's)" in html
    assert "<title>Export …00000006 · Harbour Ledger</title>" in html
    assert 'aria-current="page">Export</a>' in html
    # **Complement:** no Download link, no …/bundle request, and nothing settled.
    assert "download-link" not in html
    assert ("GET", BUNDLE) not in calls(engine)
    assert EngineOperation.objects.get().outcome == EngineOperation.Outcome.QUEUED
    partial = client.get(PARTIAL_URL)
    assert partial.status_code == 200
    assert "Not found (that export isn't this story's)" in text(partial)
    assert "hx-trigger" not in text(partial)


def test_the_story_id_compares_case_insensitively(client: Client, engine: FakeEngine) -> None:
    serve_job(engine, "export_job_running", story_id=STORY.lower())
    assert client.get(JOB_URL).status_code == 200


def test_unknown_job_is_not_found(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS_ROUTE, 404, json=error_json("job_not_found"))
    response = client.get(JOB_URL)
    assert response.status_code == 404
    assert "Not found (no such export)" in text(response)
    partial = client.get(PARTIAL_URL)
    assert partial.status_code == 200
    assert "Not found (no such export)" in text(partial)
    assert "hx-trigger" not in text(partial)


def test_an_unreachable_poll_keeps_polling(client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", STATUS_ROUTE, httpx.ConnectError("down"))
    html = text(client.get(PARTIAL_URL))
    assert "Engine unavailable — retrying" in html
    assert 'hx-trigger="every 2s"' in html


def test_nav_row_appears_and_is_current(client: Client, engine: FakeEngine) -> None:
    html = text(client.get(JOB_URL))
    assert f'<a href="{URL}" aria-current="page">Export</a>' in html
    page = text(client.get(URL))
    assert f'<a href="{URL}" aria-current="page">Export</a>' in page
    assert "<title>Export · Harbour Ledger</title>" in page
