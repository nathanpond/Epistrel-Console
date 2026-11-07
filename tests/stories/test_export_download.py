"""#319: the bundle download (``stories:export-download``) streams the Engine's archive through
the Console (owner answer 11): the bytes unchanged, the Console's own headers, nothing naming
the Engine; refusals redirect to the job page with a flash; another story's or a failed job
never opens the stream. The bundle is #292's ``m7/bundle.epistrel.tgz``."""

from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest, StreamingHttpResponse
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import exports as export_views
from console.views import story_pages
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE
from tests.inspect.helpers import full_v4
from tests.play.helpers import error_json
from tests.rewrites.helpers import STORY, m7
from tests.stories.test_export import (
    BUNDLE,
    DOWNLOAD_URL,
    FILENAME,
    JOB_URL,
    NOW,
    OTHER_STORY,
    STATUS,
    STATUS_ROUTE,
    STORY_READ,
    job,
)

pytestmark = pytest.mark.django_db

BUNDLE_ROUTE = "/admin/stories/exports/{job_id}/bundle"
BUNDLE_BYTES = (ENGINE / "m7" / "bundle.epistrel.tgz").read_bytes()
BASE_URL = "http://engine.internal"


def bundle_answer(request: httpx.Request) -> httpx.Response:
    headers = {
        "Content-Type": "application/gzip",
        "Content-Length": str(len(BUNDLE_BYTES)),
        "Content-Disposition": 'attachment; filename="engine-name.epistrel.tgz"',
        "ETag": '"engine-etag"',
        "Location": f"{BASE_URL}/x",
    }
    return httpx.Response(200, content=BUNDLE_BYTES, headers=headers)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    fake.respond("GET", STATUS_ROUTE, 200, json=m7("export_job_ready"))
    fake.side_effect("GET", BUNDLE_ROUTE, bundle_answer)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(export_views, "engine_client_for", client_with_fake)
    monkeypatch.setattr("console.portability.timezone.now", lambda: NOW)
    with override_settings(ENGINE_URL=BASE_URL):
        yield fake


@pytest.fixture
def client(operator_client: Client) -> Client:
    return operator_client


def calls(fake: FakeEngine) -> list[tuple[str, str]]:
    return [(r.method, r.url.path) for r in fake.requests]


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def test_download_streams_the_bytes(client: Client, engine: FakeEngine) -> None:
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 200
    assert isinstance(response, StreamingHttpResponse)
    assert b"".join(response.streaming_content) == BUNDLE_BYTES
    assert response["Content-Type"] == "application/gzip"
    assert response["Content-Length"] == str(len(BUNDLE_BYTES))
    assert response["Content-Disposition"] == f'attachment; filename="{FILENAME}"'
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Security-Policy"].startswith("sandbox")
    assert calls(engine) == [("GET", STORY_READ), ("GET", STATUS), ("GET", BUNDLE)]
    [sent] = [r for r in engine.requests if r.url.path == BUNDLE]
    assert "range" not in sent.headers
    assert "if-none-match" not in sent.headers


def test_the_engine_url_never_leaks(client: Client, engine: FakeEngine) -> None:
    response = client.get(DOWNLOAD_URL)
    assert isinstance(response, StreamingHttpResponse)
    b"".join(response.streaming_content)
    for name in ("Location", "ETag"):
        assert name not in response
    assert "engine-name" not in response["Content-Disposition"]
    for _, value in response.items():
        assert BASE_URL not in value
        assert "engine.internal" not in value
    page = client.get(JOB_URL).content.decode()
    assert BASE_URL not in page
    assert "engine.internal" not in page
    # **Complement:** the job page's Download href is a relative Console path.
    assert f'class="download-link" href="{DOWNLOAD_URL}"' in page
    assert DOWNLOAD_URL.startswith("/stories/")


@pytest.mark.parametrize(
    ("status", "code", "flash"),
    [
        (409, "export_not_ready", "This export isn't ready yet — wait for it to finish"),
        (410, "export_expired", "This export has expired — export the story again"),
        (404, "job_not_found", "No such export"),
    ],
)
def test_stream_refusals_redirect_with_a_flash(
    client: Client, engine: FakeEngine, status: int, code: str, flash: str
) -> None:
    if status == 404:
        engine.respond("GET", BUNDLE_ROUTE, status, json=error_json(code))
    else:  # Engine #333: the route's 409/410 aren't declared in its schema
        engine.respond_undeclared("GET", BUNDLE_ROUTE, status, json=error_json(code))
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 302
    assert response["Location"] == JOB_URL
    assert flashes(response) == [flash]
    # **Complement:** no body bytes, and no #44 row written by the download.
    assert response.content == b""
    assert not isinstance(response, StreamingHttpResponse)
    assert EngineOperation.objects.count() == 0


def test_another_code_says_download_failed(client: Client, engine: FakeEngine) -> None:
    engine.respond_undeclared("GET", BUNDLE_ROUTE, 418, json=error_json("teapot_busy"))
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 302
    assert flashes(response) == ["Download failed — Teapot busy"]


def test_wrong_story_never_opens_the_stream(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS_ROUTE, 200, json=job("export_job_ready", story_id=OTHER_STORY))
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 404
    assert "Not found (that export isn't this story's)" in unescape(response.content.decode())
    assert ("GET", BUNDLE) not in calls(engine)


def test_an_unknown_job_never_opens_the_stream(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS_ROUTE, 404, json=error_json("job_not_found"))
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 404
    assert "Not found (no such export)" in response.content.decode()
    assert ("GET", BUNDLE) not in calls(engine)


def test_failed_job_download_says_failed(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS_ROUTE, 200, json=m7("export_job_failed"))
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 302
    assert response["Location"] == JOB_URL
    assert flashes(response) == ["This export failed — export the story again"]
    # **Complement:** no GET …/bundle.
    assert ("GET", BUNDLE) not in calls(engine)


def test_head_answers_the_headers_without_the_stream(client: Client, engine: FakeEngine) -> None:
    response = client.head(DOWNLOAD_URL)
    assert response.status_code == 200
    assert response["Content-Disposition"] == f'attachment; filename="{FILENAME}"'
    assert response["Content-Type"] == "application/gzip"
    assert "Content-Length" not in response
    assert calls(engine) == [("GET", STORY_READ), ("GET", STATUS)]


def test_an_unreachable_stream_is_the_panel_in_the_frame(
    client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", BUNDLE_ROUTE, httpx.ConnectError("down"))
    response = client.get(DOWNLOAD_URL)
    assert response.status_code == 502
    html = response.content.decode()
    assert "Engine unreachable" in html
    assert "<title>Export …00000006 · Harbour Ledger</title>" in html


def test_a_stream_cut_short_is_logged(
    client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield BUNDLE_BYTES[:10]
            raise httpx.ReadError("cut")

    def cut(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Broken(), headers={"Content-Type": "application/gzip"})

    engine.side_effect("GET", BUNDLE_ROUTE, cut)
    response = client.get(DOWNLOAD_URL)
    assert isinstance(response, StreamingHttpResponse)
    with caplog.at_level("WARNING", logger="console.exports"):
        sent = b"".join(response.streaming_content)
    assert BUNDLE_BYTES.startswith(sent)  # it can only end early
    assert any("stream cut short" in r.getMessage() for r in caplog.records)
    assert STORY not in "".join(r.getMessage() for r in caplog.records if "cut" in r.getMessage())
