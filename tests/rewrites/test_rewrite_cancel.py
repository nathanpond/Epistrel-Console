"""#299's Cancel (``stories:rewrite-cancel``, ``console/views/rewrite_apply.py``): the call-free
confirm page with its facts from the link, ``confirm.py``'s confirmed rule, the one cancel, the
redirect's flash, the refusals on the confirm page, and E3's Cancel link while open."""

from html import unescape
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.views import rewrite_apply
from console.views.rewrite_apply import CANCEL_LINE, CANCELLED_TEXT
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json
from tests.rewrites.helpers import GET_ONE, ONE_ROUTE, SESSION, SESSION_URL, engine, m7

pytestmark = pytest.mark.django_db

KEY = "01JM7KY0000000000000000009"
CANCEL_ROUTE = "/stories/{story_id}/rewrites/{session_id}/cancel"
CANCEL = f"{GET_ONE}/cancel"
INSTRUCTION = "Bob never forged the ledger; he only copied it"
FACTS = {"kind": "rewrite", "state": "awaiting_approval", "instruction": INSTRUCTION}
CANCEL_URL = f"{SESSION_URL}cancel/"
LINKED = f"{CANCEL_URL}?{urlencode(FACTS)}"
WRITTEN_ONLY = ("timeline_revision", "apply", "idempotency_key")


@pytest.fixture
def fake(engine: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """#297's ``engine`` fixture, also behind the cancel view's own client."""

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = engine.transport
        return client

    monkeypatch.setattr(rewrite_apply, "engine_client_for", client_with_fake)
    return engine


def page(response: Any, status: int = 200) -> str:
    assert response.status_code == status
    return unescape(response.content.decode())


def confirm(client: Client, url: str = LINKED, **data: Any) -> Any:
    return client.post(url, {"idempotency_key": KEY, "confirmed": "1", **data})


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def test_cancel_get_makes_no_engine_call(operator_client: Client, fake: FakeEngine) -> None:
    html = page(operator_client.get(LINKED))
    assert "<title>Cancel rewrite — Epistrel Console</title>" in html
    assert '<p class="question">Cancel this rewrite session?</p>' in html
    assert f"<p>{CANCEL_LINE}</p>" in html
    assert f"<dt>Session</dt><dd><code>{SESSION}</code></dd>" in html
    assert "<dt>Kind</dt><dd><code>Rewrite</code></dd>" in html
    assert f"<dt>Instruction</dt><dd><code>{INSTRUCTION}</code></dd>" in html
    assert "<dt>State</dt><dd><code>Ready to apply</code></dd>" in html
    assert '<button type="submit">Cancel session</button>' in html  # not ``danger``
    assert f'<a href="{SESSION_URL}">Cancel</a>' in html
    assert '<input type="hidden" name="confirmed" value="1">' in html
    # **Complement:** no Engine call at all (E24).
    assert fake.requests == []


def test_cancel_facts_default_to_a_dash(operator_client: Client, fake: FakeEngine) -> None:
    html = page(operator_client.get(f"{CANCEL_URL}?kind=later_kind&instruction=%3Cb%3E"))
    assert "<dt>Kind</dt><dd><code>Later kind</code></dd>" in html
    assert "<dt>State</dt><dd><code>—</code></dd>" in html
    raw = operator_client.get(f"{CANCEL_URL}?instruction=%3Cb%3E").content.decode()
    assert f"<dd><code>{escape('<b>')}</code></dd>" in raw  # verbatim, escaped
    html = page(operator_client.get(CANCEL_URL))
    assert "<dt>Instruction</dt><dd><code>—</code></dd>" in html
    assert fake.requests == []


def test_cancel_a_line_edit_reads_line_edit(operator_client: Client, fake: FakeEngine) -> None:
    """#346: a line edit's Cancel link carries ``kind=rewrite`` and no instruction."""
    html = page(operator_client.get(f"{CANCEL_URL}?kind=rewrite&state=awaiting_approval"))
    assert "<dt>Kind</dt><dd><code>Line edit</code></dd>" in html
    assert "<dt>Instruction</dt><dd><code>—</code></dd>" in html
    assert fake.requests == []


def test_post_without_confirmed_calls_nothing(operator_client: Client, fake: FakeEngine) -> None:
    response = operator_client.post(LINKED, {"idempotency_key": KEY})
    html = page(response)
    assert "Cancel this rewrite session?" in html
    assert f'name="idempotency_key" value="{KEY}"' in html
    assert fake.requests == []


def test_confirmed_cancel(operator_client: Client, fake: FakeEngine) -> None:
    fake.respond("POST", CANCEL_ROUTE, 200, json=m7("rewrite_cancelled"))
    response = confirm(operator_client)
    assert response.status_code == 303
    assert response["Location"] == SESSION_URL
    (request,) = [r for r in fake.requests if r.method == "POST"]
    assert request.url.path == CANCEL
    assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
    assert request.content == b""
    assert request.headers["Idempotency-Key"] == KEY
    assert "X-Epistrel-Expected-Revision" not in request.headers
    (flash,) = flashes(response)
    assert flash == (f"{CANCELLED_TEXT} — idempotency key <code>01JM7KY0000000000000000001</code>")
    # **Complement:** no apply.
    assert all(not r.url.path.endswith("/apply") for r in fake.requests)


def test_cancel_closed_session_refused(operator_client: Client, fake: FakeEngine) -> None:
    fake.respond(
        "POST",
        CANCEL_ROUTE,
        409,
        json=error_json("rewrite_session_closed", "closed", state="applied"),
    )
    response = confirm(operator_client)
    html = page(response)
    assert "This session has already ended (Applied)" in html
    assert "Cancel this rewrite session?" in html
    assert '<button type="submit">Cancel session</button>' in html  # still enabled
    assert '<input type="hidden" name="confirmed" value="1">' in html
    assert f'name="idempotency_key" value="{KEY}"' not in html  # an answered refusal: fresh
    assert "<dt>State</dt><dd><code>Ready to apply</code></dd>" in html  # the query's facts
    assert flashes(response) == []


def test_cancel_not_found_refused(operator_client: Client, fake: FakeEngine) -> None:
    fake.respond(
        "POST", CANCEL_ROUTE, 404, json=error_json("rewrite_session_not_found", "no such session")
    )
    html = page(confirm(operator_client))
    assert "No such rewrite session for this story" in html


def test_cancel_unknown_outcome_keeps_the_key(operator_client: Client, fake: FakeEngine) -> None:
    fake.fail("POST", CANCEL_ROUTE, httpx.ConnectError("refused"))
    html = page(confirm(operator_client))
    assert "Engine unreachable" in html
    assert 'class="engine-error' not in html  # ``error=`` only, never the full-page panel
    assert f'name="idempotency_key" value="{KEY}"' in html
    (request,) = [r for r in fake.requests if r.method == "POST"]
    assert request.url.path == CANCEL


def session_body(name: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7(name)
    for key in WRITTEN_ONLY:
        body.pop(key, None)
    body.update(changes)
    return body


@pytest.mark.parametrize(
    ("name", "state", "linked"),
    [
        ("rewrite_open", "awaiting_answers", True),
        ("rewrite_session", "awaiting_approval", True),
        ("rewrite_session_applied", "applied", False),
        ("rewrite_session", "cancelled", False),
        ("rewrite_session", "expired", False),
    ],
)
def test_cancel_link_only_while_open(
    operator_client: Client, fake: FakeEngine, name: str, state: str, linked: bool
) -> None:
    fake.respond("GET", ONE_ROUTE, 200, json=session_body(name, state=state, id=SESSION))
    html = operator_client.get(SESSION_URL).content.decode()
    facts = {"kind": "rewrite", "state": state, "instruction": INSTRUCTION}
    link = f'<a href="{escape(f"{CANCEL_URL}?{urlencode(facts)}")}" class="session-action">'
    assert (link in html) is linked
    if not linked:
        assert "cancel/" not in html


__all__ = ["engine"]  # the fixture, re-exported for pytest
