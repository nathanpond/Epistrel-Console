"""#320: import a bundle as a new story from the Engine panel (FR-STORE-7, FR-ADMIN-4; Engine
#250/#251; E8, E11, E14, E19, E21), over #292's ``m7/import_impact.json``,
``m7/import_started.json``, ``m7/presets.json``, the ratings fixture and
``m7/bundle.epistrel.tgz`` as the upload, with ``settings.DATA_DIR`` in ``tmp_path``."""

import copy
import json
import os
import re
import time
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.imports import (
    CORRUPT_REASON_TEXT,
    PROFILE_DEFAULT,
    PROFILES_UNREAD,
    RATING_DEFAULT,
    RATINGS_UNREAD,
    RESTORE_LINE,
)
from console.import_spool import IMPORT_SPOOL_EXPIRED
from console.models import EngineOperation
from console.views import imports as import_views
from console.views import story_pages
from console.views.imports import NOT_LISTED_NOTE, NOTHING_CREATED_YET
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE, engine_fixture

pytestmark = pytest.mark.django_db

URL = "/engine/import/"
IMPORT = "/admin/stories/import"
RATINGS = "/admin/content-ratings"
PROFILES = "/engine-profiles"
KEY = "01JM7KY0000000000000000009"
CONFIRM_KEY = "01JM7KY0000000000000000008"
JOB = "01JM7JB0000000000000000007"
NEW_STORY = "01JM7ST0000000000000000003"
CUSTOM = "01JM7PF0000000000000000001"
BUNDLE = (ENGINE / "m7" / "bundle.epistrel.tgz").read_bytes()
STUBS = "tests.urls_with_m7_pages"
JOB_URL = f"/engine/import/{JOB}/"


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def custom_profile(**changes: Any) -> dict[str, Any]:
    full = next(p for p in m7("presets")["items"] if p["name"] == "Full")
    body: dict[str, Any] = {**full, "id": CUSTOM, "name": "Harbour house", "preset": False}
    body.update(changes)
    return body


def refusal(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


@pytest.fixture
def engine(
    monkeypatch: pytest.MonkeyPatch,
    operator: Any,
    client: Client,
    settings: Any,
    tmp_path: Path,
) -> Iterator[FakeEngine]:
    """The fake behind the frame, answering the three reads (presets shuffled, the full list
    with a custom profile last); ``client`` signed in as the operator."""
    client.force_login(operator)
    settings.DATA_DIR = tmp_path
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    presets = m7("presets")
    presets["items"].reverse()  # out of E13's order: the page sorts them
    everything = {"items": [*m7("presets")["items"], custom_profile()], "next_cursor": None}
    fake.respond("GET", RATINGS, 200, json=engine_fixture("list_content_ratings"))
    fake.respond("GET", PROFILES, 200, json=everything)
    fake.respond("GET", PROFILES, 200, json=presets, query={"preset": "true"})
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def spool_dir(settings: Any) -> Path:
    return Path(settings.DATA_DIR) / "imports"


def spools(settings: Any) -> list[str]:
    directory = spool_dir(settings)
    return sorted(os.listdir(directory)) if directory.exists() else []


def bundle(name: str = "harbour.epistrel.tgz", content: bytes = BUNDLE) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/gzip")


def form_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"file": bundle(), "upgrade_mode": "backfill", "idempotency_key": KEY}
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def imports(fake: FakeEngine) -> list[httpx.Request]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == IMPORT]


def routes(fake: FakeEngine) -> list[str]:
    return [
        f"{r.method} {r.url.path}{'?' + r.url.query.decode() if r.url.query else ''}"
        for r in fake.requests
    ]


def parts(request: httpx.Request) -> list[tuple[str, str | None, str, bytes]]:
    head = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
    message = BytesParser(policy=policy.HTTP).parsebytes(head + request.content)
    found: list[tuple[str, str | None, str, bytes]] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        found.append((str(name), part.get_filename(), part.get_content_type(), payload))
    return found


def metadata_of(request: httpx.Request) -> dict[str, Any]:
    name, filename, content_type, payload = parts(request)[0]
    assert (name, filename, content_type) == ("metadata", None, "application/json")
    body: dict[str, Any] = json.loads(payload)
    return body


def file_of(request: httpx.Request) -> tuple[str | None, str, bytes]:
    name, filename, content_type, payload = parts(request)[1]
    assert name == "file"
    return filename, content_type, payload


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def refusal_on(html: str, name: str) -> str:
    """The refusal's text on field ``name``, its markup dropped."""
    part = field(html, name)
    match = re.search(r'<span class="refusal-text">(.*?)</span></span>', part, re.S)
    if match is None:
        match = re.search(r'<span class="refusal-text">(.*)', part, re.S)
    assert match, name
    found = re.sub(r"<[^>]+>", "", match.group(1).split("<br>", 1)[0])
    return unescape(found).replace("\xa0", " ")


def hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, name
    return unescape(match.group(1))


def field(html: str, name: str) -> str:
    start = html.index(f'class="field field-{name}"')
    ends = [
        i for i in (html.find('class="field ', start + 1), html.find("</form>", start)) if i > 0
    ]
    return html[start : min(ends)]


def options(html: str, name: str) -> list[tuple[str, str]]:
    start = html.index(f'<select name="{name}"')
    select = html[start : html.index("</select>", start)]
    return [
        (unescape(v), unescape(label))
        for v, label in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', select)
    ]


def first_stage(client: Client, engine: FakeEngine, **changes: Any) -> str:
    engine.respond("POST", IMPORT, 200, json=m7("import_impact"))
    response = client.post(URL, form_data(**changes))
    assert response.status_code == 200
    return response.content.decode()


def confirm_data(html: str, key: str = CONFIRM_KEY) -> dict[str, str]:
    return {twostage.CONFIRM_FIELD: hidden(html, twostage.CONFIRM_FIELD), "idempotency_key": key}


# --- the page (GET) -------------------------------------------------------------------------------


def test_get_reads_and_never_imports(client: Client, engine: FakeEngine) -> None:
    response = client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert routes(engine) == [
        f"GET {RATINGS}",
        f"GET {PROFILES}?limit=50&preset=true",
        f"GET {PROFILES}?limit=50",
    ]
    assert imports(engine) == []  # complement: never POST /admin/stories/import
    assert "<title>Import a bundle · Engine</title>" in html
    assert '<a href="/engine/import/" aria-current="page">Import a bundle</a>' in html
    assert RESTORE_LINE in html
    assert 'enctype="multipart/form-data"' in html


def test_the_form_fields_in_order(client: Client, engine: FakeEngine) -> None:
    html = client.get(URL).content.decode()
    names = re.findall(r'class="field field-([a-z_]+)"', html)
    assert names == [
        "file",
        "owner",
        "name",
        "content_rating_id",
        "engine_profile_id",
        "upgrade_mode",
    ]
    assert "A .epistrel.tgz bundle from a story&#x27;s Export" in field(html, "file")
    assert "Blank: you (console:nate)" in field(html, "owner")
    assert "Blank: the bundle&#x27;s story name" in field(html, "name")
    assert options(html, "content_rating_id") == [
        ("", RATING_DEFAULT),
        ("00000000000000000000RAT001", "Everyone"),
        ("00000000000000000000RAT002", "Teen"),
        ("00000000000000000000RAT003", "Mature"),
    ]
    assert re.search(r'value="backfill"[^>]*checked', field(html, "upgrade_mode"))
    assert "Only matters when the change turns on something" in field(html, "upgrade_mode")


def test_profile_choices_are_presets_first(client: Client, engine: FakeEngine) -> None:
    html = client.get(URL).content.decode()
    assert options(html, "engine_profile_id") == [
        ("", PROFILE_DEFAULT),
        ("00000000000000000000PRF001", "Lite (preset)"),
        ("00000000000000000000PRF003", "Standard (preset)"),
        ("00000000000000000000PRF002", "Full (preset)"),
        ("00000000000000000000PRF004", "Max (preset)"),
        (CUSTOM, "Harbour house"),
    ]


def test_profile_choices_drop_deleted_and_version_shared_names(
    client: Client, engine: FakeEngine
) -> None:
    second = custom_profile(id="01JM7PF0000000000000000002", current_version=3, version=3)
    gone = custom_profile(id="01JM7PF0000000000000000003", deleted_at="2026-11-01T09:00:00Z")
    everything = {"items": [*m7("presets")["items"], custom_profile(), second, gone]}
    engine.respond("GET", PROFILES, 200, json={**everything, "next_cursor": None})
    html = client.get(URL).content.decode()
    shown = options(html, "engine_profile_id")
    assert shown[-2:] == [(CUSTOM, "Harbour house (v4)"), (second["id"], "Harbour house (v3)")]
    assert len(shown) == 7  # each preset once (deduplicated by id), the deleted one dropped


@pytest.mark.parametrize("failing", ["ratings", "presets", "profiles"])
def test_a_failed_read_degrades_its_field(client: Client, engine: FakeEngine, failing: str) -> None:
    error = refusal("database_unavailable", "The database is unavailable")
    if failing == "ratings":
        engine.respond("GET", RATINGS, 503, json=error)
    elif failing == "presets":
        engine.respond("GET", PROFILES, 503, json=error, query={"preset": "true"})
    else:
        engine.respond("GET", PROFILES, 503, json=error)
    response = client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    if failing == "ratings":
        assert options(html, "content_rating_id") == [("", RATING_DEFAULT)]
        assert RATINGS_UNREAD in field(html, "content_rating_id")
        assert len(options(html, "engine_profile_id")) == 6
    else:
        assert options(html, "engine_profile_id") == [("", PROFILE_DEFAULT)]
        assert PROFILES_UNREAD in field(html, "engine_profile_id")
        assert len(options(html, "content_rating_id")) == 4
    assert 'class="import-form"' in html  # never a stale form, never a panel instead


def test_get_sweeps_old_spools(client: Client, engine: FakeEngine, settings: Any) -> None:
    directory = spool_dir(settings)
    directory.mkdir(parents=True)
    old = directory / "01JM7SP0000000000000000001.epistrel.tgz"
    new = directory / "01JM7SP0000000000000000002.epistrel.tgz"
    for path in (old, new):
        path.write_bytes(BUNDLE)
    hour_ago = time.time() - twostage.MAX_AGE_S - 5
    os.utime(old, (hour_ago, hour_ago))
    client.get(URL)
    assert spools(settings) == [new.name]


# --- the first stage ------------------------------------------------------------------------------


def test_blank_members_are_left_out(client: Client, engine: FakeEngine, settings: Any) -> None:
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    client.post(URL, form_data(owner="  ", name=""))
    (call,) = imports(engine)
    assert metadata_of(call) == {"upgrade_mode": "backfill", "ignore_warnings": False}
    # complement: no optional member, not even ""
    for member in ("owner", "name", "content_rating_id", "engine_profile_id"):
        assert member not in metadata_of(call)
    filename, content_type, payload = file_of(call)
    assert (filename, content_type, payload) == ("harbour.epistrel.tgz", "application/gzip", BUNDLE)
    assert call.headers["Idempotency-Key"] == KEY


def test_chosen_members_are_sent(client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    client.post(
        URL,
        form_data(
            owner=" user:alice ",
            name="Harbour (restored)",
            content_rating_id="00000000000000000000RAT002",
            engine_profile_id="00000000000000000000PRF003",
            upgrade_mode="forward_only",
        ),
    )
    (call,) = imports(engine)
    assert metadata_of(call) == {
        "owner": "user:alice",
        "name": "Harbour (restored)",
        "content_rating_id": "00000000000000000000RAT002",
        "engine_profile_id": "00000000000000000000PRF003",
        "upgrade_mode": "forward_only",
        "ignore_warnings": False,
    }


def test_the_uploaded_name_is_its_base_name(client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    upload = bundle("harbour.epistrel.tgz")
    upload.name = "../../evil.tgz"
    upload.content_type = ""
    client.post(URL, form_data(file=upload))
    (call,) = imports(engine)
    filename, content_type, _ = file_of(call)
    assert (filename, content_type) == ("evil.tgz", "application/octet-stream")


@pytest.mark.parametrize("upload", ["missing", "empty"])
def test_missing_file_calls_nothing(
    client: Client, engine: FakeEngine, settings: Any, upload: str
) -> None:
    data = form_data(file=None if upload == "missing" else bundle(content=b""))
    response = client.post(URL, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert imports(engine) == []
    assert 'class="errorlist"' in field(html, "file")
    assert spools(settings) == []
    assert hidden(html, "idempotency_key") != KEY  # E21: a fresh key after a local failure


def test_an_unknown_choice_is_a_field_error(client: Client, engine: FakeEngine) -> None:
    response = client.post(URL, form_data(content_rating_id="01JM7XX0000000000000000001"))
    assert imports(engine) == []
    assert 'class="errorlist"' in field(response.content.decode(), "content_rating_id")


def test_a_spooling_failure_calls_nothing(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from console.views import imports as views

    def refuse(upload: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(views, "spool", refuse)
    response = client.post(URL, form_data())
    html = response.content.decode()
    assert imports(engine) == []
    assert (
        "The Console couldn&#x27;t hold the upload (Permission denied) — nothing was created"
        in html
    )


def test_a_warning_shows_the_impact_and_creates_nothing(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(
        client, engine, engine_profile_id="00000000000000000000PRF003", upgrade_mode="forward_only"
    )
    assert NOTHING_CREATED_YET in html
    assert 'id="impact-01JM7ST0000000000000000003"' in html  # the report rows
    assert '<section class="impact">' in html
    assert ">Import anyway</button>" in html
    facts = text(html[html.index('class="import-facts"') : html.index("</dl>")])
    assert "File harbour.epistrel.tgz (199 bytes)" in facts
    assert "Owner you (console:nate)" in facts
    assert "Name the bundle's story name" in facts
    assert f"Content rating {RATING_DEFAULT}" in facts
    assert "Engine profile Standard (preset)" in facts
    assert "Earlier turns Forward only" in facts
    # complement: exactly one POST, no redirect, one spool file on disk
    assert len(imports(engine)) == 1
    assert len(spools(settings)) == 1
    assert hidden(html, "idempotency_key") != KEY  # the partial's fresh key
    payload = twostage.unsign("import", hidden(html, twostage.CONFIRM_FIELD))
    assert payload.target_id == ""
    assert payload.body == {
        "engine_profile_id": "00000000000000000000PRF003",
        "upgrade_mode": "forward_only",
        "ignore_warnings": True,
    }
    assert payload.base["spool"]["name"] == spools(settings)[0]
    assert payload.base["spool"]["filename"] == "harbour.epistrel.tgz"
    assert payload.base["spool"]["size"] == len(BUNDLE)
    assert payload.base["config_revision"] == 30


@override_settings(ROOT_URLCONF=STUBS)
def test_import_anyway_resends_with_ignore_warnings(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(client, engine)
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    response = client.post(URL, confirm_data(html))
    assert response.status_code == 303
    assert response["Location"] == JOB_URL
    first, second = imports(engine)
    assert metadata_of(second) == {"upgrade_mode": "backfill", "ignore_warnings": True}
    assert file_of(second)[2] == BUNDLE == file_of(first)[2]
    assert second.headers["Idempotency-Key"] == CONFIRM_KEY != first.headers["Idempotency-Key"]
    (flash,) = flashes(response)
    assert flash == (
        f"Import started: job <code>{JOB}</code> — idempotency key "
        "<code>01JM7KY0000000000000000001</code>"
    )
    assert spools(settings) == []  # complement: the spool is gone


def test_no_import_without_confirm_after_a_warning(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(client, engine)
    # a first-stage POST with no confirm token and no file: a field error, no import
    response = client.post(URL, {"upgrade_mode": "backfill", "idempotency_key": KEY})
    assert 'class="errorlist"' in field(response.content.decode(), "file")
    # a tampered token: EXPIRED_TEXT, zero calls, and back to the form
    before = len(engine.requests)
    token = hidden(html, twostage.CONFIRM_FIELD)
    response = client.post(URL, {twostage.CONFIRM_FIELD: token[:-2] + "xx"})
    assert response.status_code == 303
    assert response["Location"] == URL
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert len(engine.requests) == before  # zero calls
    assert len(imports(engine)) == 1  # complement: no second POST …/import


def test_an_expired_token_deletes_its_spool(
    client: Client, engine: FakeEngine, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    html = first_stage(client, engine)
    assert len(spools(settings)) == 1
    later = time.time() + twostage.MAX_AGE_S + 5
    monkeypatch.setattr(time, "time", lambda: later)
    before = len(engine.requests)
    response = client.post(URL, confirm_data(html))
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert len(engine.requests) == before
    assert spools(settings) == []


def page_confirm(html: str) -> dict[str, str]:
    """What the impact page's own two-stage form posts: its token and its confirm key."""
    match = re.search(r'<form[^>]*class="two-stage".*?</form>', html, re.S)
    assert match, "no two-stage form"
    form = match.group(0)
    return {
        twostage.CONFIRM_FIELD: hidden(form, twostage.CONFIRM_FIELD),
        "idempotency_key": hidden(form, "idempotency_key"),
    }


def tampered(data: dict[str, str]) -> dict[str, str]:
    token = data[twostage.CONFIRM_FIELD]
    return {**data, twostage.CONFIRM_FIELD: token[:-3] + ("aaa" if token[-3:] != "aaa" else "bbb")}


def test_a_tampered_token_deletes_its_spool(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    """#345 (#320 AC 3): a tampered token → EXPIRED_TEXT, zero calls, and the spool deleted.
    The name comes from the session's record for the page's confirm key, not the token."""
    html = first_stage(client, engine)
    assert len(spools(settings)) == 1
    before = len(engine.requests)
    response = client.post(URL, tampered(page_confirm(html)))
    assert response.status_code == 303
    assert response["Location"] == URL
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert len(engine.requests) == before  # zero calls
    assert spools(settings) == []


def test_a_tampered_token_deletes_only_its_own_pages_spool(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    """#345's complement: another impact page's spool stays, and a key this session never
    rendered deletes nothing."""
    first = first_stage(client, engine)
    [first_spool] = spools(settings)
    second = first_stage(client, engine)
    [second_spool] = [n for n in spools(settings) if n != first_spool]
    response = client.post(URL, {**tampered(page_confirm(first)), "idempotency_key": KEY})
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert spools(settings) == sorted([first_spool, second_spool])  # an unknown key: nothing
    client.post(URL, tampered(page_confirm(first)))
    assert spools(settings) == [second_spool]
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    assert client.post(URL, page_confirm(second)).status_code == 303  # still confirmable
    assert spools(settings) == []


def test_a_missing_spool_asks_for_the_file_again(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(client, engine)
    for name in spools(settings):
        (spool_dir(settings) / name).unlink()
    before = len(engine.requests)
    response = client.post(URL, confirm_data(html))
    assert response.status_code == 303
    assert flashes(response) == [IMPORT_SPOOL_EXPIRED]
    assert len(engine.requests) == before  # zero calls


def test_a_confirm_held_again_shows_the_report_without_a_confirm(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(client, engine)
    response = client.post(URL, confirm_data(html))
    again = response.content.decode()
    assert response.status_code == 200
    assert '<section class="impact">' in again
    assert twostage.CONFIRM_FIELD not in again
    assert IMPORT_SPOOL_EXPIRED in again
    assert spools(settings) == []
    # a second submit of the same confirm: the spool is gone
    before = len(engine.requests)
    response = client.post(URL, confirm_data(html))
    assert flashes(response) == [IMPORT_SPOOL_EXPIRED]
    assert len(engine.requests) == before


def test_a_refusal_at_the_confirm_rerenders_the_form(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(client, engine, name="Harbour (restored)")
    engine.respond("POST", IMPORT, 409, json=refusal("profile_deleted", "deleted"))
    response = client.post(URL, confirm_data(html))
    page = response.content.decode()
    assert response.status_code == 200
    assert "That engine profile has been deleted — choose another" in field(
        page, "engine_profile_id"
    )
    assert 'value="Harbour (restored)"' in field(page, "name")
    assert 'name="keep_spool"' not in page
    assert spools(settings) == []


def test_an_unknown_outcome_at_the_confirm_keeps_the_key(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    html = first_stage(client, engine)
    engine.fail("POST", IMPORT, httpx.ReadTimeout("slow"))
    response = client.post(URL, confirm_data(html))
    page = response.content.decode()
    assert 'class="engine-error' in page
    assert hidden(page, "idempotency_key") == CONFIRM_KEY
    assert spools(settings) == []


@override_settings(ROOT_URLCONF=STUBS)
def test_started_without_a_warning_goes_straight_on(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    response = client.post(URL, form_data())
    assert response.status_code == 303
    assert response["Location"] == JOB_URL
    row = EngineOperation.objects.get(kind="import_story")
    assert row.outcome == EngineOperation.Outcome.QUEUED
    assert row.entity_id == NEW_STORY
    assert row.job_id == JOB
    # complement: no confirm page was rendered, and the spool is deleted
    assert twostage.CONFIRM_FIELD not in response.content.decode()
    assert spools(settings) == []


def test_before_s25b_the_result_block_shows(
    client: Client, engine: FakeEngine, settings: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D19's unregistered case: #321 registered ``engine:import-job``, so ``import_job_url``
    answering ``None`` stands in for a build without it."""
    monkeypatch.setattr(import_views, "import_job_url", lambda job_id: None)
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    response = client.post(URL, form_data())
    html = response.content.decode()
    assert response.status_code == 200
    block = html[html.index('class="import-started"') :]
    assert (
        f"Import started: job <code>{JOB}</code>; the new story "
        f'<a href="/stories/{NEW_STORY}/"><code>{NEW_STORY}</code></a> opens once it ends'
    ) in block
    assert "Idempotency key <code>01JM7KY0000000000000000001</code>" in block
    assert 'class="import-form"' in html  # the form reset below it
    assert 'name="keep_spool"' not in html
    assert spools(settings) == []


# --- refusals -------------------------------------------------------------------------------------

REFUSALS = {
    "bundle_too_large": (
        413,
        refusal("bundle_too_large", "too big", limit="bundle_max_uncompressed_bytes", value=2048),
        "file",
        "The bundle is larger than this Engine accepts (bundle_max_uncompressed_bytes (2.0 KB))"
        " — nothing was created",
    ),
    "corrupt_bundle": (
        422,
        refusal("corrupt_bundle", "bad", reason="checksum_mismatch"),
        "file",
        "The bundle is damaged or isn't an Epistrel bundle: "
        f"{CORRUPT_REASON_TEXT['checksum_mismatch']} — nothing was created",
    ),
    "unsupported_format_version": (
        422,
        refusal("unsupported_format_version", "new", bundle_version="2.0", supported="1.x"),
        "file",
        "This bundle's format (2.0) isn't one this Engine reads (1.x)",
    ),
    "rating_required": (
        422,
        refusal("rating_required", "name one", bundle_rating_name="Mature+", candidates=["Mature"]),
        "content_rating_id",
        "The bundle's rating “Mature+” matches more than one rating here — choose one",
    ),
}


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    if status == 413:  # the route's schema declares no 413 (nathanpond/Epistrel#786)
        engine.respond_undeclared("POST", IMPORT, status, json=body)
    else:
        engine.respond("POST", IMPORT, status, json=body)


@pytest.mark.parametrize("code", sorted(REFUSALS))
def test_refusals_in_plain_words(
    client: Client, engine: FakeEngine, settings: Any, code: str
) -> None:
    status, body, on, wording = REFUSALS[code]
    answer(engine, status, body)
    response = client.post(URL, form_data())
    html = response.content.decode()
    assert response.status_code == 200
    assert refusal_on(html, on) == wording
    if code == "rating_required":
        assert "Closest: Mature" in field(html, on)
        assert "Keep the uploaded file harbour.epistrel.tgz (199 bytes)" in text(html)
        assert len(spools(settings)) == 1
    else:  # complement: the file refusals delete the spool and show no keep box
        assert 'name="keep_spool"' not in html
        assert spools(settings) == []


def test_too_large_links_the_settings(client: Client, engine: FakeEngine) -> None:
    status, body, _, _ = REFUSALS["bundle_too_large"]
    answer(engine, status, body)
    html = client.post(URL, form_data()).content.decode()
    assert (
        '<a class="settings-link" href="/engine/settings/#settings-story-bundles">'
        "<code>bundle_max_uncompressed_bytes</code></a>"
    ) in field(html, "file")


@pytest.mark.parametrize(
    ("details", "wording"),
    [
        ({}, "accepts (bundle_max_uncompressed_bytes) — nothing"),
        ({"limit": "bundle_max_entries", "value": 100000}, "(bundle_max_entries (100,000))"),
        ({"limit": "manifest_max_bytes", "value": 65536}, "(manifest_max_bytes (64.0 KB))"),
    ],
)
def test_too_large_limits(
    client: Client, engine: FakeEngine, details: dict[str, Any], wording: str
) -> None:
    answer(engine, 413, refusal("bundle_too_large", "too big", **details))
    html = client.post(URL, form_data()).content.decode()
    assert wording in refusal_on(html, "file")
    if details.get("limit") == "manifest_max_bytes":
        assert "settings-link" not in field(html, "file")


@pytest.mark.parametrize(
    ("details", "wording"),
    [
        ({"bundle_rating_name": "Mature+"}, "The bundle's rating “Mature+” matches no rating here"),
        ({"bundle_rating_name": None}, "The bundle names no rating — choose one"),
    ],
)
def test_rating_required_variants(
    client: Client, engine: FakeEngine, details: dict[str, Any], wording: str
) -> None:
    answer(engine, 422, refusal("rating_required", "name one", **details))
    html = client.post(URL, form_data()).content.decode()
    assert wording in refusal_on(html, "content_rating_id")
    assert "Closest" not in html


def test_corrupt_unknown_reason_and_entry(client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, refusal("corrupt_bundle", "bad", reason="new_thing", entry="rows/x.jsonl"))
    html = client.post(URL, form_data()).content.decode()
    assert (
        "isn&#x27;t an Epistrel bundle: new thing (<code>rows/x.jsonl</code>) — nothing was created"
    ) in field(html, "file")


def test_rating_fix_reuses_the_spool(client: Client, engine: FakeEngine, settings: Any) -> None:
    status, body, _, _ = REFUSALS["rating_required"]
    answer(engine, status, body)
    html = client.post(URL, form_data()).content.decode()
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    data = {
        "spool": hidden(html, "spool"),
        "keep_spool": "1",
        "content_rating_id": "00000000000000000000RAT003",
        "upgrade_mode": "backfill",
        "idempotency_key": hidden(html, "idempotency_key"),
    }
    response = client.post(URL, data)  # no file part
    assert response.status_code == 303  # #321: to the job page
    assert response["Location"] == JOB_URL
    first, second = imports(engine)
    assert file_of(second)[2] == file_of(first)[2] == BUNDLE
    assert metadata_of(second)["content_rating_id"] == "00000000000000000000RAT003"
    assert spools(settings) == []


def test_an_unticked_keep_box_needs_a_file(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    answer(engine, *REFUSALS["rating_required"][:2])
    html = client.post(URL, form_data()).content.decode()
    data = {"spool": hidden(html, "spool"), "upgrade_mode": "backfill"}
    response = client.post(URL, data)
    assert 'class="errorlist"' in field(response.content.decode(), "file")
    assert len(imports(engine)) == 1


def test_a_swept_spool_reads_expired_on_the_file(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    answer(engine, *REFUSALS["rating_required"][:2])
    html = client.post(URL, form_data()).content.decode()
    for name in spools(settings):
        (spool_dir(settings) / name).unlink()
    data = {"spool": hidden(html, "spool"), "keep_spool": "1", "upgrade_mode": "backfill"}
    response = client.post(URL, data)
    assert IMPORT_SPOOL_EXPIRED in field(response.content.decode(), "file")
    assert len(imports(engine)) == 1


def test_a_new_file_beats_the_kept_spool(client: Client, engine: FakeEngine, settings: Any) -> None:
    answer(engine, *REFUSALS["rating_required"][:2])
    html = client.post(URL, form_data()).content.decode()
    old = spools(settings)
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    data = form_data(
        file=bundle("other.epistrel.tgz", b"other bytes"),
        spool=hidden(html, "spool"),
        keep_spool="1",
    )
    client.post(URL, data)
    second = imports(engine)[-1]
    assert file_of(second)[0] == "other.epistrel.tgz"
    assert file_of(second)[2] == b"other bytes"
    assert spools(settings) == []
    assert old


def test_a_tampered_spool_field_opens_nothing(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    answer(engine, *REFUSALS["rating_required"][:2])
    html = client.post(URL, form_data()).content.decode()
    data = {"spool": hidden(html, "spool") + "x", "keep_spool": "1", "upgrade_mode": "backfill"}
    response = client.post(URL, data)
    assert 'class="errorlist"' in field(response.content.decode(), "file")
    assert len(imports(engine)) == 1


@pytest.mark.parametrize("path", ["metadata.name", "body.metadata.name"])
def test_metadata_validation_error_lands_on_the_field(
    client: Client, engine: FakeEngine, settings: Any, path: str
) -> None:
    body = refusal("validation_error", "name is too long", field=path)
    answer(engine, 422, body)
    response = client.post(URL, form_data(name="Harbour"))
    html = response.content.decode()
    assert response.status_code == 200
    assert "name is too long — nothing was created" in field(html, "name")
    assert 'value="Harbour"' in field(html, "name")
    assert len(spools(settings)) == 1  # kept: a metadata change can fix it
    assert "Keep the uploaded file" in html


def test_an_unplaced_validation_error_goes_above_the_form(
    client: Client, engine: FakeEngine
) -> None:
    answer(
        engine, 422, refusal("validation_error", "the metadata is invalid", field="body.metadata")
    )
    html = client.post(URL, form_data()).content.decode()
    above = html[: html.index('class="import-form"')]
    assert "the metadata is invalid — nothing was created" in above


def test_profile_deleted_lands_on_the_profile(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    answer(engine, 409, refusal("profile_deleted", "deleted", field="metadata.engine_profile_id"))
    html = client.post(URL, form_data(engine_profile_id=CUSTOM)).content.decode()
    assert "That engine profile has been deleted — choose another" in field(
        html, "engine_profile_id"
    )
    assert len(spools(settings)) == 1


def test_anything_else_is_the_panel_and_drops_the_spool(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    answer(engine, 503, refusal("database_unavailable", "The database is unavailable"))
    response = client.post(URL, form_data())
    html = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in html
    assert 'class="import-form"' in html
    assert spools(settings) == []


def test_a_held_import_is_the_frame_fragment(
    client: Client, engine: FakeEngine, settings: Any
) -> None:
    answer(engine, 503, refusal("erasure_pending", "An erasure is pending"))
    response = client.post(URL, form_data())
    assert response.status_code == 503
    assert spools(settings) == []


# --- recent imports -------------------------------------------------------------------------------


def import_row(job_id: str, story_id: str, **changes: Any) -> EngineOperation:
    fields: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "user:nate",
        "kind": "import_story",
        "http_method": "POST",
        "path": IMPORT,
        "outcome": EngineOperation.Outcome.QUEUED,
        "job_id": job_id,
        "entity_type": "story",
        "entity_id": story_id,
        "response": {"job_id": job_id, "story_id": story_id},
        **changes,
    }
    return EngineOperation.objects.create(**fields)


def test_recent_imports_from_the_log(client: Client, engine: FakeEngine) -> None:
    older = import_row("01JM7JB0000000000000000011", "01JM7ST0000000000000000011")
    newer = import_row(
        "01JM7JB0000000000000000012",
        "01JM7ST0000000000000000012",
        outcome=EngineOperation.Outcome.OK,
    )
    html = client.get(URL).content.decode()
    section = html[html.index('<section class="recent-imports">') :]
    assert "Recent imports from this Console" in section
    rows = re.findall(r'<tr class="recent-import">(.*?)</tr>', section, re.S)
    assert len(rows) == 2
    assert newer.job_id in rows[0]  # newest first
    assert older.job_id in rows[1]
    # #321 registered the job page: each job links it.
    assert '<a href="/engine/import/01JM7JB0000000000000000012/"><code>' in rows[0]
    assert f'<a href="/stories/{newer.entity_id}/"><code>{newer.entity_id}</code></a>' in rows[0]
    assert 'title="ok">Ok</span>' in rows[0]
    assert 'title="queued">Queued</span>' in rows[1]
    assert NOT_LISTED_NOTE.replace("'", "&#x27;") in section


@override_settings(ROOT_URLCONF=STUBS)
def test_recent_imports_link_their_job_pages(client: Client, engine: FakeEngine) -> None:
    import_row(JOB, NEW_STORY)
    html = client.get(URL).content.decode()
    assert f'<a href="{JOB_URL}"><code>{JOB}</code></a>' in html


def test_no_recent_imports(client: Client, engine: FakeEngine) -> None:
    html = client.get(URL).content.decode()
    assert "No imports from this Console yet" in html
