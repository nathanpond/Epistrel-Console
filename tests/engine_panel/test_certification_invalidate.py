"""#315: invalidate a certification from the Engine panel, through #134's confirmed confirm page
(Engine #234; E11, E19, E21, D8, D19, C13), over #292's ``m7/certification_invalidated.json``."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import NoReverseMatch
from django.urls import reverse as real_reverse

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import certification_admin as views
from console.views import certifications as cert_views
from console.views import story_pages
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

CERT = "01JM7CT0000000000000000001"
FP = "sha256:4710ae26cb731c016d2b86c747e7d3e40e7e9b0a3cfda00bfe60118230039b4b"
DETAIL = f"/engine/certifications/{CERT}/"
URL = f"/engine/certifications/{CERT}/invalidate/"
INVALIDATE = f"/admin/certifications/{CERT}/invalidate"
ROUTE = "/admin/certifications/{certification_id}/invalidate"
KEY = "01JM7KY0000000000000000009"
QUERY = {"fingerprint": FP, "profile": "Full", "next": DETAIL}


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def refusal(code: str, message: str = "refused", **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (story_pages, views, cert_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def posts(fake: FakeEngine) -> list[Any]:
    return [r for r in fake.requests if r.method == "POST"]


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def key_of(html: str) -> str:
    match = re.search(r'name="idempotency_key" value="([^"]*)"', html)
    assert match
    return match.group(1)


def text(html: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def confirmed(**changes: str) -> dict[str, str]:
    data = {
        "idempotency_key": KEY,
        "confirmed": "1",
        "next": DETAIL,
        "fingerprint": FP,
        "profile": "Full",
        "reason": "drift",
        "note": "  provider swapped weights  ",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def test_confirm_get_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"{URL}?{urlencode(QUERY)}")
    html = response.content.decode()
    assert response.status_code == 200
    assert engine.requests == []
    shown = text(html)
    assert "Invalidate certification" in shown
    assert "Mark certification …00000001 stale?" in shown
    assert (
        "Stories on its fingerprint report Stale from their next turn. The record is kept."
    ) in shown
    assert (
        "Only a new certification run makes the configuration Certified again (with a new "
        "certification)."
    ) in shown
    assert f"<dt>Certification</dt><dd><code>{CERT}</code></dd>" in html
    assert f"<dt>Fingerprint</dt><dd><code>{FP}</code></dd>" in html
    assert "<dt>Profile</dt><dd><code>Full</code></dd>" in html
    assert 'type="radio" name="reason" value="drift"' in html
    assert "The model changed under the same name and version" in html
    assert "Withdrawn by an operator" in html
    assert " checked" not in html  # no default reason
    assert re.search(r'<textarea name="note" cols="60" rows="4" maxlength="1000"', html)
    assert "Kept for the audit trail" in html
    assert '<button type="submit" class="danger">Invalidate</button>' in html
    assert f'<a href="{DETAIL}">Cancel</a>' in html
    assert 'name="confirmed" value="1"' in html


def test_facts_absent_read_as_dash(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(URL).content.decode()
    assert "<dt>Fingerprint</dt><dd><code>—</code></dd>" in html
    assert "<dt>Profile</dt><dd><code>—</code></dd>" in html
    assert f'<a href="{DETAIL}">Cancel</a>' in html  # Cancel falls back to the certification
    assert engine.requests == []


def test_lower_case_id_is_normalised(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"/engine/certifications/ {CERT.lower()}/invalidate/").content
    assert f"<code>{CERT}</code>".encode() in html


def test_post_without_confirmed_calls_nothing(operator_client: Client, engine: FakeEngine) -> None:
    data = confirmed()
    del data["confirmed"]
    response = operator_client.post(URL, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert posts(engine) == []
    assert key_of(html) == KEY
    assert "This field is required" not in html


@pytest.mark.parametrize(
    "changes",
    [{"reason": None}, {"note": "    "}, {"reason": "revoked"}, {"note": None}],
)
def test_reason_and_note_required(
    operator_client: Client, engine: FakeEngine, changes: dict[str, Any]
) -> None:
    data = confirmed()
    for name, value in changes.items():
        if value is None:
            data.pop(name)
        else:
            data[name] = value
    response = operator_client.post(URL, data)
    html = response.content.decode()
    assert response.status_code == 200
    assert "This field is required" in html or "Select a valid choice" in html
    assert posts(engine) == []
    assert key_of(html) == KEY  # the same key, so the eventual confirm sends it
    assert f"<code>{FP}</code>" in html  # the display facts ride across the POST


def test_note_over_the_engine_limit(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(URL, confirmed(note="x" * 1001))
    assert "at most 1000 characters" in unescape(response.content.decode())
    assert posts(engine) == []


def test_confirmed_invalidate_sends_reason_and_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        ROUTE,
        200,
        json={**m7("certification_invalidated"), "status": "stale", "invalidated_reason": "drift"},
    )
    response = operator_client.post(URL, confirmed())
    assert response.status_code == 303
    assert response["Location"] == DETAIL
    (call,) = posts(engine)
    assert call.url.path == INVALIDATE
    assert json.loads(call.content) == {"reason": "drift", "note": "provider swapped weights"}
    assert call.headers["Idempotency-Key"] == KEY
    assert flashes(response) == [
        "Certification marked stale (the model changed under the same version) — stories on "
        "its fingerprint report Stale from their next turn — idempotency key "
        "<code>01JM7KY0000000000000000001</code>"
    ]
    row = EngineOperation.objects.get(path=INVALIDATE)
    assert (row.entity_type, row.entity_id) == ("certification", CERT)
    assert row.request["body"] == {"reason": "drift", "note": "provider swapped weights"}


def test_success_redirect_ignores_next(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", ROUTE, 200, json=m7("certification_invalidated"))
    response = operator_client.post(URL, confirmed(next="/engine/certifications/", reason="manual"))
    assert response["Location"] == DETAIL
    (flash,) = flashes(response)
    assert flash.startswith("Certification marked stale (withdrawn by an operator) — ")


@pytest.mark.parametrize(
    ("details", "shown"),
    [
        ({"status": "stale"}, "This certification is already stale — nothing changed"),
        (
            {"status": "revoked"},
            "This certification is revoked — only a valid one can be invalidated",
        ),
        ({}, "This certification isn't valid — only a valid one can be invalidated"),
    ],
)
def test_not_valid_refusal(
    operator_client: Client, engine: FakeEngine, details: dict[str, Any], shown: str
) -> None:
    engine.respond("POST", ROUTE, 409, json=refusal("certification_not_valid", **details))
    response = operator_client.post(URL, confirmed())
    html = response.content.decode()
    assert response.status_code == 200
    assert "Location" not in response
    assert shown in unescape(html)
    assert len(posts(engine)) == 1
    assert key_of(html) != KEY  # an answered refusal: a fresh key
    assert '<button type="submit" class="danger">Invalidate</button>' in html


def test_not_found_refusal(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", ROUTE, 404, json=refusal("certification_not_found"))
    response = operator_client.post(URL, confirmed())
    assert response.status_code == 200
    assert '<p class="errorlist confirm-error">No such certification</p>' in (
        response.content.decode()
    )


def test_other_refusal_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", ROUTE, 409, json=refusal("idempotency_conflict", "reused"))
    html = operator_client.post(URL, confirmed()).content.decode()
    assert 'class="engine-error' in html
    assert "idempotency_conflict" in html
    assert key_of(html) != KEY


def test_unknown_outcome_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", ROUTE, httpx.ReadTimeout("slow"))
    html = operator_client.post(URL, confirmed()).content.decode()
    assert 'class="engine-error' in html
    assert key_of(html) == KEY


def test_malformed_id_404_no_call(operator_client: Client, engine: FakeEngine) -> None:
    for method in ("get", "post"):
        response = getattr(operator_client, method)("/engine/certifications/nope/invalidate/")
        assert response.status_code == 404
    assert engine.requests == []


def test_link_appears_on_detail(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/certifications/{certification_id}", 200, json=m7("certification"))
    html = operator_client.get(DETAIL).content.decode()  # the real urlconf
    link = re.search(r'<a class="button invalidate" href="([^"]+)">Invalidate…</a>', html)
    assert link is not None
    assert unescape(link.group(1)).startswith(f"/engine/certifications/{CERT}/invalidate/?")
    stale = {**m7("certification"), "status": "stale", "invalidated_reason": "manual"}
    engine.respond("GET", "/certifications/{certification_id}", 200, json=stale)
    assert "Invalidate…" not in operator_client.get(DETAIL).content.decode()


def test_link_absent_while_unregistered(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D19's plain case, kept now the name resolves: a stand-in ``reverse`` without it."""

    def reverse(name: str, *args: Any, **kwargs: Any) -> str:
        if name == "engine:certification-invalidate":
            raise NoReverseMatch(name)
        return real_reverse(name, *args, **kwargs)

    monkeypatch.setattr(cert_views, "reverse", reverse)
    engine.respond("GET", "/certifications/{certification_id}", 200, json=m7("certification"))
    assert "Invalidate…" not in operator_client.get(DETAIL).content.decode()


def test_validation_error_on_the_fields(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        ROUTE,
        422,
        json=refusal(
            "validation_error",
            "invalid",
            errors=[{"field": "body.note", "message": "String should have at most 1000 chars"}],
        ),
    )
    html = operator_client.post(URL, confirmed()).content.decode()
    note = re.search(r'<p class="field field-note">(.*?)</p>', html, re.S)
    assert note is not None
    assert "String should have at most 1000 chars" in note.group(1)
    assert 'class="engine-error' not in html
    assert len(posts(engine)) == 1
