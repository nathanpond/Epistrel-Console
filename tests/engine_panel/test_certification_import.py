"""#315: import a certification record from the Engine panel (Engine #235; E11, E19, E21, D14),
over #292's ``m7/certification_imported.json`` and a record built from ``m7/certification.json``."""

import copy
import hashlib
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.certification_admin import (
    FILE_EMPTY,
    FILE_NOT_JSON_NAME,
    FILE_NOT_UTF8,
    FILE_TOO_BIG,
    MAX_RECORD_BYTES,
    ONE_OF_THE_TWO,
    PASTED_TOO_BIG,
    SCHEMA_VERSION_ONLY_1,
)
from console.models import EngineOperation
from console.views import certification_admin as views
from console.views import certifications as list_views
from console.views import story_pages
from console.views.certification_admin import ALREADY_STORED
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

URL = "/engine/certification-import/"
IMPORT = "/admin/certifications/import"
CERT = "01JM7CT0000000000000000003"
OTHER = "01JM7CT0000000000000000001"
KEY = "01JM7KY0000000000000000009"
REPORT = "# Full v4 certification\r\n\r\nAll floors held.\n\n"
RECORD_MEMBERS = (
    "fingerprint",
    "profile_id",
    "profile_version",
    "preset",
    "models",
    "quality_settings_hash",
    "floors_passed",
    "floors_failed",
    "floors_insufficient",
    "intervention_types_covered",
    "intervention_types_insufficient",
    "judge_calibrated",
    "sample_sizes",
    "seed_count",
    "model_tier",
    "host",
    "report_path",
    "run_at",
)


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def report_hash(report_md: str) -> str:
    """The Engine's rule: LF line ends and exactly one trailing newline."""
    text = report_md.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record(**changes: Any) -> dict[str, Any]:
    """An importable record from ``m7/certification.json``'s fields (``id`` →
    ``certification_id``, only ``CertificationRecord``'s members, plus ``schema_version``,
    ``report_md`` and ``report_sha256``)."""
    cert = m7("certification")
    body: dict[str, Any] = {"schema_version": 1, "certification_id": CERT}
    body.update({k: cert[k] for k in RECORD_MEMBERS})
    body["report_md"] = REPORT
    body["report_sha256"] = report_hash(REPORT)
    body.update(changes)
    return body


def imported(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("certification_imported")
    body.update(changes)
    return body


def refusal(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (story_pages, views, list_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def imports(fake: FakeEngine) -> list[Any]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == IMPORT]


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def key_of(html: str) -> str:
    match = re.search(r'name="idempotency_key" value="([^"]*)"', html)
    assert match
    return match.group(1)


def textarea(html: str) -> str:
    match = re.search(r'<textarea name="record"[^>]*>(.*?)</textarea>', html, re.S)
    assert match
    return unescape(match.group(1)).strip()


def text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def paste(client: Client, body: object, **extra: str) -> Any:
    raw = body if isinstance(body, str) else json.dumps(body)
    return client.post(URL, {"record": raw, "idempotency_key": KEY, **extra})


def upload(client: Client, content: bytes, name: str = "full-v4-2026-11-01.json") -> Any:
    file = SimpleUploadedFile(name, content, content_type="application/octet-stream")
    return client.post(URL, {"record_file": file, "idempotency_key": KEY})


def test_get_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    assert "<title>Import a certification record · Engine</title>" in html
    assert 'enctype="multipart/form-data"' in html
    assert 'name="record_file"' in html
    assert 'name="record"' in html
    assert '<a class="certifications" href="/engine/certifications/">Certifications</a>' in html
    assert '<a href="/engine/certifications/" aria-current="page">Certifications</a>' in html
    assert "<h2>Import a certification record</h2>" in html


def test_file_upload_imports(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 200, json=imported())
    sent = record()
    response = upload(operator_client, json.dumps(sent).encode())
    assert response.status_code == 303
    assert response["Location"] == f"/engine/certifications/{CERT}/"
    (call,) = imports(engine)
    assert json.loads(call.content) == sent
    assert call.headers["Idempotency-Key"] == KEY
    (flash,) = flashes(response)
    assert flash.startswith("Stored certification <code title=")
    assert flash.endswith(" — idempotency key <code>01JM7KY0000000000000000001</code>")


def test_file_with_a_bom_imports(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 200, json=imported())
    response = upload(operator_client, b"\xef\xbb\xbf" + json.dumps(record()).encode())
    assert response.status_code == 303
    assert len(imports(engine)) == 1


def test_pasted_json_imports(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 200, json=imported())
    sent = record()
    response = paste(operator_client, sent)
    assert response.status_code == 303
    assert response["Location"] == f"/engine/certifications/{CERT}/"
    (call,) = imports(engine)
    assert json.loads(call.content) == sent
    assert call.headers["Idempotency-Key"] == KEY


@pytest.mark.parametrize("given", ["both", "neither", "blank-beside-nothing"])
def test_both_or_neither_refused_locally(
    operator_client: Client, engine: FakeEngine, given: str
) -> None:
    data: dict[str, Any] = {"idempotency_key": KEY}
    if given == "both":
        data["record"] = json.dumps(record())
        data["record_file"] = SimpleUploadedFile("r.json", json.dumps(record()).encode())
    elif given == "blank-beside-nothing":
        data["record"] = "   \n "
    response = operator_client.post(URL, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert ONE_OF_THE_TWO in unescape(html)
    assert imports(engine) == []
    assert key_of(html) != KEY  # E21: a local failure mints a fresh key


def test_whitespace_textarea_beside_a_file_counts_as_not_given(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", IMPORT, 200, json=imported())
    file = SimpleUploadedFile("r.JSON", json.dumps(record()).encode())
    response = operator_client.post(
        URL, {"record": "  ", "record_file": file, "idempotency_key": KEY}
    )
    assert response.status_code == 303
    assert len(imports(engine)) == 1


@pytest.mark.parametrize("body", ["[1, 2]", '"a record"', "42"])
def test_not_an_object_refused_locally(
    operator_client: Client, engine: FakeEngine, body: str
) -> None:
    response = paste(operator_client, body)
    assert response.status_code == 200
    assert "Enter a JSON object" in unescape(response.content.decode())
    assert imports(engine) == []


def test_strict_json_refused_locally(operator_client: Client, engine: FakeEngine) -> None:
    for body in ('{"a": 1, "a": 2}', '{"a": NaN}', "{not json"):
        response = paste(operator_client, body)
        assert "Enter valid JSON" in unescape(response.content.decode())
    assert imports(engine) == []


def test_missing_members_refused_locally(operator_client: Client, engine: FakeEngine) -> None:
    sent = record()
    for name in ("report_md", "seed_count", "certification_id"):
        del sent[name]
    response = paste(operator_client, sent)
    html = response.content.decode()
    assert response.status_code == 200
    assert (
        "This isn't a certification record (missing: <code>certification_id</code>, "
        "<code>seed_count</code>, <code>report_md</code>)"
    ) in unescape(html)
    assert imports(engine) == []
    # The preview shows whatever parsed, a missing member as "—".
    assert "Certification — · fingerprint" in text(html)


@pytest.mark.parametrize("version", [2, 1.0, "1", True])
def test_schema_version_2_refused_locally(
    operator_client: Client, engine: FakeEngine, version: object
) -> None:
    response = paste(operator_client, record(schema_version=version))
    assert SCHEMA_VERSION_ONLY_1 in unescape(response.content.decode())
    assert imports(engine) == []


def test_oversize_file_refused_locally(operator_client: Client, engine: FakeEngine) -> None:
    response = upload(operator_client, b" " * (MAX_RECORD_BYTES + 1))
    html = unescape(response.content.decode())
    assert response.status_code == 200
    assert FILE_TOO_BIG in html
    assert imports(engine) == []


def test_oversize_paste_refused_locally(operator_client: Client, engine: FakeEngine) -> None:
    big = json.dumps(record(report_md="é" * (MAX_RECORD_BYTES // 2 + 1)))
    with override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=10 * MAX_RECORD_BYTES):
        response = paste(operator_client, big)
    assert PASTED_TOO_BIG in unescape(response.content.decode())
    assert imports(engine) == []


@pytest.mark.parametrize(
    ("content", "name", "message"),
    [
        (b"{}", "record.txt", FILE_NOT_JSON_NAME),
        (b"", "record.json", FILE_EMPTY),
        (b"  \n", "record.json", FILE_EMPTY),
        (b"\xff\xfe{", "record.json", FILE_NOT_UTF8),
        (b"{nope", "record.json", "Enter valid JSON"),
        (b"[]", "record.json", "Enter a JSON object"),
    ],
)
def test_bad_file_refused_locally(
    operator_client: Client, engine: FakeEngine, content: bytes, name: str, message: str
) -> None:
    response = upload(operator_client, content, name)
    html = response.content.decode()
    assert response.status_code == 200
    field = re.search(r'<p class="field field-record_file">(.*?)</p>', html, re.S)
    assert field is not None
    assert message in unescape(field.group(1))
    assert imports(engine) == []


def test_refused_upload_moves_into_the_textarea(
    operator_client: Client, engine: FakeEngine
) -> None:
    sent = record(schema_version=2)
    raw = json.dumps(sent)
    response = upload(operator_client, raw.encode(), "full-v4.json")
    html = response.content.decode()
    assert textarea(html) == raw
    assert "Loaded from full-v4.json" in html
    field = re.search(r'<p class="field field-record">(.*?)</p>', html, re.S)
    assert field is not None
    assert SCHEMA_VERSION_ONLY_1 in unescape(field.group(1))
    assert imports(engine) == []


def test_preview_line(operator_client: Client, engine: FakeEngine) -> None:
    response = paste(operator_client, record(schema_version=2))
    preview = re.search(r'<p class="record-preview">(.*?)</p>', response.content.decode(), re.S)
    assert preview is not None
    shown = text(preview.group(1))
    assert (
        shown == "Certification …00000003 · fingerprint 4710ae26cb73 · run 2026-11-05 13:00:00 UTC"
    )
    raw = paste(operator_client, record(schema_version=2, run_at="last tuesday"))
    assert "run <code>last tuesday</code>" in raw.content.decode()


def test_already_stored(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 200, json=imported(stored=False))
    response = paste(operator_client, record())
    assert response.status_code == 303
    assert response["Location"] == f"/engine/certifications/{CERT}/"
    (flash,) = flashes(response)
    assert flash == (f"{ALREADY_STORED} — idempotency key <code>01JM7KY0000000000000000001</code>")
    (message,) = get_messages(response.wsgi_request)
    assert message.level_tag == "info"


def test_invalid_record_lines(operator_client: Client, engine: FakeEngine) -> None:
    errors = [
        {"loc": "report_md", "type": "extra_forbidden"},
        {"loc": ["floors_passed", 0, "rule"], "type": "literal_error"},
        {"type": "missing"},
    ]
    engine.respond(
        "POST",
        IMPORT,
        422,
        json=refusal(
            "invalid_certification_record",
            "the certification record does not validate",
            reason="schema",
            errors=errors,
        ),
    )
    raw = json.dumps(record(extra=1), indent=2)
    response = paste(operator_client, raw)
    html = response.content.decode()
    assert response.status_code == 200
    block = re.search(r'<div class="refusal" role="alert">(.*?)</div>', html, re.S)
    assert block is not None
    assert text(block.group(1)) == (
        "The Engine refused this record: the certification record does not validate "
        "report_md: Extra forbidden floors_passed.0.rule: Literal error "
        '{"type":"missing"}'
    )
    assert textarea(html) == raw.strip()
    assert key_of(html) != KEY
    assert len(imports(engine)) == 1


@pytest.mark.parametrize(
    ("details", "shown"),
    [
        ({"reason": "report_hash"}, "The report doesn't match its report_sha256 — re-export"),
        ({"reason": "report_missing"}, "The record has no report (report_md) — re-export it"),
        ({"reason": "too_old"}, "The Engine refused this record: Too old"),
        ({}, "The Engine refused this record: the report does not match"),
    ],
)
def test_invalid_record_reason(
    operator_client: Client, engine: FakeEngine, details: dict[str, Any], shown: str
) -> None:
    message = "the report does not match the record's report_sha256"
    engine.respond(
        "POST", IMPORT, 422, json=refusal("invalid_certification_record", message, **details)
    )
    html = unescape(paste(operator_client, record()).content.decode())
    assert shown in html
    if details:
        assert message not in html  # never both


def test_record_conflict_links_the_stored_one(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        IMPORT,
        409,
        json=refusal("certification_record_conflict", "conflict", certification_id=OTHER),
    )
    response = paste(operator_client, record())
    html = response.content.decode()
    assert response.status_code == 200
    assert (
        f'A different record is already stored under <a href="/engine/certifications/{OTHER}/">'
        f'<code title="{OTHER}">'
    ) in html
    assert "— nothing was stored" in unescape(html)
    assert len(imports(engine)) == 1


def test_record_conflict_falls_back_to_the_records_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", IMPORT, 409, json=refusal("certification_record_conflict", "conflict"))
    html = paste(operator_client, record()).content.decode()
    assert f'<a href="/engine/certifications/{CERT}/">' in html


def test_validation_error_on_the_textarea(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        IMPORT,
        422,
        json=refusal(
            "validation_error",
            "invalid",
            errors=[{"field": "body.run_at", "message": "not a datetime"}],
        ),
    )
    html = paste(operator_client, record()).content.decode()
    field = re.search(r'<p class="field field-record">(.*?)</p>', html, re.S)
    assert field is not None
    assert "run_at: not a datetime" in field.group(1)
    assert 'class="engine-error' not in html


def test_other_refusal_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 409, json=refusal("idempotency_conflict", "reused key"))
    response = paste(operator_client, record())
    html = response.content.decode()
    assert response.status_code == 200
    assert 'class="engine-error' in html
    assert "idempotency_conflict" in html
    assert textarea(html) == json.dumps(record())
    assert key_of(html) != KEY


def test_timeout_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", IMPORT, httpx.ReadTimeout("slow"))
    html = paste(operator_client, record()).content.decode()
    assert 'class="engine-error' in html
    assert key_of(html) == KEY


def test_recorded_about_the_certification(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", IMPORT, 200, json=imported())
    paste(operator_client, record())
    row = EngineOperation.objects.get(path=IMPORT)
    assert (row.entity_type, row.entity_id) == ("certification", CERT)


def test_list_links_the_import_page(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications", 200, json=m7("certifications"))
    html = operator_client.get("/engine/certifications/").content.decode()
    assert (
        '<a class="certification-import" href="/engine/certification-import/">'
        "Import a certification record…</a>"
    ) in html
