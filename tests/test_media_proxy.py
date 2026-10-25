"""/media/<id>/content/ (#65, to #70's spec): the media proxy every M3 page shows media through."""

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import media_proxy
from tests.fakes.engine import FakeEngine
from tests.helpers import SERVICE_KEY

pytestmark = pytest.mark.django_db

CONTENT = "/media/{media_id}/content"
MEDIA = "01JC000000000000000000MED1"
VIDEO = "01JC000000000000000000MED2"
URL = f"/media/{MEDIA}/content/"
MEDIA_DIR = Path(__file__).resolve().parent / "fixtures" / "engine" / "media"
PNG = (MEDIA_DIR / "sample.png").read_bytes()
MP4 = (MEDIA_DIR / "sample.mp4").read_bytes()
ETAG = '"sha256-abc123"'
CSP = (
    "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'"
)
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def serve(request: httpx.Request) -> httpx.Response:
    """The Engine's content route over the two sample files: ETag, Range (one range, clamped;
    416 when no byte exists), If-None-Match → 304."""
    media_id = request.url.path.split("/")[2]
    data, mime = {MEDIA: (PNG, "image/png"), VIDEO: (MP4, "video/mp4")}.get(media_id, (b"", ""))
    if not data:
        body = {"error": {"code": "not_found", "message": "no such media", "details": {}}}
        return httpx.Response(404, json=body)
    common = {
        "ETag": ETAG,
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=60",
        "Access-Control-Allow-Origin": "*",
        "Last-Modified": "Wed, 21 Oct 2026 07:28:00 GMT",
        "Vary": "Accept-Encoding",
    }
    if request.headers.get("If-None-Match") == ETAG:
        return httpx.Response(304, headers=common)
    match = RANGE.match(request.headers.get("Range", ""))
    if match:
        first, last = match.groups()
        start = int(first) if first else len(data) - int(last)
        end = min(int(last), len(data) - 1) if first and last else len(data) - 1
        if start >= len(data):
            body = {"error": {"code": "range_not_satisfiable", "message": "x", "details": {}}}
            return httpx.Response(416, json=body, headers={"Content-Range": f"bytes */{len(data)}"})
        headers = {
            **common,
            "Content-Type": mime,
            "Content-Range": f"bytes {start}-{end}/{len(data)}",
        }
        return httpx.Response(206, content=data[start : end + 1], headers=headers)
    return httpx.Response(200, content=data, headers={**common, "Content-Type": mime})


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(media_proxy, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test", ENGINE_SERVICE_KEY=SERVICE_KEY):
        yield fake


@pytest.fixture
def media(engine: FakeEngine) -> FakeEngine:
    engine.side_effect("GET", CONTENT, serve)
    return engine


def body(response: Any) -> bytes:
    return b"".join(response.streaming_content) if response.streaming else response.content


def assert_secured(response: Any) -> None:
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Content-Security-Policy"] == CSP


def assert_no_credential(response: Any) -> None:
    """The browser's answer never carries the Engine credential."""
    assert not response.has_header("Authorization")
    for name, value in response.items():
        assert SERVICE_KEY not in value, name
    assert SERVICE_KEY.encode() not in body(response)


def test_streams_the_whole_content(operator_client: Client, media: FakeEngine) -> None:
    response = operator_client.get(URL)
    assert response.status_code == 200
    assert response.streaming
    assert body(response) == PNG
    assert response["Content-Type"] == "image/png"
    assert response["Content-Length"] == str(len(PNG))
    assert response["ETag"] == ETAG
    assert response["Accept-Ranges"] == "bytes"
    assert response["Cache-Control"] == "private, max-age=31536000, immutable"
    # Nothing outside the allow-list is forwarded.
    for header in ("Last-Modified", "Access-Control-Allow-Origin", "Content-Disposition"):
        assert not response.has_header(header)
    assert "Accept-Encoding" not in response.get("Vary", "")  # the Engine's Vary is dropped
    assert_secured(response)
    (sent,) = media.requests
    assert sent.headers["Authorization"] == f"Bearer {SERVICE_KEY}"  # the Engine gets the key
    assert sent.headers["X-Epistrel-Principal"] == "console:nate"


def test_passes_range_and_answers_206(operator_client: Client, media: FakeEngine) -> None:
    response = operator_client.get(URL, HTTP_RANGE="bytes=0-9")
    assert response.status_code == 206
    assert body(response) == PNG[:10]
    assert response["Content-Range"] == f"bytes 0-9/{len(PNG)}"
    assert response["Content-Length"] == "10"
    assert_secured(response)
    (sent,) = media.requests
    assert sent.headers["Range"] == "bytes=0-9"
    # Complement: the browser's answer never carries the credential.
    assert_no_credential(response)


def test_a_video_range(operator_client: Client, media: FakeEngine) -> None:
    response = operator_client.get(f"/media/{VIDEO}/content/", HTTP_RANGE="bytes=24-")
    assert response.status_code == 206
    assert body(response) == MP4[24:]
    assert response["Content-Type"] == "video/mp4"


def test_passes_if_none_match_and_answers_304(operator_client: Client, media: FakeEngine) -> None:
    response = operator_client.get(URL, HTTP_IF_NONE_MATCH=ETAG, HTTP_IF_RANGE=ETAG)
    assert response.status_code == 304
    assert response.content == b""
    assert response["ETag"] == ETAG
    assert response["Cache-Control"] == "private, max-age=31536000, immutable"
    for header in ("Content-Type", "Accept-Ranges", "Content-Range"):
        assert not response.has_header(header)
    assert response.get("Content-Length", "0") == "0"  # Django's own, for the empty body
    assert_secured(response)
    (sent,) = media.requests
    assert sent.headers["If-None-Match"] == ETAG
    assert sent.headers["If-Range"] == ETAG


def test_416_keeps_content_range(operator_client: Client, media: FakeEngine) -> None:
    response = operator_client.get(URL, HTTP_RANGE="bytes=9999-")
    assert response.status_code == 416
    assert response["Content-Range"] == f"bytes */{len(PNG)}"
    assert json.loads(response.content)["error"]["code"] == "range_not_satisfiable"
    assert_secured(response)


def test_only_the_three_request_headers_and_no_query_are_forwarded(
    operator_client: Client, media: FakeEngine
) -> None:
    operator_client.get(
        f"{URL}?img=1&exp=99&sig=abc",
        HTTP_X_FORWARDED_FOR="10.0.0.1",
        HTTP_ACCEPT="text/html",
    )
    (sent,) = media.requests
    assert sent.url.query == b""
    assert "x-forwarded-for" not in sent.headers
    assert sent.headers["Accept"] == "*/*"


def test_anonymous_is_redirected_to_login(client: Client, media: FakeEngine) -> None:
    response = client.get(URL)
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/")
    assert media.requests == []


def test_get_only(operator_client: Client, media: FakeEngine) -> None:
    assert operator_client.post(URL).status_code == 405
    assert operator_client.head(URL).status_code == 405
    assert media.requests == []


def test_an_engine_404_as_an_image_is_an_svg(
    operator_client: Client, media: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    missing = "01JC000000000000000000NOPE"
    with caplog.at_level(logging.INFO, logger="console.media_proxy"):
        response = operator_client.get(f"/media/{missing}/content/?img=1")
    assert response.status_code == 404
    assert response["Content-Type"] == "image/svg+xml"
    svg = response.content.decode()
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"')
    assert "Image unavailable" in svg
    assert missing in svg
    assert "404 not_found" in svg
    assert f"<title>Image unavailable — {missing} — 404 not_found</title>" in svg
    assert_secured(response)
    (record,) = [r for r in caplog.records if r.name == "console.media_proxy"]
    assert record.levelno == logging.WARNING
    assert missing in record.getMessage()
    assert "404 not_found" in record.getMessage()


def test_an_engine_error_without_img_passes_through(
    operator_client: Client, media: FakeEngine
) -> None:
    missing = "01JC000000000000000000NOPE"
    response = operator_client.get(f"/media/{missing}/content/?img=2")  # only "1" means image
    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert json.loads(response.content) == {
        "error": {"code": "not_found", "message": "no such media", "details": {}}
    }
    assert_secured(response)


def test_svg_values_are_escaped(operator_client: Client, media: FakeEngine) -> None:
    response = operator_client.get("/media/%3Cb%3E/content/?img=1")
    svg = response.content.decode()
    assert "<b>" not in svg
    assert "&lt;b&gt;" in svg


@pytest.mark.parametrize(
    ("failure", "status", "words"),
    [
        (httpx.ConnectError("refused"), 502, "Engine unreachable"),
        (httpx.ReadTimeout("slow"), 504, "Engine timeout"),
    ],
)
def test_no_answer(
    operator_client: Client,
    engine: FakeEngine,
    failure: Exception,
    status: int,
    words: str,
) -> None:
    engine.fail("GET", CONTENT, failure)
    image = operator_client.get(f"{URL}?img=1")
    assert image.status_code == status
    assert image["Content-Type"] == "image/svg+xml"
    assert words in image.content.decode()
    assert MEDIA in image.content.decode()
    assert_secured(image)
    plain = operator_client.get(URL)
    assert plain.status_code == status
    assert plain["Content-Type"].startswith("text/plain")
    assert plain.content.decode() == words
    assert_secured(plain)


def test_not_configured(operator_client: Client, engine: FakeEngine) -> None:
    with override_settings(ENGINE_URL=None):
        image = operator_client.get(f"{URL}?img=1")
        plain = operator_client.get(URL)
    assert image.status_code == 503
    assert "Engine not configured" in image.content.decode()
    assert image["Content-Type"] == "image/svg+xml"
    assert plain.status_code == 503
    assert plain.content.decode() == "Engine not configured"
    assert_secured(image)
    assert_secured(plain)


def test_writes_no_operation_row(operator_client: Client, media: FakeEngine) -> None:
    body(operator_client.get(URL))
    operator_client.get("/media/01JC000000000000000000NOPE/content/?img=1")
    assert not EngineOperation.objects.exists()


def test_nothing_is_logged_per_request_at_info(
    operator_client: Client, media: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="console.media_proxy"):
        body(operator_client.get(URL))
    assert [r for r in caplog.records if r.name == "console.media_proxy"] == []


def test_the_engine_response_is_closed(
    operator_client: Client, media: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[int] = []
    real_close = httpx.Response.close

    def spy(self: httpx.Response) -> None:
        closed.append(1)
        real_close(self)

    monkeypatch.setattr(httpx.Response, "close", spy)
    response = operator_client.get(URL)
    assert body(response) == PNG
    response.close()
    assert closed
