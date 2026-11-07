"""#303: undo and redo an amendment (``stories:amendment-undo`` / ``stories:amendment-redo``,
``console/views/amendments.py``), against the schema-validating fake Engine over #292's
``m7/story.json`` cast and ``full_v4()``: which action an amendment offers (its page and the
Amendments list), the open form (GET, ineligible, not found), the POST (one write with E4's
modes and the posted key, no expected revision, no amendment read; a 303 to #297's session
page), and the refusals with D8's key rule."""

import json
import logging
import re
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.utils.html import escape

from console.amendments import (
    OPENED_UNREADABLE,
    REDO_EXPLAIN,
    UNDO_EXPLAIN,
    USE_REDO_HINT,
    amendment_action,
)
from console.engine.models import AmendmentSummary
from console.rewrites import (
    AMENDMENT_TEXT,
    NOT_APPLIED_PREFIX,
    OMNISCIENT_LINE,
    OMNISCIENT_REQUIRED_TEXT,
    SESSION_LIMIT_TEXT,
)
from console.upgrades import BACKFILL_PAUSED_TEXT
from tests.fakes.engine import FakeEngine
from tests.fixtures import ENGINE
from tests.play.helpers import error_json
from tests.rewrites.helpers import (
    CONFIG_READ,
    M7_PAGES,
    STORY,
    STORY_READ,
    answer_raw,
    calls,
    engine,
    key_in,
    m7,
    queries,
)

pytestmark = pytest.mark.django_db
__all__ = ["engine"]  # the fixture, re-exported for pytest

S = f"/stories/{STORY}/"
AMENDMENTS = f"{S}amendments/"
A1 = "01JM7AM0000000000000000001"  # rewrite, active
A2 = "01JM7AM0000000000000000002"  # rewrite, reverted by A4
A4 = "01JM7AM0000000000000000004"  # undo of A2, active
UNDO_URL = f"{AMENDMENTS}{A1}/undo/"
REDO_URL = f"{AMENDMENTS}{A2}/redo/"
ONE_ROUTE = "/stories/{story_id}/amendments/{amendment_id}"
LIST_ROUTE = "/stories/{story_id}/amendments"
UNDO_ROUTE = "/stories/{story_id}/amendments/{amendment_id}/undo"
REDO_ROUTE = "/stories/{story_id}/amendments/{amendment_id}/redo"
SESSION_ROUTE = "/stories/{story_id}/rewrites/{session_id}"
UNDO_PATH = f"/stories/{STORY}/amendments/{A1}/undo"
REDO_PATH = f"/stories/{STORY}/amendments/{A2}/redo"
UNDO_SESSION = "01JM7RW0000000000000000010"
REDO_SESSION = "01JM7RW0000000000000000011"
KEY = "01JM7KY0000000000000000009"
WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SUBMIT = re.compile(r'<button[^>]*type="submit"')


def no_submit(html: str) -> bool:
    """The open form's section holds no submit button (the page chrome may)."""
    section = html.split('<section class="amendment-open')[1].split("</section>")[0]
    return SUBMIT.search(section) is None


# --- helpers -------------------------------------------------------------------------------------


def amendment(status: str = "active", kind: str = "rewrite", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("amendment")
    body.update(status=status, kind=kind, **changes)
    return body


def detail(engine: FakeEngine, body: Any | None = None) -> None:
    engine.respond("GET", ONE_ROUTE, 200, json=body if body is not None else m7("amendment"))


def registry(engine: FakeEngine) -> None:
    body = json.loads((ENGINE.parent / "inspect" / "predicates_list.json").read_text())
    engine.respond("GET", "/stories/{story_id}/predicates", 200, json=body)


def page(client: Client, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status
    return str(response.content.decode())


def post(client: Client, url: str = UNDO_URL, **data: Any) -> Any:
    form = {"idempotency_key": KEY, "resolution": "synchronous", **data}
    return client.post(url, form)


def writes(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.method in WRITES]


def sent(engine: FakeEngine) -> Any:
    (request,) = writes(engine)
    return json.loads(request.content)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def squeezed(html: str) -> str:
    return re.sub(r">\s*\n\s*<", "><", html)


def session_read(name: str) -> dict[str, Any]:
    """A session fixture as ``GET …/rewrites/{id}`` answers it (E16's one-off: the write
    answer's own members dropped)."""
    body: dict[str, Any] = m7(name)
    for key in ("timeline_revision", "apply", "idempotency_key"):
        body.pop(key, None)
    return body


def summary(**changes: Any) -> AmendmentSummary:
    raw = {
        "id": A1,
        "kind": "rewrite",
        "status": "active",
        "author": "console:nate",
        "tx_time": "2026-11-04T12:00:00Z",
        "parts_count": 1,
        **changes,
    }
    parsed = AmendmentSummary.from_json(raw)
    assert parsed is not None
    return parsed


# --- which action an amendment offers -------------------------------------------------------------


def test_amendment_action_rules() -> None:
    undo = amendment_action(summary(), STORY)
    assert undo is not None
    assert (undo.label, undo.url) == ("Undo…", UNDO_URL)
    redo = amendment_action(summary(id=A2, status="reverted"), STORY)
    assert redo is not None
    assert (redo.label, redo.url) == ("Redo…", REDO_URL)
    hint = amendment_action(summary(id=A4, kind="undo", undoes=A2), STORY)
    assert hint is not None
    assert (hint.label, hint.url, hint.hint_code) == ("", None, A2)
    assert hint.hint_url == f"{AMENDMENTS}{A2}/"
    assert amendment_action(summary(status="superseded"), STORY) is None


def test_active_rewrite_offers_undo_only(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine)
    registry(engine)
    html = page(operator_client, f"{AMENDMENTS}{A1}/")
    assert f'<a class="amendment-action" href="{UNDO_URL}">Undo…</a>' in html
    # **Complement:** no Redo link.
    assert "Redo…" not in html
    assert "/redo/" not in html
    assert writes(engine) == []


def test_reverted_offers_redo_only(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine, amendment("reverted", id=A2, reverted_by=A4, parts=[], relies_on=[]))
    html = page(operator_client, f"{AMENDMENTS}{A2}/")
    assert f'<a class="amendment-action" href="{REDO_URL}">Redo…</a>' in html
    # **Complement:** no Undo link.
    assert "Undo…" not in html
    assert "/undo/" not in html


def test_active_undo_offers_no_undo_and_points_to_redo(
    operator_client: Client, engine: FakeEngine
) -> None:
    detail(engine, amendment("active", "undo", id=A4, undoes=A2, parts=[], relies_on=[]))
    html = page(operator_client, f"{AMENDMENTS}{A4}/")
    assert (
        f'<span class="use-redo">{USE_REDO_HINT} <a href="{AMENDMENTS}{A2}/">'
        f'<code title="{A2}">…00000002</code></a></span>'
    ) in html
    assert "Undo…" not in html
    assert "Redo…" not in html


def test_active_undo_with_no_undoes_shows_a_dash(
    operator_client: Client, engine: FakeEngine
) -> None:
    detail(engine, amendment("active", "undo", id=A4, undoes=None, parts=[], relies_on=[]))
    html = page(operator_client, f"{AMENDMENTS}{A4}/")
    assert f'<span class="use-redo">{USE_REDO_HINT} —</span>' in html


def test_list_rows_carry_the_same_actions(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST_ROUTE, 200, json=m7("amendments"))
    html = squeezed(page(operator_client, AMENDMENTS))
    assert "<th>Actions</th>" in html

    def cell(anchor: str) -> str:
        found = re.search(rf'<tr id="amendment-{anchor}">.*?<td class="actions">(.*?)</td>', html)
        assert found, anchor
        return found.group(1)

    assert cell(A1) == f'<a class="amendment-action" href="{UNDO_URL}">Undo…</a>'
    assert cell(A2) == f'<a class="amendment-action" href="{REDO_URL}">Redo…</a>'
    assert cell(A4) == ""  # E22: an empty cell; the hint is detail-only
    assert USE_REDO_HINT not in html


# --- the open form (GET) --------------------------------------------------------------------------


def test_undo_form_reads_and_renders(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine)
    html = page(operator_client, UNDO_URL)
    assert calls(engine) == [
        ("GET", STORY_READ),
        ("GET", CONFIG_READ),
        ("GET", f"/stories/{STORY}/amendments/{A1}"),
    ]
    assert "<title>Undo amendment …00000001 · " in html
    assert html.count(OMNISCIENT_LINE) == 1
    assert '<dl class="amendment-summary">' in html
    assert UNDO_EXPLAIN in unescape(html)
    assert (
        html.index(OMNISCIENT_LINE)
        < html.index("amendment-summary")
        < html.index('class="explanation"')
        < html.index('class="amendment-open-form"')
    )
    assert '<option value="synchronous" selected>Ask me about every conflict</option>' in html
    assert 'name="apply_directly"' in html
    assert "checked" not in html.split("Advanced")[1][:200]
    assert '<button type="submit">Plan the undo</button>' in html
    assert f'<a class="cancel" href="{AMENDMENTS}{A1}/">Cancel</a>' in html
    first = key_in(html)
    assert key_in(page(operator_client, UNDO_URL)) != first  # a fresh key per render
    assert writes(engine) == []


def test_redo_form_reads_and_renders(operator_client: Client, engine: FakeEngine) -> None:
    detail(engine, amendment("reverted", id=A2, reverted_by=A4))
    html = page(operator_client, REDO_URL)
    assert "<title>Redo amendment …00000002 · " in html
    assert REDO_EXPLAIN in unescape(html)
    assert '<button type="submit">Plan the redo</button>' in html
    assert writes(engine) == []


@pytest.mark.parametrize(
    ("url", "body", "code", "action"),
    [
        (
            UNDO_URL,
            amendment("reverted"),
            "amendment_already_reverted",
            f'<a class="amendment-action" href="{AMENDMENTS}{A1}/redo/">Redo…</a>',
        ),
        (UNDO_URL, amendment("active", "undo", undoes=A2), "use_redo", USE_REDO_HINT),
        (
            f"{AMENDMENTS}{A1}/redo/",
            amendment("active"),
            "amendment_not_reverted",
            f'<a class="amendment-action" href="{UNDO_URL}">Undo…</a>',
        ),
    ],
    ids=["already-undone", "use-redo", "not-undone"],
)
def test_get_ineligible_shows_sentence_and_no_submit(
    operator_client: Client, engine: FakeEngine, url: str, body: Any, code: str, action: str
) -> None:
    detail(engine, body)
    html = page(operator_client, url)
    assert f'<p class="warning ineligible">{AMENDMENT_TEXT[code]}</p>' in unescape(html)
    assert action in html
    assert no_submit(html)
    assert "amendment-open-form" not in html
    assert "Back to the amendment" in html
    assert writes(engine) == []


def test_get_not_found(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 404, json=error_json("amendment_not_found"))
    html = page(operator_client, UNDO_URL)
    assert AMENDMENT_TEXT["amendment_not_found"] in html
    assert "<title>Undo amendment …00000001 · " in html
    assert html.count(OMNISCIENT_LINE) == 1
    assert no_submit(html)
    assert "Back to the amendment" in html
    assert writes(engine) == []


def test_get_read_failure_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ONE_ROUTE, 503, json=error_json("database_unavailable"))
    html = page(operator_client, UNDO_URL)
    assert 'class="engine-error' in html
    assert no_submit(html)
    assert writes(engine) == []


def test_unknown_status_shows_the_form(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "GET", ONE_ROUTE, amendment("superseded"))
    html = page(operator_client, UNDO_URL)
    assert '<button type="submit">Plan the undo</button>' in html


def test_malformed_id_is_django_404(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, f"{AMENDMENTS}not-an-id/undo/", status=404)
    assert operator_client.post(f"{AMENDMENTS}not-an-id/redo/", {}).status_code == 404
    assert engine.requests == []


# --- the open (POST) ------------------------------------------------------------------------------


def opened(engine: FakeEngine, name: str = "undo_session", route: str = UNDO_ROUTE) -> None:
    engine.respond("POST", route, 201, json=m7(name))


def test_undo_posts_modes_and_redirects(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine)
    response = post(operator_client)
    assert response.status_code == 303
    assert response["Location"] == f"{S}rewrites/{UNDO_SESSION}/"
    assert sent(engine) == {"preview": "preview_first", "resolution_mode": "synchronous"}
    (write,) = writes(engine)
    assert write.url.path == UNDO_PATH
    assert list(write.url.params.multi_items()) == [("viewpoint", "omniscient")]
    assert write.headers["Idempotency-Key"] == KEY
    assert flashes(response) == [
        f'Undo planned as rewrite session <code title="{UNDO_SESSION}">…00000010</code>'
        " — idempotency key <code>01JM7KY0000000000000000001</code>"
    ]
    # **Complement:** no expected revision, and no amendment read before the write.
    assert "X-Epistrel-Expected-Revision" not in write.headers
    assert calls(engine) == [("GET", STORY_READ), ("GET", CONFIG_READ), ("POST", UNDO_PATH)]


def test_apply_directly_and_threshold(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine)
    post(operator_client, resolution="threshold_high", apply_directly="on")
    assert sent(engine) == {"preview": "apply_directly", "resolution_mode": {"threshold": "high"}}


def test_redo_posts_to_redo(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine, "redo_session", REDO_ROUTE)
    response = post(operator_client, REDO_URL)
    assert response.status_code == 303
    assert response["Location"] == f"{S}rewrites/{REDO_SESSION}/"
    (write,) = writes(engine)
    assert write.url.path == REDO_PATH
    assert flashes(response)[0].startswith("Redo planned as rewrite session <code")


def test_cascade_question_preselected_after_redirect(
    operator_client: Client, engine: FakeEngine
) -> None:
    opened(engine)
    engine.respond("GET", SESSION_ROUTE, 200, json=session_read("undo_session"))
    response = post(operator_client, follow=False)
    html = page(operator_client, response["Location"])
    question = html.split('id="question-01JM7QN0000000000000000003"')[1].split("</fieldset>")[0]
    assert 'value="o1" checked' in question
    assert "Undo the dependent amendment too (recommended)" in question
    assert (
        '<p class="why">Why: cascade_undo keeps the dependent amendment from contradicting the '
        "story</p>"
    ) in question


def test_unreadable_201_warns_and_redirects(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "POST", UNDO_ROUTE, {"id": UNDO_SESSION}, 201)
    response = post(operator_client)
    assert response.status_code == 303
    assert response["Location"] == f"{S}rewrites/"
    messages = list(get_messages(response.wsgi_request))
    assert [m.level_tag for m in messages] == ["warning"]
    assert str(messages[0]) == f"{escape(OPENED_UNREADABLE)} — idempotency key <code>{KEY}</code>"


def test_invalid_modes_zero_writes(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, resolution="sideways")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Select a valid choice" in html
    assert key_in(html) == KEY
    assert writes(engine) == []
    assert f'Amendment <a href="{AMENDMENTS}{A1}/">' in html


def test_not_applied_directly_prefix(operator_client: Client, engine: FakeEngine) -> None:
    opened(engine)
    response = post(operator_client, apply_directly="on")
    (flash,) = flashes(response)
    assert flash.startswith(f"{NOT_APPLIED_PREFIX}Undo planned as rewrite session <code")


# --- refusals -------------------------------------------------------------------------------------

STUB = f"{S}rewrites/"  # #297's Rewrite page (the stub urlconf's #296 frame probe there)

REFUSALS = [
    pytest.param(
        409,
        error_json("amendment_already_reverted"),
        AMENDMENT_TEXT["amendment_already_reverted"],
        id="already-reverted",
    ),
    pytest.param(422, error_json("use_redo"), AMENDMENT_TEXT["use_redo"], id="use-redo"),
    pytest.param(
        409,
        error_json("amendment_not_reverted"),
        AMENDMENT_TEXT["amendment_not_reverted"],
        id="not-reverted",
    ),
    pytest.param(
        409,
        error_json("amendment_already_redone"),
        AMENDMENT_TEXT["amendment_already_redone"],
        id="already-redone",
    ),
    pytest.param(
        404,
        error_json("amendment_not_found"),
        AMENDMENT_TEXT["amendment_not_found"],
        id="not-found",
    ),
    pytest.param(
        409,
        error_json("rewrite_session_limit", limit=3),
        SESSION_LIMIT_TEXT.format(limit="3") + f' <a href="{STUB}">Rewrite</a>',
        id="limit",
    ),
    pytest.param(
        409,
        error_json("module_off", module="lite_rewrites"),
        "History rewrites need a planner for this story's pipeline (<code>lite_rewrites</code> "
        f'is off) <a href="{S}config/">Config</a>',
        id="module-off",
    ),
    pytest.param(
        409, error_json("story_paused", cause="backfill"), BACKFILL_PAUSED_TEXT, id="paused"
    ),
    pytest.param(423, error_json("story_busy"), "Story busy", id="busy"),
    pytest.param(403, error_json("omniscient_required"), OMNISCIENT_REQUIRED_TEXT, id="omniscient"),
]


@pytest.mark.parametrize(("status", "body", "text"), REFUSALS)
@pytest.mark.parametrize("url", [UNDO_URL, REDO_URL], ids=["undo", "redo"])
def test_refusals_word_and_keep_the_modes(
    operator_client: Client,
    engine: FakeEngine,
    caplog: pytest.LogCaptureFixture,
    settings: Any,
    url: str,
    status: int,
    body: dict[str, Any],
    text: str,
) -> None:
    settings.ROOT_URLCONF = M7_PAGES
    route = UNDO_ROUTE if url == UNDO_URL else REDO_ROUTE
    engine.respond("POST", route, status, json=body)
    with caplog.at_level(logging.WARNING, logger="console.engine"):
        response = post(operator_client, url, resolution="best_effort", apply_directly="on")
    assert response.status_code == 200
    html = unescape(response.content.decode())
    refusal = html.split('<p class="errorlist rewrite-refusal" role="alert">')[1].split("</p>")[0]
    assert refusal.startswith(text) if "<a" in text else text in refusal
    # The kept modes, a fresh key, one write and no read after it.
    assert '<option value="best_effort" selected>' in html
    assert 'name="apply_directly" id="id_apply_directly" checked' in html
    assert key_in(html) != KEY
    assert len(writes(engine)) == 1
    assert calls(engine)[-1][0] == "POST"
    assert not any(
        path.endswith(("/amendments/" + A1, "/amendments/" + A2)) for _, path in calls(engine)
    )
    # "Amendment <short id>" linked in place of the summary, then the explanation and the form.
    assert '<dl class="amendment-summary">' not in html
    assert (
        html.index('class="amendment-ref"')
        < html.index('class="explanation"')
        < html.index("rewrite-refusal")
        < html.index('class="amendment-open-form"')
    )
    if body["error"]["code"] == "omniscient_required":
        assert any("omniscient_required" in record.getMessage() for record in caplog.records)
    # Nothing changed: a re-GET of the amendment still reads Active (the fake's state).
    detail(engine)
    registry(engine)
    again = page(operator_client, f"{AMENDMENTS}{A1}/")
    assert 'title="active">Active</span>' in again


def test_a_refusal_changes_nothing(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", UNDO_ROUTE, 409, json=error_json("amendment_already_reverted"))
    post(operator_client)
    detail(engine)
    registry(engine)
    html = page(operator_client, f"{AMENDMENTS}{A1}/")
    assert 'title="active">Active</span>' in html


def test_transport_error_keeps_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", UNDO_ROUTE, httpx.ReadTimeout("slow"))
    response = post(operator_client, resolution="threshold_low")
    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="engine-error' in html
    assert key_in(html) == KEY
    assert '<option value="threshold_low" selected>' in html


def test_unreadable_refusal_keeps_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    answer_raw(engine, "POST", UNDO_ROUTE, {"not": "an envelope"}, 503)
    html = post(operator_client).content.decode()
    assert 'class="engine-error' in html
    assert key_in(html) == KEY


def test_idempotency_conflict_is_the_panel_with_a_fresh_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", UNDO_ROUTE, 409, json=error_json("idempotency_conflict", "reused key"))
    html = post(operator_client).content.decode()
    assert 'class="engine-error' in html
    assert "rewrite-refusal" not in html
    assert key_in(html) != KEY


def test_importing_on_the_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        UNDO_ROUTE,
        409,
        json=error_json("story_importing", stage="media", job_id="01JM7JB0000000000000000007"),
    )
    html = unescape(post(operator_client).content.decode())
    assert "This story is being imported" in html
    assert key_in(html) != KEY


def test_no_queries_carry_a_viewpoint_but_omniscient(
    operator_client: Client, engine: FakeEngine
) -> None:
    detail(engine)
    page(operator_client, f"{UNDO_URL}?viewpoint=01JM7CH0000000000000000002")
    assert queries(engine, "GET", f"/stories/{STORY}/amendments/{A1}") == [
        [("viewpoint", "omniscient")]
    ]
