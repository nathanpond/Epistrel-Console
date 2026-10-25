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
