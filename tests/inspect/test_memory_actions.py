"""Pin, unpin, quarantine and release a memory from the retrieval explorer (#143): the row
controls, each write's Engine call and flash, the quarantine page's local checks, the release
confirm, the refusals and the operation log rows.

Runs against the real urlconf on the schema-validating fake Engine, with the explorer's own
fixture (``retrieval.json``: ``…M04`` is the quarantined memory) and one base write answer
``tests/fixtures/inspect/memory_written.json``, copied and changed per test."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import REWRITE_INVALID
from console.held import held_text
from console.models import EngineOperation
from console.views import inspect as views
from console.views import inspect_memory
from console.views.inspect_memory import memory_state, pin_limit_text, shown_reason
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, structured
from tests.play.helpers import error_json, story_json

pytestmark = pytest.mark.django_db

M = "01JB2Z8Y5Q3W6K9M1N4P7R0M"
MID = f"{M}01"  # a hot, unquarantined memory
QUARANTINED = f"{M}04"
CHARACTER = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
SHORT = "…4P7R0M01"
QUESTION = "What happened at the tavern with Tobin?"
EXPLORER = f"/stories/{STORY}/inspect/retrieval/"
EXPLORER_NEXT = f"{EXPLORER}?{urlencode({'q': QUESTION, 'as_of': '5'})}"
RETRIEVE = "/stories/{story_id}/retrieve"
PIN = "/stories/{story_id}/memories/{memory_id}/pin"
QUARANTINE = "/admin/stories/{story_id}/memories/{memory_id}/quarantine"
PIN_PATH = f"/stories/{STORY}/memories/{MID}/pin"
QUARANTINE_PATH = f"/admin/stories/{STORY}/memories/{MID}/quarantine"
RELEASE_PATH = f"/admin/stories/{STORY}/memories/{QUARANTINED}/quarantine"
SANITIZE = "/admin/stories/{story_id}/memories/{memory_id}/sanitize"
SANITIZE_PATH = f"/admin/stories/{STORY}/memories/{MID}/sanitize"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0K99"


def url(action: str, memory_id: str = MID) -> str:
    return f"/stories/{STORY}/inspect/memories/{memory_id}/{action}/"


def written(**memory: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "memory_written.json").read_text())
    body = copy.deepcopy(body)
    body["memory"].update(memory)
    return body


def unchanged(**memory: Any) -> dict[str, Any]:
    body = written(**memory)
    body["operation_id"] = None
    return body


def retrieval() -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "retrieval.json").read_text())
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(inspect_memory, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured())
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=6))
    fake.respond("GET", RETRIEVE, 200, json=retrieval())
    fake.respond("POST", PIN, 200, json=written())
    fake.respond("DELETE", PIN, 200, json=written(pinned=False, pinned_by=None))
    fake.respond(
        "POST", QUARANTINE, 200, json=written(quarantined=True, quarantine_reason="contradicts")
    )
    fake.respond("DELETE", QUARANTINE, 200, json=written(id=QUARANTINED))
    fake.respond("POST", SANITIZE, 200, json=written(text="Mara asked the stranger about it."))
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if "/memories/" in r.url.path]


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def explorer(client: Client, **params: str) -> str:
    response = client.get(f"{EXPLORER}?{urlencode({'q': QUESTION, **params})}")
    assert response.status_code == 200
    return response.content.decode()


def row(html: str, memory_id: str) -> str:
    for found in re.findall(r'<tr class="memory-row[^"]*">.*?</tr>', html, re.S):
        if f'title="{memory_id}"' in found:
            return str(found)
    raise AssertionError(memory_id)


def form_key(html: str, action: str) -> str:
    found = re.search(
        rf'action="[^"]*/{action}/">.*?name="idempotency_key" value="([^"]+)"', html, re.S
    )
    assert found is not None, action
    return found.group(1)


def post(client: Client, action: str, memory_id: str = MID, **data: str) -> Any:
    fields = {"idempotency_key": KEY, "next": EXPLORER_NEXT, **data}
    return client.post(url(action, memory_id), fields)


# --- the row controls ----------------------------------------------------------------------------


def test_each_row_has_pin_unpin_and_quarantine_or_release(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = explorer(operator_client)
    plain, quarantined = row(html, MID), row(html, QUARANTINED)
    for found in (plain, quarantined):
        assert found.index(">Pin</button>") < found.index(">Unpin</button>")
    assert ">Quarantine</a>" in plain
    assert ">Release</a>" not in plain
    assert ">Release</a>" in quarantined
    assert ">Quarantine</a>" not in quarantined
    assert plain.index(">Unpin</button>") < plain.index(">Quarantine</a>")
    assert 'title="A quarantined pin stays out of context until released"' in quarantined
    assert "A quarantined pin" not in plain
    assert "<th>Actions</th>" in html
    assert not writes(engine)  # rendering the controls calls nothing


def test_each_form_carries_its_own_key_and_the_explorer_path(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = explorer(operator_client)
    found = row(html, MID)
    keys = re.findall(r'name="idempotency_key" value="([^"]+)"', found)
    assert len(keys) == 2
    assert keys[0] != keys[1]
    full = f"{EXPLORER}?{urlencode({'q': QUESTION})}"
    assert found.count(f'name="next" value="{full}"') == 2
    assert f'href="{url("quarantine")}?{urlencode({"next": full})}"' in found.replace("&amp;", "&")


def test_an_over_long_next_on_a_link_falls_back_to_the_memory_prefill(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = explorer(operator_client, q="x" * 1990)
    found = row(html, MID).replace("&amp;", "&")
    fallback = f"{EXPLORER}?{urlencode({'memory': MID})}"
    assert f'href="{url("quarantine")}?{urlencode({"next": fallback})}"' in found


# --- pin and unpin -------------------------------------------------------------------------------


def test_pin_sends_the_rendered_key_and_returns_to_the_explorer(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = explorer(operator_client)
    key = form_key(row(html, MID), "pin")
    engine.reset()
    response = post(operator_client, "pin", idempotency_key=key)
    assert response.status_code == 302
    assert response["Location"] == EXPLORER_NEXT
    (call,) = writes(engine)
    assert (call.method, call.url.path) == ("POST", PIN_PATH)
    assert call.headers["Idempotency-Key"] == key
    assert call.content == b""
    assert flashes(response) == [
        ("success", f"Pinned memory {SHORT} — pinned · not quarantined · tier hot")
    ]


def test_unpin_deletes_the_pin(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, "unpin")
    assert response["Location"] == EXPLORER_NEXT
    (call,) = writes(engine)
    assert (call.method, call.url.path, call.headers["Idempotency-Key"]) == (
        "DELETE",
        PIN_PATH,
        KEY,
    )
    assert flashes(response) == [
        ("success", f"Unpinned memory {SHORT} — not pinned · not quarantined · tier hot")
    ]


def test_an_off_site_next_falls_back_to_the_explorer(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post(operator_client, "pin", next="https://evil.example/")
    assert response["Location"] == EXPLORER


def test_a_missing_key_gets_a_fresh_one(operator_client: Client, engine: FakeEngine) -> None:
    operator_client.post(url("pin"), {"next": EXPLORER_NEXT})
    (call,) = writes(engine)
    assert re.fullmatch(r"[0-9A-Z]{26}", call.headers["Idempotency-Key"])


@pytest.mark.parametrize(
    ("action", "method", "body", "text"),
    [
        ("pin", "POST", unchanged(), f"Already pinned — memory {SHORT} — pinned"),
        ("unpin", "DELETE", unchanged(pinned=False), f"Not pinned — memory {SHORT} — not pinned"),
    ],
)
def test_an_unchanged_answer_flashes_at_info(
    operator_client: Client,
    engine: FakeEngine,
    action: str,
    method: str,
    body: dict[str, Any],
    text: str,
) -> None:
    engine.respond(method, PIN, 200, json=body)
    response = post(operator_client, action)
    assert flashes(response) == [("info", f"{text} · not quarantined · tier hot")]


def test_a_retired_memory_names_its_retired_reason(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", PIN, 200, json=written(retrieval_tier="retired", retired_reason="budget_overflow")
    )
    response = post(operator_client, "pin")
    assert flashes(response) == [
        (
            "success",
            f"Pinned memory {SHORT} — pinned · not quarantined · tier retired (budget overflow)",
        )
    ]


def test_the_state_text() -> None:
    from console.engine.models import MemoryView

    base = written()["memory"]
    memory = MemoryView.from_json({**base, "retrieval_tier": "frozen", "pinned": False})
    assert memory is not None
    assert memory_state(memory) == "not pinned · not quarantined · tier frozen"  # unknown: raw
    assert shown_reason("a  b\n\tc") == "a b c"
    assert shown_reason("x" * 81) == "x" * 80 + "…"
    assert shown_reason("x" * 80) == "x" * 80


def test_get_and_head_on_pin_and_unpin_are_405(operator_client: Client, engine: FakeEngine) -> None:
    for action in ("pin", "unpin"):
        for response in (operator_client.get(url(action)), operator_client.head(url(action))):
            assert response.status_code == 405
            assert response["Allow"] == "POST"
    for action in ("quarantine", "release"):
        response = operator_client.put(url(action))
        assert response.status_code == 405
        assert response["Allow"] == "GET, HEAD, POST"
    assert not writes(engine)


@pytest.mark.parametrize("action", ["pin", "unpin", "quarantine", "release"])
def test_a_non_ulid_memory_id_is_404_with_no_call(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    for bad in ("not-a-ulid", MID.lower(), MID + "X"):
        response = operator_client.post(url(action, bad), {"reason": "x", "confirmed": "1"})
        assert response.status_code == 404
    assert not engine.requests


# --- quarantine ----------------------------------------------------------------------------------


def test_the_quarantine_page_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"{url('quarantine')}?{urlencode({'next': EXPLORER_NEXT})}")
    html = response.content.decode()
    assert response.status_code == 200
    assert f"<title>Quarantine memory {SHORT} — Epistrel Console</title>" in html
    assert f'<span title="{MID}">Quarantine memory {SHORT}</span>' in html
    assert "A quarantined memory is never retrieved, consolidated or put into context" in html
    assert 'maxlength="500"' in html
    assert ">Reason</label>" in html
    assert '<button type="submit">Quarantine</button>' in html
    assert f'<a href="{EXPLORER_NEXT.replace("&", "&amp;")}">Cancel</a>' in html
    assert f"<code>{MID}</code>" in html
    assert f"<code>{STORY}</code>" in html
    assert not engine.requests


def test_quarantine_sends_the_trimmed_reason(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, "quarantine", reason="  contradicts canon \r\n")
    assert response.status_code == 302
    assert response["Location"] == EXPLORER_NEXT
    (call,) = writes(engine)
    assert (call.method, call.url.path, call.headers["Idempotency-Key"]) == (
        "POST",
        QUARANTINE_PATH,
        KEY,
    )
    assert json.loads(call.content) == {"reason": "contradicts canon"}
    assert flashes(response) == [
        ("success", f"Quarantined memory {SHORT} — pinned · quarantined: contradicts · tier hot")
    ]


def test_a_long_reason_is_cut_in_the_flash(operator_client: Client, engine: FakeEngine) -> None:
    reason = "word  " * 30
    engine.respond(
        "POST", QUARANTINE, 200, json=written(quarantined=True, quarantine_reason=reason)
    )
    response = post(operator_client, "quarantine", reason="why")
    shown = " ".join(reason.split())[:80] + "…"
    assert flashes(response)[0][1].endswith(f"quarantined: {shown} · tier hot")


def test_an_already_quarantined_memory_flashes_at_info(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", QUARANTINE, 200, json=unchanged(quarantined=True, quarantine_reason="old")
    )
    response = post(operator_client, "quarantine", reason="new")
    assert flashes(response) == [
        ("info", f"Already quarantined — memory {SHORT} — pinned · quarantined: old · tier hot")
    ]


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("", "Give a reason."),
        (" \n\t ", "Give a reason."),
        ("x" * 501, "At most 500 characters."),
        ("repeated_implication", "<code>repeated_implication</code> is the Engine"),
        ("  repeated_implication \n", "<code>repeated_implication</code> is the Engine"),
    ],
)
def test_an_invalid_reason_makes_no_call(
    operator_client: Client, engine: FakeEngine, reason: str, message: str
) -> None:
    response = post(operator_client, "quarantine", reason=reason)
    html = response.content.decode()
    assert response.status_code == 200
    assert message in html
    assert f'name="idempotency_key" value="{KEY}"' in html  # no call was made: the key is kept
    assert not engine.requests


def test_a_differently_cased_reserved_reason_is_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    post(operator_client, "quarantine", reason="Repeated_Implication")
    (call,) = writes(engine)
    assert json.loads(call.content) == {"reason": "Repeated_Implication"}


def test_a_500_character_reason_with_crlf_newlines_is_accepted(
    operator_client: Client, engine: FakeEngine
) -> None:
    reason = "a" * 249 + "\r\n" + "b" * 250  # 501 as posted, 500 as the browser counted
    response = post(operator_client, "quarantine", reason=reason)
    assert response.status_code == 302
    (call,) = writes(engine)
    assert json.loads(call.content) == {"reason": "a" * 249 + "\n" + "b" * 250}


def test_an_engine_validation_error_lands_on_the_reason(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST",
        QUARANTINE,
        422,
        json=error_json(
            "validation_error", "repeated_implication is reserved", field="body.reason"
        ),
    )
    response = post(operator_client, "quarantine", reason="mine")
    html = response.content.decode()
    assert response.status_code == 200
    assert '<span class="errorlist field-error">repeated_implication is reserved</span>' in html
    assert f'value="{KEY}"' not in html  # an Engine refusal mints a fresh key
    assert re.search(r">\s*mine</textarea>", html)


def test_a_quarantine_not_found_shows_above_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", QUARANTINE, 404, json=error_json("not_found", "no such memory"))
    response = post(operator_client, "quarantine", reason="mine")
    html = response.content.decode()
    assert response.status_code == 200
    assert "Not found (it may have been deleted or truncated)" in html
    assert html.index("quarantine-refusal") < html.index("<textarea")


def test_a_quarantine_503_renders_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", QUARANTINE, 503, json=error_json("database_unavailable", "down"))
    html = post(operator_client, "quarantine", reason="mine").content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert re.search(r">\s*mine</textarea>", html)


@pytest.mark.parametrize("action", ["quarantine", "release"])
def test_module_off_renders_the_greyed_panel(
    operator_client: Client, engine: FakeEngine, action: str
) -> None:
    method = "POST" if action == "quarantine" else "DELETE"
    engine.respond(method, QUARANTINE, 409, json=error_json("module_off", "off", module="episodic"))
    response = post(operator_client, action, reason="mine", confirmed="1")
    html = response.content.decode()
    assert response.status_code == 409
    assert '<div class="panel unavailable">' in html
    assert "Needs the <code>episodic</code> module" in html
    assert f'href="/stories/{STORY}/config/"' in html


# --- release -------------------------------------------------------------------------------------


def test_the_release_confirm_page_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    query = urlencode({"next": EXPLORER_NEXT})
    html = operator_client.get(f"{url('release', QUARANTINED)}?{query}").content.decode()
    assert "Release the quarantine on memory <em>…4P7R0M04</em>? It becomes retrievable" in html
    assert '<input type="hidden" name="confirmed" value="1">' in html
    assert '<button type="submit">Release</button>' in html  # not danger
    assert f"<code>{QUARANTINED}</code>" in html
    assert not engine.requests


def test_a_release_post_without_confirmed_rerenders_with_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post(operator_client, "release", QUARANTINED)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Release the quarantine on memory" in html
    assert f'name="idempotency_key" value="{KEY}"' in html
    assert not engine.requests


def test_a_confirmed_release_deletes_the_quarantine(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = post(operator_client, "release", QUARANTINED, confirmed="1")
    assert response["Location"] == EXPLORER_NEXT
    (call,) = writes(engine)
    assert (call.method, call.url.path, call.headers["Idempotency-Key"]) == (
        "DELETE",
        RELEASE_PATH,
        KEY,
    )
    assert flashes(response) == [
        ("success", "Released memory …4P7R0M04 — pinned · not quarantined · tier hot")
    ]


def test_releasing_an_unquarantined_memory_flashes_at_info(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", QUARANTINE, 200, json=unchanged())
    response = post(operator_client, "release", confirmed="1")
    assert flashes(response) == [
        ("info", f"Not quarantined — memory {SHORT} — pinned · not quarantined · tier hot")
    ]


# --- refusals ------------------------------------------------------------------------------------


def test_pin_limit_for_the_story(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        PIN,
        422,
        json=error_json(
            "pin_limit_exceeded", "too many", scope="story", limit=5, actual=6, cap="max_pins"
        ),
    )
    response = post(operator_client, "pin")
    assert response["Location"] == EXPLORER_NEXT
    assert flashes(response) == [
        ("error", "Pin limit reached: the story allows 5 pins (`max_pins`); this pin would make 6.")
    ]


def test_pin_limit_for_a_character(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        PIN,
        422,
        json=error_json(
            "pin_limit_exceeded",
            "too many",
            scope="character",
            limit=2,
            actual=3,
            character_id=CHARACTER,
        ),
    )
    response = post(operator_client, "pin")
    assert flashes(response) == [
        ("error", "Pin limit reached: character …4P7R0T3B allows 2 pins; this pin would make 3.")
    ]


@pytest.mark.parametrize(
    "details",
    [
        {"scope": "story", "actual": 6},
        {"scope": "story", "limit": "5", "actual": 6},
        {"scope": "story", "limit": True, "actual": 6},
        {"scope": "story", "limit": -1, "actual": 6},
        {"scope": "character", "limit": 2, "actual": 3},
    ],
)
def test_pin_limit_falls_back_to_the_engine_message(details: dict[str, Any]) -> None:
    from console.engine.client import EngineError

    exc = EngineError("pin_limit_exceeded", "Over the pin ceiling.", details, 422)
    assert pin_limit_text(exc) == "Pin limit reached. Over the pin ceiling."


def test_pin_limit_fallback_through_the_view(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", PIN, 422, json=error_json("pin_limit_exceeded", "Over.", scope="story", actual=6)
    )
    assert flashes(post(operator_client, "pin")) == [("error", "Pin limit reached. Over.")]


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (404, "not_found", "Not found (it may have been deleted or truncated)"),
        (409, "story_archived", "This story is archived — unarchive it to change its memories"),
        (423, "story_busy", "A turn is running — try again"),
        (422, "validation_error", "the Engine's own words"),
    ],
)
def test_a_row_refusal_is_an_error_flash(
    operator_client: Client, engine: FakeEngine, status: int, code: str, text: str
) -> None:
    engine.respond("DELETE", PIN, status, json=error_json(code, "the Engine's own words"))
    response = post(operator_client, "unpin")
    assert response.status_code == 302
    assert response["Location"] == EXPLORER_NEXT
    assert flashes(response) == [("error", text)]


def test_pin_module_off_is_not_mapped(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PIN, 409, json=error_json("module_off", "off", module="episodic"))
    html = post(operator_client, "pin").content.decode()
    assert "Engine error 409 module_off" in html


def test_erasure_pending_is_the_held_wrapper(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PIN, 503, json=error_json("erasure_pending", "held", story_id=STORY))
    response = post(operator_client, "pin")
    assert response.status_code == 503
    assert held_text(None) in response.content.decode()


def test_unreachable_renders_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("DELETE", QUARANTINE, httpx.ConnectError("down"))
    html = post(operator_client, "release", confirmed="1").content.decode()
    assert "Engine unreachable" in html


def test_a_malformed_200_still_flashes_and_is_recorded(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.side_effect("POST", PIN, lambda request: httpx.Response(200, json={"memory": {}}))
    response = post(operator_client, "pin")
    assert response["Location"] == EXPLORER_NEXT
    assert flashes(response) == [
        (
            "warning",
            f"Pinned memory {SHORT} — the Engine's answer couldn't be read; "
            "re-run the question to see its state",
        )
    ]
    row_ = EngineOperation.objects.get()
    assert (row_.kind, row_.outcome, row_.error_code) == ("pin_memory", "error", "bad_response")


# --- the operation log ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "memory_id", "data", "kind", "method", "path"),
    [
        ("pin", MID, {}, "pin_memory", "POST", PIN_PATH),
        ("unpin", MID, {}, "unpin_memory", "DELETE", PIN_PATH),
        ("quarantine", MID, {"reason": " planted "}, "quarantine_memory", "POST", QUARANTINE_PATH),
        ("release", QUARANTINED, {"confirmed": "1"}, "release_memory", "DELETE", RELEASE_PATH),
    ],
)
def test_each_write_records_one_row(
    operator_client: Client,
    engine: FakeEngine,
    action: str,
    memory_id: str,
    data: dict[str, str],
    kind: str,
    method: str,
    path: str,
) -> None:
    post(operator_client, action, memory_id, **data)
    (recorded,) = EngineOperation.objects.all()
    assert (recorded.kind, recorded.http_method, recorded.path) == (kind, method, path)
    assert (recorded.entity_type, recorded.entity_id, recorded.story_id) == ("story", STORY, STORY)
    assert recorded.outcome == "ok"
    assert recorded.idempotency_key == KEY
    if action == "quarantine":
        assert recorded.request["body"] == {"reason": "planted"}


def test_a_refused_write_records_an_error_row(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PIN, 404, json=error_json("not_found", "gone"))
    post(operator_client, "pin")
    (recorded,) = EngineOperation.objects.all()
    assert (recorded.kind, recorded.outcome, recorded.error_code) == (
        "pin_memory",
        "error",
        "not_found",
    )
    assert recorded.entity_type == "story"


# --- sanitize (#144) -----------------------------------------------------------------------------

TEXT = "Mara asked Tobin about the tavern fire."  # MID's text in retrieval.json


def sanitize_post(client: Client, **data: str) -> Any:
    """A sanitize form POST (``confirmed="1"`` for the confirm page's Confirm)."""
    fields = {"span": "Tobin", "surrogate": "the stranger", "shown_text": TEXT, **data}
    return post(client, "sanitize", **fields)


def retrieves(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path.endswith("/retrieve")]


def hidden(html: str, name: str) -> str:
    found = re.search(rf'<input type="hidden" name="{name}"(?: value="([^"]*)")?', html)
    assert found is not None, name
    return found.group(1) or ""


def test_each_row_ends_with_a_sanitize_link(operator_client: Client, engine: FakeEngine) -> None:
    html = explorer(operator_client, as_of="5")
    cell = row(html, MID)
    link = re.search(r'<a class="button memory-sanitize" href="([^"]+)">Sanitize…</a>', cell)
    assert link is not None
    assert cell.index("memory-sanitize") > cell.index("memory-quarantine")  # the last control
    assert link.group(1).startswith(url("sanitize") + "?next=")
    assert "as_of%3D5" in link.group(1)


def test_the_sanitize_page_shows_the_text_the_question_retrieves(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(f"{url('sanitize')}?{urlencode({'next': EXPLORER_NEXT})}")
    html = response.content.decode()
    assert response.status_code == 200
    (read,) = retrieves(engine)
    assert (read.url.params["q"], read.url.params["as_of"]) == (QUESTION, "5")
    assert read.url.params["viewpoint"] == "omniscient"
    assert writes(engine) == []
    assert f"Sanitize memory {SHORT}" in html
    assert "as the explorer's question retrieves it now" in html
    assert f'<pre class="memory-text">{TEXT}</pre>' in html
    assert hidden(html, "shown_text") == TEXT
    assert f'name="next" value="{EXPLORER_NEXT.replace("&", "&amp;")}"' in html
    assert "Empty removes the span" in html
    for textarea in re.findall(r"<textarea[^>]*>", html):
        assert "maxlength" not in textarea  # the server counts code points
    assert "← Back to the explorer" in html


@pytest.mark.parametrize("case", ["no question", "failed read", "not retrieved", "other page"])
def test_the_sanitize_page_without_a_text_says_so(
    operator_client: Client, engine: FakeEngine, case: str
) -> None:
    back = EXPLORER_NEXT
    memory_id = MID
    if case == "no question":
        back = f"{EXPLORER}?memory={MID}"
    elif case == "failed read":
        engine.respond("GET", RETRIEVE, 503, json=error_json("database_unavailable", "down"))
    elif case == "not retrieved":
        memory_id = f"{M}99"
    else:
        back = f"/stories/{STORY}/history/?{urlencode({'q': QUESTION})}"
    html = operator_client.get(
        f"{url('sanitize', memory_id)}?{urlencode({'next': back})}"
    ).content.decode()
    assert "Text not shown — see the explorer" in html
    assert 'class="memory-text"' not in html
    assert hidden(html, "shown_text") == ""
    expected_reads = 1 if case in ("failed read", "not retrieved") else 0
    assert len(retrieves(engine)) == expected_reads


def test_a_valid_sanitize_post_renders_the_confirm_page_with_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = sanitize_post(operator_client).content.decode()
    assert engine.requests == []
    assert (
        "Every occurrence is replaced. The original is kept only in the Engine&#x27;s audit record."
        in html
    )
    assert 'class="danger"' in html
    assert hidden(html, "idempotency_key") == KEY  # the sanitize form's key, carried forward
    assert hidden(html, "confirmed") == "1"
    assert (hidden(html, "span"), hidden(html, "surrogate")) == ("Tobin", "the stranger")
    for fact in (MID, "Tobin", "the stranger", "Mara asked the stranger about the tavern fire."):
        assert f"<code>{fact}</code>" in html
    assert "<dt>Occurrences</dt><dd><code>1</code></dd>" in html
    assert 'name="edit" value="1"' in html
    assert "contains the span" not in html


def test_the_confirm_page_escapes_and_names_an_empty_surrogate(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = sanitize_post(
        operator_client, span="<b>x</b>", surrogate="", shown_text="a <b>x</b> b"
    ).content.decode()
    assert "<code>&lt;b&gt;x&lt;/b&gt;</code>" in html
    assert "<b>x</b>" not in html
    assert "<code>(empty — removes the span)</code>" in html
    assert engine.requests == []


def test_a_surrogate_containing_the_span_is_allowed_with_a_note(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = sanitize_post(operator_client, surrogate="Tobin Smith").content.decode()
    assert "The surrogate contains the span, so the span stays in the text." in html
    assert engine.requests == []


def test_a_confirmed_sanitize_sends_the_rendered_key_and_returns_to_the_explorer(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = operator_client.get(f"{url('sanitize')}?{urlencode({'next': EXPLORER_NEXT})}")
    key = hidden(page.content.decode(), "idempotency_key")
    confirm = operator_client.post(
        url("sanitize"),
        {
            "idempotency_key": key,
            "next": EXPLORER_NEXT,
            "span": "Tobin",
            "surrogate": "the stranger",
            "shown_text": TEXT,
        },
    ).content.decode()
    assert hidden(confirm, "idempotency_key") == key
    assert writes(engine) == []
    response = operator_client.post(
        url("sanitize"),
        {
            "idempotency_key": key,
            "next": EXPLORER_NEXT,
            "span": "Tobin",
            "surrogate": "the stranger",
            "shown_text": TEXT,
            "confirmed": "1",
        },
    )
    (sent,) = writes(engine)
    assert (sent.method, sent.url.path) == ("POST", SANITIZE_PATH)
    assert json.loads(sent.content) == {"span": "Tobin", "surrogate": "the stranger"}
    assert sent.headers["Idempotency-Key"] == key
    assert response.status_code == 302
    assert response["Location"] == EXPLORER_NEXT
    assert flashes(response) == [
        ("success", f"Sanitized memory {SHORT} — pinned · not quarantined · tier hot")
    ]


def test_a_sanitize_with_no_operation_id_still_flashes_as_a_success(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", SANITIZE, 200, json=unchanged())
    response = sanitize_post(operator_client, confirmed="1")
    assert flashes(response)[0][0] == "success"


def test_crlf_newlines_are_sent_as_lf(operator_client: Client, engine: FakeEngine) -> None:
    sanitize_post(
        operator_client,
        span="Tobin\r\nabout",
        surrogate="him\r\nabout",
        shown_text="Mara asked Tobin\r\nabout the fire.",
        confirmed="1",
    )
    (sent,) = writes(engine)
    assert json.loads(sent.content) == {"span": "Tobin\nabout", "surrogate": "him\nabout"}


@pytest.mark.parametrize(
    ("data", "field", "message"),
    [
        ({"span": ""}, "span", "Enter the span to replace."),
        (
            {"span": "x" * 2001, "shown_text": ""},
            "span",
            "At most 2,000 characters (this one has 2,001).",
        ),
        ({"surrogate": "x" * 2001}, "surrogate", "At most 2,000 characters (this one has 2,001)."),
        (
            {"surrogate": "Tobin"},
            "surrogate",
            "The surrogate is the same as the span; nothing would change",
        ),
        ({"span": "tobin"}, "span", "This span isn&#x27;t in the memory&#x27;s text"),
        (
            {"span": TEXT, "surrogate": "   "},
            "surrogate",
            REWRITE_INVALID,
        ),
        (
            {"span": "a", "surrogate": "x" * 200, "shown_text": "a" * 20},
            "surrogate",
            REWRITE_INVALID,
        ),
    ],
)
def test_a_local_refusal_makes_no_call_and_keeps_the_key(
    operator_client: Client, engine: FakeEngine, data: dict[str, str], field: str, message: str
) -> None:
    for confirmed in ({}, {"confirmed": "1"}):
        html = sanitize_post(operator_client, **data, **confirmed).content.decode()
        assert message in html
        assert hidden(html, "idempotency_key") == KEY
        assert "Every occurrence is replaced." not in html  # the form, not the confirm page
    assert engine.requests == []


def test_an_unshown_text_skips_the_presence_check(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = sanitize_post(operator_client, span="absent", shown_text="").content.decode()
    assert "Every occurrence is replaced." in html
    assert "<dt>Occurrences</dt>" not in html
    assert engine.requests == []


def test_edit_rerenders_the_form_with_the_inputs_and_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = sanitize_post(
        operator_client, surrogate="him\nthere", confirmed="1", edit="1"
    ).content.decode()
    assert engine.requests == []
    assert "Every occurrence is replaced." not in html
    assert ">\nTobin</textarea>" in html
    assert ">\nhim\nthere</textarea>" in html
    assert hidden(html, "idempotency_key") == KEY
    assert f'<pre class="memory-text">{TEXT}</pre>' in html


def test_a_sanitize_is_405_for_other_methods(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.put(url("sanitize")).status_code == 405
    assert operator_client.delete(url("sanitize")).status_code == 405
    assert operator_client.head(url("sanitize")).status_code == 200


def test_the_sanitize_view_marks_its_text_fields_sensitive(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = sanitize_post(operator_client)
    assert tuple(response.wsgi_request.sensitive_post_parameters) == (
        "span",
        "surrogate",
        "shown_text",
    )


@pytest.mark.parametrize("shown", [TEXT, ""])
def test_span_not_found_lands_on_the_span_with_a_fresh_key(
    operator_client: Client, engine: FakeEngine, shown: str
) -> None:
    engine.respond("POST", SANITIZE, 422, json=error_json("span_not_found", "nope", memory_id=MID))
    html = sanitize_post(operator_client, shown_text=shown, confirmed="1").content.decode()
    suffix = " (it may have changed since this page loaded)"
    expected = "This span isn&#x27;t in the memory&#x27;s text" + (suffix if shown else "")
    assert f'<span class="errorlist field-error">{expected}</span>' in html
    assert hidden(html, "idempotency_key") not in ("", KEY)
    assert ">\nTobin</textarea>" in html


@pytest.mark.parametrize(
    ("field", "message", "shown"),
    [
        ("body.surrogate", REWRITE_INVALID, "field"),
        ("body.span", "the Engine&#x27;s words", "field"),
        (None, "the Engine&#x27;s words", "above"),
    ],
)
def test_a_validation_error_lands_on_its_field(
    operator_client: Client, engine: FakeEngine, field: str | None, message: str, shown: str
) -> None:
    details = {"field": field} if field else {}
    engine.respond(
        "POST", SANITIZE, 422, json=error_json("validation_error", "the Engine's words", **details)
    )
    html = sanitize_post(operator_client, confirmed="1").content.decode()
    if shown == "field":
        assert f'<span class="errorlist field-error">{message}</span>' in html
    else:
        assert f'<p class="errorlist">{message}</p>' in html
    assert hidden(html, "idempotency_key") != KEY


def test_a_guardrail_violation_shows_above_the_fields_with_the_inputs_kept(
    operator_client: Client, engine: FakeEngine
) -> None:
    violation = {
        "policy_id": "no_slurs",
        "name": "No slurs",
        "message": "The surrogate uses a slur",
        "kind": "forbidden_content",
    }
    engine.respond(
        "POST",
        SANITIZE,
        422,
        json=error_json(
            "guardrail_violation", "refused", violations=[violation], model_calls=1, elapsed_ms=2
        ),
    )
    html = sanitize_post(operator_client, confirmed="1").content.decode()
    assert '<table class="violations">' in html
    assert "<code>no_slurs</code>" in html
    assert "The surrogate uses a slur" in html
    assert html.index("violations") < html.index('name="span"')
    assert ">\nthe stranger</textarea>" in html
    assert hidden(html, "idempotency_key") != KEY


def test_sanitize_module_off_renders_the_greyed_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", SANITIZE, 409, json=error_json("module_off", "off", module="episodic"))
    response = sanitize_post(operator_client, confirmed="1")
    assert response.status_code == 409
    assert "Not available for this story" in response.content.decode()


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (404, "not_found", "Not found (it may have been deleted or truncated)"),
        (409, "story_archived", "This story is archived"),
        (409, "idempotency_conflict", "That form was already used for another request"),
    ],
)
def test_a_sanitize_refusal_shows_above_the_form(
    operator_client: Client, engine: FakeEngine, status: int, code: str, text: str
) -> None:
    engine.respond("POST", SANITIZE, status, json=error_json(code, "gone"))
    html = sanitize_post(operator_client, confirmed="1").content.decode()
    assert '<p class="errorlist sanitize-refusal">' + text in html
    assert hidden(html, "idempotency_key") != KEY
    assert ">\nTobin</textarea>" in html


def test_sanitize_erasure_pending_is_the_held_wrapper(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "POST", SANITIZE, 503, json=error_json("erasure_pending", "held", story_id=STORY)
    )
    response = sanitize_post(operator_client, confirmed="1")
    assert response.status_code == 503
    assert held_text(None) in response.content.decode()


@pytest.mark.parametrize("failure", ["503", "unreachable"])
def test_a_sanitize_failure_renders_the_panel_above_the_form(
    operator_client: Client, engine: FakeEngine, failure: str
) -> None:
    if failure == "503":
        engine.respond("POST", SANITIZE, 503, json=error_json("database_unavailable", "down"))
        expected = "database_unavailable"
    else:
        engine.fail("POST", SANITIZE, httpx.ConnectError("down"))
        expected = "Engine unreachable"
    html = sanitize_post(operator_client, confirmed="1").content.decode()
    assert expected in html
    assert ">\nTobin</textarea>" in html
    assert hidden(html, "idempotency_key") != KEY


def test_a_sanitize_records_one_row_with_only_lengths(
    operator_client: Client, engine: FakeEngine
) -> None:
    sanitize_post(operator_client, confirmed="1")
    (recorded,) = EngineOperation.objects.all()
    assert (recorded.kind, recorded.http_method, recorded.path) == (
        "sanitize_memory",
        "POST",
        SANITIZE_PATH,
    )
    assert (recorded.entity_type, recorded.entity_id, recorded.story_id) == ("story", STORY, STORY)
    assert recorded.idempotency_key == KEY
    assert recorded.request["body"] == {"span_length": 5, "surrogate_length": 12}
    assert "Tobin" not in json.dumps(recorded.request) + json.dumps(recorded.response)
