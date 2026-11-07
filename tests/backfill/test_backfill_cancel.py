"""Cancelling a story's backfill behind a confirm page (#308; #134's confirmed mode, E21)."""

import re
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.backfill import (
    CANCEL_LINES,
    CANCELLED_TEXT,
    NO_BACKFILL,
    NOT_CANCELLABLE_TEXT,
)
from console.portability import IMPORTING_TEXT
from tests.backfill.helpers import (
    BACKFILL_ID,
    BACKFILL_PATH,
    BACKFILL_READ,
    CANCEL,
    PAGE,
    STORY,
    m7,
    paths,
)
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

KEY = "01JM7KY0000000000000000005"
LINK = f"{CANCEL}?backfill={BACKFILL_ID}&status=extracting"


def confirmed(**extra: str) -> dict[str, str]:
    return {"idempotency_key": KEY, "confirmed": "1", **extra}


def deletes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "DELETE"]


def cancelled(engine: FakeEngine, **changes: Any) -> None:
    body = m7("backfill_cancelled")
    body.update(changes)
    engine.respond("DELETE", BACKFILL_PATH, 200, json=body)


def test_confirm_get_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(LINK)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<title>Cancel the backfill — Epistrel Console</title>" in html
    assert "<h1>Cancel the backfill</h1>" in html
    assert "Cancel this story&#x27;s backfill?" in html
    for line in CANCEL_LINES:
        assert f"<p>{line}</p>" in html
    assert f"<dt>Story</dt><dd><code>{STORY}</code></dd>" in html
    assert f"<dt>Backfill id</dt><dd><code>{BACKFILL_ID}</code></dd>" in html
    assert "<dt>Status</dt><dd><code>Extracting</code></dd>" in html
    assert 'class="danger">Cancel the backfill</button>' in html
    assert f'<a href="{PAGE}">Cancel</a>' in html
    assert 'name="confirmed" value="1"' in html


def test_confirm_get_calls_nothing(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** the fake's call log is empty."""
    operator_client.get(LINK)
    assert engine.requests == []


def test_a_missing_status_reads_a_dash(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(CANCEL).content.decode()
    assert "<dt>Backfill id</dt><dd><code>—</code></dd>" in html
    assert "<dt>Status</dt><dd><code>—</code></dd>" in html
    assert engine.requests == []


def test_the_query_is_escaped(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"{CANCEL}?backfill=<b>x</b>&status=<i>").content.decode()
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_post_without_confirmed_calls_nothing(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(CANCEL, {"idempotency_key": KEY})
    assert response.status_code == 200
    assert engine.requests == []
    assert f'value="{KEY}"' in response.content.decode()


def test_confirmed_cancel(operator_client: Client, engine: FakeEngine) -> None:
    cancelled(engine, idempotency_key=KEY)
    response = operator_client.post(CANCEL, confirmed())
    assert response.status_code == 303
    assert response["Location"] == PAGE
    (sent,) = deletes(engine)
    assert sent.url.path == BACKFILL_READ
    assert sent.headers["Idempotency-Key"] == KEY
    assert sent.content == b""
    assert paths(engine) == [f"DELETE {BACKFILL_READ}"]
    (message,) = [str(m) for m in get_messages(response.wsgi_request)]
    assert message == f"{CANCELLED_TEXT} — idempotency key <code>{KEY}</code>"


def test_the_flash_shows_after_the_redirect(operator_client: Client, engine: FakeEngine) -> None:
    cancelled(engine, idempotency_key=KEY)
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_cancelled")["backfill"])
    response = operator_client.post(CANCEL, confirmed(), follow=True)
    html = response.content.decode()
    assert CANCELLED_TEXT in html
    assert f"<code>{KEY}</code>" in html


def test_not_cancellable(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", BACKFILL_PATH, 409, json=error_json("backfill_not_cancellable"))
    response = operator_client.post(CANCEL, confirmed())
    html = response.content.decode()
    assert response.status_code == 200
    assert NOT_CANCELLABLE_TEXT in html
    # Complement: no redirect, and only the one DELETE.
    assert "Location" not in response
    assert paths(engine) == [f"DELETE {BACKFILL_READ}"]
    assert f'value="{KEY}"' not in html  # an answered refusal mints a fresh key


def test_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", BACKFILL_PATH, 404, json=error_json("backfill_not_found"))
    html = operator_client.post(CANCEL, confirmed()).content.decode()
    assert NO_BACKFILL in html


def test_importing(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "DELETE",
        BACKFILL_PATH,
        409,
        json=error_json(
            "story_importing",
            job_id="01JM7JB0000000000000000007",
            stage="guardrails",
            progress={"done": 3, "total": 8},
        ),
    )
    response = operator_client.post(CANCEL, confirmed())
    html = response.content.decode()
    assert response.status_code == 200
    assert IMPORTING_TEXT in html


def test_another_refusal_is_c5s(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("DELETE", BACKFILL_PATH, 409, json=error_json("story_archived"))
    html = operator_client.post(CANCEL, confirmed()).content.decode()
    assert "This story is archived — unarchive it to cancel the backfill." in html


def test_an_unknown_outcome_keeps_the_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("DELETE", BACKFILL_PATH, httpx.ReadTimeout("slow"))
    response = operator_client.post(CANCEL, confirmed())
    html = response.content.decode()
    assert response.status_code == 200
    assert f'value="{KEY}"' in html
    assert "engine-error" in html


def test_cancel_link_hidden_when_terminal(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=m7("backfill_completed"))
    html = operator_client.get(PAGE).content.decode()
    assert "Cancel the backfill…" not in html


def test_cancel_link_while_running(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** the running page links the confirm page with its display query."""
    html = operator_client.get(PAGE).content.decode()
    found = re.search(r'<a href="([^"]+)">Cancel the backfill…</a>', html)
    assert found is not None
    assert found.group(1) == f"{CANCEL}?backfill={BACKFILL_ID}&amp;status=extracting"
