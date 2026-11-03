"""Compensate a listed external effect through a form and a confirm page (#264).

Runs against the real urlconf on the schema-validating fake Engine, under Structured v3 (#239)
and #232's M6 cast (``m6/story.json``: Mara the player, Alice, Bob; revision 12).
``m6/compensated.json`` answers with the new row ``BX04`` compensating ``BX03`` at turn 3⁺²
(revision 13, operation ``…QP…52``, Engine position ``3⁺2``). Refusal bodies are built inline
(D16)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.reconciliation import BOTH_GIVEN, CAST_UNREAD, NONE_GIVEN
from console.forms.world import DELTA_INVALID
from console.models import EngineOperation
from console.positions import knowledge_position_text, knowledge_position_url
from console.reconciliation import MALFORMED_OUTBOX_ID, RESULT_UNAVAILABLE
from console.views import inspect as views
from console.views import inspect_reconciliation
from console.world import five_part, state_not_found_text
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, STORY_PATH, lite_m6, structured_v3
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
OUTBOX = "01JM6BX0000000000000000003"
NEW_ROW = "01JM6BX0000000000000000004"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
BOB = "01JM6CH0000000000000000003"
KEY = "01JM6QP00000000000000000K1"
COMPENSATE = "/stories/{story_id}/reconciliation/{outbox_id}/compensate"
COMPENSATE_PATH = f"/stories/{STORY}/reconciliation/{OUTBOX}/compensate"
LIST_URL = f"/stories/{STORY}/inspect/effects/reconciliation/"
URL = f"/stories/{STORY}/inspect/effects/reconciliation/{OUTBOX}/compensate/"
COMPENSATED_URL = f"/stories/{STORY}/inspect/effects/reconciliation/compensated/"
SHELL_PATHS = [f"/stories/{STORY}/effective-config", f"/stories/{STORY}"]
ROW_ANCHOR = f"{LIST_URL}#outbox-{OUTBOX}"
MINUS = "\u2212"
QUERY = {
    "expected_revision": "11",
    "provider": "bank",
    "value": "balance",
    "listed_reason": "confirmed_discarded_origin",
    "state": "gold",
    "next": ROW_ANCHOR,
}
# What the form posts (the hidden display fields as the form page renders them).
FORM = {
    "idempotency_key": KEY,
    "expected_revision": "12",
    "provider": "bank",
    "provider_value": "balance",
    "listed_reason": "confirmed_discarded_origin",
    "state": "gold",
    "scope_key": ALICE,
    "delta": "500",
    "value": "",
    "reason": "refund",
}
STATES_OFF_REASON = (
    "Needs <code>states.level</code> at <code>prompt_only</code> or above "
    "(this story runs <code>off</code>)"
)


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"m6/{name}"))
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(inspect_reconciliation, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v3())
    fake.respond("GET", STORY_PATH, 200, json=fixture("story"))
    fake.respond("POST", COMPENSATE, 201, json=fixture("compensated"))
    with override_settings(ENGINE_URL="http://engine.test", ROOT_URLCONF="epistrel_console.urls"):
        yield fake


def compensations(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST" and r.url.path == COMPENSATE_PATH]


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method == "POST"]


def get(client: Client, url: str = URL, query: dict[str, str] | None = None) -> str:
    target = f"{url}?{urlencode(QUERY if query is None else query)}"
    response = client.get(target)
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, url: str = URL, *, confirmed: bool = False, **fields: Any) -> str:
    data: dict[str, Any] = {**FORM, **({"confirmed": "1"} if confirmed else {})}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    response = client.post(url, data)
    assert response.status_code == 200
    return response.content.decode()


def hidden(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="hidden" name="{name}" value="([^"]*)">', html)
    return unescape(found.group(1)) if found is not None else None


def input_value(html: str, name: str) -> str | None:
    found = re.search(rf'<input type="text" name="{name}"[^>]*?(?: value="([^"]*)")?[^>]*>', html)
    if found is None:
        return None
    return unescape(found.group(1)) if found.group(1) is not None else ""


def options(html: str, name: str) -> list[tuple[str, str, bool]]:
    found = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert found is not None
    return [
        (unescape(value), unescape(label), bool(selected))
        for value, selected, label in re.findall(
            r'<option value="([^"]*)"( selected)?>(.*?)</option>', found.group(1)
        )
    ]


def field_errors(html: str, name: str) -> list[str]:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return [unescape(e) for e in re.findall(r'<p class="errorlist">(.*?)</p>', found.group(1))]


def non_field_errors(html: str) -> list[str]:
    found = re.search(r'<div class="errors" role="alert">(.*?)</div>', html, re.S)
    if found is None:
        return []
    return [unescape(e) for e in re.findall(r'<p class="errorlist">(.*?)</p>', found.group(1))]


def refusal(html: str) -> str:
    found = re.search(r'<p class="errorlist compensate-refusal" role="alert">(.*?)</p>', html, re.S)
    assert found is not None
    return found.group(1)


def fact(html: str, label: str) -> str:
    found = re.search(rf"<dt>{re.escape(label)}</dt><dd>(.*?)</dd>", html, re.S)
    assert found is not None, label
    return found.group(1)


def lines(html: str) -> list[str]:
    found = re.search(r'<ul class="reconciliation-result-lines">(.*?)</ul>', html, re.S)
    assert found is not None
    return re.findall(r"<li [^>]*>(.*?)</li>", found.group(1), re.S)


def more(html: str) -> str:
    found = re.search(r'<details class="reconciliation-result-more">(.*?)</details>', html, re.S)
    assert found is not None
    return found.group(1)


def is_form_page(html: str) -> bool:
    return 'class="compensate-form"' in html and "reconciliation-result-lines" not in html


def is_confirm_page(html: str) -> bool:
    return '<div class="confirm">' in html and 'class="compensate-form"' not in html


def sent_effect(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = compensations(engine)
    body: dict[str, Any] = json.loads(sent.content)
    return body


# --- the form page -----------------------------------------------------------------------------


def test_form_get_makes_only_the_shell_reads(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert "<title>Inspect · Compensate an effect · Harbour Ledger" in html
    assert '<h3 class="compensate-title">Compensate an effect</h3>' in html
    assert f'<a class="active" aria-current="page" href="{LIST_URL}">Reconciliation</a>' in html
    assert input_value(html, "state") == "gold"
    assert fact(html, "Outbox id") == f'<code class="outbox-id">{OUTBOX}</code>'
    provider = reverse("engine:provider", args=["bank"])
    assert fact(html, "Provider") == f'<a href="{provider}">bank</a>'
    assert fact(html, "Provider value") == "balance"
    assert fact(html, "Listed as") == (
        '<span title="confirmed_discarded_origin">Confirmed discarded origin</span>'
    )
    # D8: the shell's story revision (12) wins over the link's (11).
    assert hidden(html, "expected_revision") == "12"
    assert hidden(html, "provider_value") == "balance"
    assert hidden(html, "idempotency_key") not in (None, "", KEY)
    assert '<button type="submit" class="compensate-submit">Review</button>' in html
    assert f'<a href="{ROW_ANCHOR}">Cancel</a>' in html
    assert "A signed change for a number State, e.g. -500" in html
    assert escape("put text in double quotes") in html
    assert '<fieldset class="compensate-fields">' in html
    # **Complement:** the call log has only the effective-config and story reads.
    assert [r.url.path for r in engine.requests] == SHELL_PATHS


def test_form_facts_dash_without_the_query(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, query={})
    for label in ("Provider", "Provider value", "Listed as"):
        assert fact(html, label) == "—"
    assert input_value(html, "state") == ""
    assert hidden(html, "expected_revision") == "12"


def test_link_revision_without_the_story_read(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("unavailable", "down"))
    html = get(operator_client)
    assert hidden(html, "expected_revision") == "11"


def test_character_choices_follow_the_cast(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert options(html, "scope_key") == [
        ("", "— (a story State)", True),
        (MARA, "Mara (player)", False),
        (ALICE, "Alice", False),
        (BOB, "Bob", False),
    ]
    # A posted id outside the cast is kept as an extra option (C13) and sent.
    other = "01JM6CH0000000000000000009"
    html = post(operator_client, scope_key=other.lower(), edit="1")
    assert options(html, "scope_key")[-1] == (other, f"{other} (not in the cast)", True)
    post(operator_client, scope_key=other, confirmed=True)
    assert sent_effect(engine)["effect"]["scope_key"] == other


def test_free_text_character_without_the_story_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("unavailable", "down"))
    html = get(operator_client)
    assert '<select name="scope_key"' not in html
    assert input_value(html, "scope_key") == ""
    assert escape(CAST_UNREAD) in html
    post(operator_client, scope_key="  01jm6ch0000000000000000002 ", confirmed=True)
    assert sent_effect(engine)["effect"]["scope_key"] == ALICE
    # **Complement:** blank sends no scope_key; over 64 characters is a field error, no call.
    engine.requests.clear()
    post(operator_client, scope_key="", confirmed=True)
    assert "scope_key" not in sent_effect(engine)["effect"]
    engine.requests.clear()
    html = post(operator_client, scope_key="X" * 65, confirmed=True)
    assert field_errors(html, "scope_key")
    assert compensations(engine) == []


@pytest.mark.parametrize(
    ("fields", "where", "message"),
    [
        ({"delta": "5", "value": "true"}, None, BOTH_GIVEN),
        ({"delta": "", "value": ""}, None, NONE_GIVEN),
        ({"delta": "5e3"}, "delta", DELTA_INVALID),
        ({"delta": "five"}, "delta", DELTA_INVALID),
        ({"reason": ""}, "reason", "This field is required."),
        ({"reason": "r" * 201}, "reason", "at most 200 characters"),
        ({"state": ""}, "state", "This field is required."),
    ],
)
@pytest.mark.parametrize("confirmed", [False, True])
def test_invalid_form_makes_no_call(
    operator_client: Client,
    engine: FakeEngine,
    fields: dict[str, str],
    where: str | None,
    message: str,
    confirmed: bool,
) -> None:
    html = post(operator_client, confirmed=confirmed, **fields)
    assert is_form_page(html)
    errors = non_field_errors(html) if where is None else field_errors(html, where)
    assert any(message in e for e in errors), errors
    # D8: local invalid input keeps the posted key and revision.
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"
    # **Complement:** no POST to …/compensate.
    assert compensations(engine) == []
    assert posts(engine) == []


def test_valid_form_shows_the_confirm_page_without_calling(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client)
    assert is_confirm_page(html)
    assert "<title>Compensate an effect — Epistrel Console</title>" in html
    assert f"Send a compensating effect for <em>{OUTBOX}</em>?" in html
    assert "<p>gold · Alice · +500 — “refund”</p>" in html
    assert "The listed effect shows Compensated until that row is confirmed." in html
    assert fact(html, "Story id") == f"<code>{STORY}</code>"
    assert fact(html, "Outbox id") == f"<code>{OUTBOX}</code>"
    provider = reverse("engine:provider", args=["bank"])
    assert fact(html, "Provider") == f'<a href="{provider}"><code>bank</code></a>'
    assert fact(html, "Provider value") == "<code>balance</code>"
    for name in ("state", "scope_key", "delta", "reason", "provider", "provider_value"):
        assert hidden(html, name) == FORM[name], name
    assert hidden(html, "value") == ""
    assert hidden(html, "expected_revision") == "12"
    assert hidden(html, "confirmed") == "1"
    assert hidden(html, "idempotency_key") == KEY  # the carried key
    assert '<button type="submit">Send compensation</button>' in html
    assert 'name="edit" value="1"' in html
    assert 'class="danger"' not in html
    # **Complement:** no POST.
    assert posts(engine) == []


def test_confirm_line_words_values_and_story_states(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, scope_key="", delta="", value='"12"')
    assert "<p>gold · story State · &quot;12&quot; — “refund”</p>" in html
    assert hidden(html, "value") == '"12"'  # as typed, never the cleaned "12"
    html = post(operator_client, delta="-3")
    assert f"gold · Alice · {MINUS}3 — “refund”" in html
    assert posts(engine) == []


def test_edit_returns_to_the_form_with_values(operator_client: Client, engine: FakeEngine) -> None:
    confirm = post(operator_client)
    data = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', confirm))
    data = {k: unescape(v) for k, v in data.items()}
    response = operator_client.post(URL, {**data, "edit": "1"})
    html = response.content.decode()
    assert is_form_page(html)
    assert input_value(html, "delta") == "500"
    assert input_value(html, "reason") == "refund"
    assert input_value(html, "state") == "gold"
    assert (ALICE, "Alice", True) in options(html, "scope_key")
    assert hidden(html, "idempotency_key") == KEY  # the confirm page's key, unused
    assert hidden(html, "provider") == "bank"
    assert non_field_errors(html) == []
    # **Complement:** no POST.
    assert posts(engine) == []


def test_cancel_link_targets_the_row(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client)
    assert f'<a href="{ROW_ANCHOR}">Cancel</a>' in html
    assert posts(engine) == []


def test_confirmed_post_sends_the_effect(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, confirmed=True)
    (sent,) = compensations(engine)
    assert json.loads(sent.content) == {
        "effect": {"state": "gold", "scope_key": ALICE, "delta": 500, "reason": "refund"}
    }
    assert sent.headers["Idempotency-Key"] == KEY
    assert sent.headers["X-Epistrel-Expected-Revision"] == "12"
    assert len(posts(engine)) == 1
    # **Complement:** a story-scoped choice sends no scope_key; a value sends no delta.
    for fields, expected in (
        ({"scope_key": ""}, {"state": "gold", "delta": 500, "reason": "refund"}),
        (
            {"delta": "", "value": "sword"},
            {"state": "gold", "scope_key": ALICE, "value": "sword", "reason": "refund"},
        ),
        ({"delta": "", "value": "true"}, {"value": True}),
        ({"delta": "", "value": '"true"'}, {"value": "true"}),
        ({"delta": "2.5"}, {"delta": 2.5}),
    ):
        engine.requests.clear()
        post(operator_client, confirmed=True, **fields)
        effect = sent_effect(engine)["effect"]
        assert {k: effect.get(k) for k in expected} == expected, fields
        assert ("delta" in effect) != ("value" in effect)
    # A missing or non-digit expected_revision sends no header.
    for revision in ("", "x"):
        engine.requests.clear()
        post(operator_client, confirmed=True, expected_revision=revision)
        (sent,) = compensations(engine)
        assert "X-Epistrel-Expected-Revision" not in sent.headers


def test_tampered_hidden_fields_return_to_the_form(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, confirmed=True, value="7")
    assert is_form_page(html)
    assert non_field_errors(html) == [BOTH_GIVEN]
    # **Complement:** no POST.
    assert compensations(engine) == []


def test_malformed_confirmed_key_rerenders_the_confirm_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = post(operator_client, confirmed=True, idempotency_key="bad key")
    assert is_confirm_page(html)
    assert "This confirmation was incomplete" in html
    assert posts(engine) == []


# --- the gate ----------------------------------------------------------------------------------


def test_gate_closed_refuses_without_writing(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=lite_m6())
    html = get(operator_client)
    assert '<fieldset class="compensate-fields" disabled>' in html
    assert f'<small class="gate-reason" id="gate-compensate">{STATES_OFF_REASON}</small>' in html
    assert 'class="compensate-submit" disabled aria-describedby="gate-compensate"' in html
    for confirmed in (False, True):
        html = post(operator_client, confirmed=confirmed)
        assert is_form_page(html)
        config = reverse("stories:config", args=[STORY])
        assert refusal(html) == f'{STATES_OFF_REASON} <a href="{config}">Config</a>'
        assert hidden(html, "idempotency_key") == KEY
    # **Complement:** the fake saw no POST to …/compensate (C6).
    assert compensations(engine) == []
    assert posts(engine) == []


# --- the result page ---------------------------------------------------------------------------


def test_result_page_lines(operator_client: Client, engine: FakeEngine) -> None:
    html = post(operator_client, confirmed=True)
    row = EngineOperation.objects.get(kind="compensate")
    position = five_part(fixture("compensated")["slot"])
    assert position is not None
    slot_text = knowledge_position_text(position)
    assert (
        "Compensation sent: "
        f'<a href="{LIST_URL}#outbox-{NEW_ROW}"><code class="outbox-id">{NEW_ROW}</code></a> '
        f'(Intended) compensates <a href="{ROW_ANCHOR}"><code class="outbox-id">{OUTBOX}</code>'
        "</a></h3>"
    ) in html
    operation = reverse("operations:detail", args=[row.pk])
    assert lines(html) == [
        f'<a href="{knowledge_position_url(STORY, position)}">Slot: {slot_text}</a>',
        f'<a href="{operation}">Operation: <code>01JM6QP0000000000000000052</code></a>',
        "Story revision now 13",
    ]
    assert html.count("3⁺2") == 1
    assert "<dt>Engine position</dt><dd><code>3⁺2</code></dd>" in more(html)
    assert f"<dt>Idempotency key</dt><dd><code>{KEY}</code></dd>" in more(html)
    assert f'<a class="reconciliation-back" href="{LIST_URL}">Back to Reconciliation</a>' in html
    assert f'href="{COMPENSATED_URL}?report={row.pk}">Link to this result</a>' in html
    assert f'<a class="active" aria-current="page" href="{LIST_URL}">Reconciliation</a>' in html
    assert "<title>Inspect · Compensate · Harbour Ledger" in html
    assert "Re-verified" not in html


def test_report_rerenders_from_the_log_row(operator_client: Client, engine: FakeEngine) -> None:
    first = post(operator_client, confirmed=True)
    row = EngineOperation.objects.get(kind="compensate")
    engine.requests.clear()
    response = operator_client.get(f"{COMPENSATED_URL}?report={row.pk}")
    assert response.status_code == 200
    html = response.content.decode()
    assert [r.url.path for r in engine.requests] == SHELL_PATHS
    assert lines(html) == lines(first)
    assert more(html) == more(first)
    assert f'<a class="active" aria-current="page" href="{LIST_URL}">Reconciliation</a>' in html


@pytest.mark.parametrize("case", ["settle_now", "story", "error", "unparsed", "nonint", "missing"])
def test_report_unavailable(operator_client: Client, engine: FakeEngine, case: str) -> None:
    post(operator_client, confirmed=True)
    row = EngineOperation.objects.get(kind="compensate")
    report: object = row.pk
    if case == "settle_now":
        row.kind = "settle_now"
    elif case == "story":
        row.story_id = "01JM6ST0000000000000000002"
    elif case == "error":
        row.outcome = EngineOperation.Outcome.ERROR
    elif case == "unparsed":
        row.response = {"outbox_id": 3}
    elif case == "nonint":
        report = "x"
    elif case == "missing":
        report = row.pk + 1
    row.save()
    engine.requests.clear()
    response = operator_client.get(f"{COMPENSATED_URL}?report={report}")
    assert response.status_code == 200
    html = response.content.decode()
    assert f'{escape(RESULT_UNAVAILABLE)} — <a href="{LIST_URL}">Reconciliation</a>' in html
    assert "reconciliation-result-lines" not in html
    assert [r.url.path for r in engine.requests] == SHELL_PATHS


# --- refusals ----------------------------------------------------------------------------------


def refuse(
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None = None,
    **details: Any,
) -> None:
    engine.respond("POST", COMPENSATE, status, json=error_json(code, **details), headers=headers)


def refused(client: Client, **fields: Any) -> str:
    html = post(client, confirmed=True, **fields)
    assert is_form_page(html)
    assert "Compensation sent" not in html
    # **Complement:** the posted Delta, Value and Reason are still in the inputs.
    assert input_value(html, "delta") == fields.get("delta", "500")
    assert input_value(html, "value") == fields.get("value", "")
    assert input_value(html, "reason") == "refund"
    assert hidden(html, "provider") == "bank"
    return html


@pytest.mark.parametrize(
    ("status", "code", "headers", "details", "expected"),
    [
        (
            422,
            "provider_read_only",
            None,
            {"provider": "shop"},
            "shop is read-only — it can't take a compensating effect",
        ),
        (
            422,
            "state_unpermitted",
            None,
            {"reason": "not_external"},
            "Only a State an external provider owns can be compensated",
        ),
        (
            409,
            "module_off",
            None,
            {"module": "states"},
            STATES_OFF_REASON.replace("(this story runs <code>off</code>)", ""),
        ),
        (
            423,
            "story_busy",
            {"Retry-After": "7"},
            {"reason": "in_flight"},
            "Story busy — another turn is running · retry after 7 s",
        ),
        (
            409,
            "story_archived",
            None,
            {},
            "This story is archived — unarchive it to compensate an effect.",
        ),
        (409, "revision_conflict", None, {"current_revision": 14}, "changed"),
    ],
)
def test_refusals_return_to_the_form_keeping_values(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    headers: dict[str, str] | None,
    details: dict[str, Any],
    expected: str,
) -> None:
    refuse(engine, status, code, headers, **details)
    html = refused(operator_client)
    text = refusal(html)
    assert expected.strip() in unescape(text) or expected.strip() in text
    if code == "module_off":
        assert f'<a href="{reverse("stories:config", args=[STORY])}">Config</a>' in text
    # D8: an answered refusal: a fresh key, the revision from this POST's story read.
    assert hidden(html, "idempotency_key") not in (None, KEY)
    assert hidden(html, "expected_revision") == "12"
    assert len(compensations(engine)) == 1


def test_state_not_found_lands_on_state(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found", state="silver")
    html = refused(operator_client, state="silver")
    assert field_errors(html, "state") == [state_not_found_text("silver")]
    assert "compensate-refusal" not in html


def test_no_listing_links_the_list(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "not_found", reason="no_listing")
    html = refused(operator_client)
    assert refusal(html) == (
        "This effect is no longer listed for reconciliation — reload the list "
        f'<a href="{LIST_URL}">Reconciliation</a>'
    )


@pytest.mark.parametrize(
    ("field", "where"),
    [
        ("body.effect.scope_key", "scope_key"),
        ("body.effect.delta", "delta"),
        ("body.effect.value", "value"),
        ("body.effect.state", "state"),
    ],
)
def test_validation_error_lands_on_the_field(
    operator_client: Client, engine: FakeEngine, field: str, where: str
) -> None:
    engine.respond(
        "POST",
        COMPENSATE,
        422,
        json=error_json("validation_error", "a character is required", field=field),
    )
    html = refused(operator_client)
    assert field_errors(html, where) == ["a character is required"]
    assert "compensate-refusal" not in html
    assert hidden(html, "idempotency_key") != KEY


def test_validation_error_elsewhere_goes_above(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST", COMPENSATE, 422, json=error_json("validation_error", "bad", field="body.effect")
    )
    html = refused(operator_client)
    assert non_field_errors(html) == ["effect: bad"]


def test_unknown_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 422, "later_code")
    html = refused(operator_client)
    assert 'class="engine-error' in html
    assert "later_code" in html
    assert "compensate-refusal" not in html


def test_unreachable_keeps_the_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    engine.side_effect("POST", COMPENSATE, boom)
    html = refused(operator_client)
    assert 'class="engine-error' in html
    assert hidden(html, "idempotency_key") == KEY
    assert hidden(html, "expected_revision") == "12"


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 503, "erasure_pending")
    response = operator_client.post(URL, {**FORM, "confirmed": "1"})
    assert response.status_code == 503
    assert len(compensations(engine)) == 1


# --- a malformed outbox id ---------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "post"])
def test_malformed_outbox_id_is_404_with_no_call(
    operator_client: Client, engine: FakeEngine, method: str
) -> None:
    url = URL.replace(OUTBOX, "not-an-id")
    data = {**FORM, "confirmed": "1"} if method == "post" else {}
    response = getattr(operator_client, method)(url, data)
    assert response.status_code == 404
    html = response.content.decode()
    assert escape(MALFORMED_OUTBOX_ID) in html
    assert f'<a href="{LIST_URL}">← Reconciliation</a>' in html
    assert engine.requests == []


def test_url_outbox_id_is_trimmed_and_upper_cased(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, URL.replace(OUTBOX, OUTBOX.lower()))
    assert fact(html, "Outbox id") == f'<code class="outbox-id">{OUTBOX}</code>'
    post(operator_client, URL.replace(OUTBOX, OUTBOX.lower()), confirmed=True)
    assert len(compensations(engine)) == 1
