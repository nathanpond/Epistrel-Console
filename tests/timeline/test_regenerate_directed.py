"""Regenerate with a directive (#301, FR-COMMIT-12): the confirm page's Direction box and E4's
modes, the body they send, a steered 200 (or Lite's nudge), a 202 ``DirectedPending`` landing on
the held reply's rewrite session (#297), and the directive refusals. #292's fallback (#43's panel
while ``stories:rewrite`` doesn't resolve) stays."""

import copy
import re
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client

from console.directives import (
    APPLIED_DIRECTLY_NOTE,
    BINDING_UNMATCHED_TEXT,
    DIRECTED_DONE,
    DIRECTIVE_UNAVAILABLE,
    HELD_STEM,
    LITE_NUDGE_NOTE,
    held_text,
)
from console.engine.client import EngineClient, turn_timeout
from console.engine_access import engine_client_for
from console.forms.timeline import DIRECTIVE_LABEL, DIRECTIVE_TOO_LONG
from console.models import EngineOperation
from console.rewrites import DIRECTIVE_TEXT, GUARDRAIL_TEXT, SESSION_LIMIT_TEXT
from console.views import story_pages
from console.views import timeline as timeline_views
from console.views.play import PlayStory
from console.views.timeline import DIRECTED_LINE, HELD_FOR_REVIEW_TEXT
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import snapshot, story_json
from tests.timeline.helpers import (
    HEAD,
    KEY,
    MARA,
    REV,
    REVISION_HEADER,
    STORY,
    TOBIN,
    body_of,
    calls,
    error_json,
    fixture,
    flash,
    html,
    path,
    regenerate_posted,
    url,
)

pytestmark = pytest.mark.django_db

SESSION = "01JM7RW0000000000000000004"
SESSION_PAGE = f"/stories/{STORY}/rewrites/{SESSION}/"
REGENERATE = f"/stories/{STORY}/regenerate"
GEORGE = "The knocker is George."
KEY_INPUT = re.compile(r'name="idempotency_key" value="([^"]+)"')
OPERATION = "01M5R6ZBB2M2GCN1ZFDX07WSXE"  # the regenerate fixture's first entry
FIXTURE_KEY = "01M5R6ZBAJHBTYVAEQAM3EGA2M"
MAIN_FLASH = (
    "Regenerated turn 5: variant 2 is active, previous variant 1 is kept as a dormant variant — "
    f"operation {OPERATION}, timeline revision 7, idempotency key {FIXTURE_KEY}"
)


def esc(text: str) -> str:
    return text.replace("'", "&#x27;")


def directed(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m7/directed_pending"))
    body["rewrite_session"]["story_id"] = STORY
    body.update(changes)
    return body


def session_read() -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("m7/rewrite_session_directed"))
    body["story_id"] = STORY
    return body


def sent(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", REGENERATE)


def sent_body(engine: FakeEngine) -> Any:
    (request,) = sent(engine)
    return body_of(request)


def regenerated(engine: FakeEngine, **changes: Any) -> None:
    engine.respond("POST", path("regenerate"), 200, json=fixture("regenerate", **changes))


def refused(engine: FakeEngine, status: int, code: str, **details: Any) -> None:
    engine.respond("POST", path("regenerate"), status, json=error_json(code, **details))


def page_key(page: str) -> str:
    found = KEY_INPUT.search(page)
    assert found is not None
    return found.group(1)


def direction_field(page: str) -> str:
    return page.split('class="field field-directive"')[1].split("</div>")[0]


@pytest.fixture
def framed(monkeypatch: pytest.MonkeyPatch, engine: FakeEngine) -> FakeEngine:
    """The timeline fake also behind #296's frame (the session page), with Harbour's story."""
    engine.respond("GET", "/stories/{story_id}", 200, json=story_json())
    engine.respond("GET", "/stories/{story_id}/rewrites/{session_id}", 200, json=session_read())

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = engine.transport
        return client

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    return engine


# --- The Direction box ----------------------------------------------------------------------------


def test_the_confirm_page_has_the_direction_box(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = html(operator_client, url("regenerate", snapshot=snapshot()))
    field = direction_field(page)
    assert DIRECTIVE_LABEL in field
    assert '<textarea name="directive"' in field
    assert "maxlength" not in field  # C10
    assert "Steer the new reply, e.g. “the knocker is George”." in field
    assert "If the direction conflicts with recorded history" in page
    assert 'name="resolution"' in page
    assert 'name="apply_directly"' in page
    # Direction, then the modes, then Parameters.
    assert page.index('name="directive"') < page.index("regenerate-modes")
    assert page.index("regenerate-modes") < page.index("regenerate-params")
    assert '<details class="regenerate-modes">' in page  # closed by default
    assert '<details class="regenerate-params">' in page
    assert esc(DIRECTED_LINE) not in page  # a first render, not a re-render
    assert "This field is required" not in page
    assert engine.requests == []


def test_a_hand_made_get_with_a_direction_shows_no_directed_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    page = html(operator_client, url("regenerate", snapshot=snapshot(), directive=GEORGE))
    assert esc(DIRECTED_LINE) not in page


def test_the_modes_details_opens_when_modes_were_posted(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.post(
        url("regenerate"), regenerate_posted(directive="x" * 2001, resolution="best_effort")
    )
    page = response.content.decode()
    assert '<details class="regenerate-modes" open>' in page
    assert '<details class="regenerate-params">' in page  # has_overrides() is unchanged


# --- The body -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "modes", [{"resolution": "synchronous"}, {"apply_directly": "on", "resolution": "best_effort"}]
)
def test_without_direction_the_body_is_unchanged(
    operator_client: Client, engine: FakeEngine, modes: dict[str, str]
) -> None:
    regenerated(engine)
    fields = {"length": "short", "temperature": "0.4", "output_viewpoint": f"character:{TOBIN}"}
    data = regenerate_posted(directive="   ", **fields, **modes)
    data["render_character_ids"] = MARA
    operator_client.post(url("regenerate"), data)
    body = sent_body(engine)
    # Built as test_operations.py::test_regenerate_sends_only_the_overrides_given builds it.
    assert body == {
        "turn": HEAD,
        "prompt_params": {"length": "short", "temperature": 0.4},
        "output_viewpoint": f"character:{TOBIN}",
        "render_character_ids": [MARA],
    }
    for member in ("directive", "preview", "resolution_mode"):
        assert member not in body


def test_direction_body_defaults(operator_client: Client, engine: FakeEngine) -> None:
    regenerated(engine)
    operator_client.post(
        url("regenerate"), regenerate_posted(directive=f"  {GEORGE}  ", resolution="synchronous")
    )
    body = sent_body(engine)
    assert body == {"turn": HEAD, "directive": {"text": GEORGE}}
    assert "preview" not in body
    assert "resolution_mode" not in body


def test_direction_body_with_modes(operator_client: Client, engine: FakeEngine) -> None:
    regenerated(engine)
    operator_client.post(
        url("regenerate"),
        regenerate_posted(directive=GEORGE, apply_directly="on", resolution="threshold_high"),
    )
    body = sent_body(engine)
    assert body["directive"] == {"text": GEORGE}
    assert body["preview"] == "apply_directly"
    assert body["resolution_mode"] == {"threshold": "high"}


def test_a_tampered_resolution_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(url("regenerate"), regenerate_posted(resolution="whatever"))
    assert response.status_code == 200
    assert "Select a valid choice" in response.content.decode()
    assert engine.requests == []


def test_expected_revision_and_timeout_unchanged(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    regenerated(engine)
    seen: list[float] = []
    original = EngineClient.regenerate

    def recording(self: EngineClient, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["timeout"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(EngineClient, "regenerate", recording)
    data = regenerate_posted(directive=GEORGE)
    operator_client.post(url("regenerate"), data)
    (request,) = sent(engine)
    assert request.headers[REVISION_HEADER] == str(REV)
    play = PlayStory.unsigned(data["snapshot"], STORY)
    assert play is not None
    assert seen == [turn_timeout(play.turn_deadline)]


def test_overlong_direction_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(url("regenerate"), regenerate_posted(directive="x" * 2001))
    assert response.status_code == 200
    page = response.content.decode()
    field = direction_field(page)
    assert DIRECTIVE_TOO_LONG in field
    assert "x" * 2001 in field  # the page is kept
    assert esc(DIRECTED_LINE) in page  # a re-render of a POST carrying a direction
    assert engine.requests == []


# --- A 200 ----------------------------------------------------------------------------------------


def test_steered_200_flash(operator_client: Client, engine: FakeEngine) -> None:
    regenerated(engine)
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    assert response.status_code == 302
    assert response["Location"].endswith("#turn-5")
    (line,) = flash(response)
    assert DIRECTED_DONE in line
    assert line.index(DIRECTED_DONE) < line.index("timeline revision 7")
    assert LITE_NUDGE_NOTE not in line
    assert APPLIED_DIRECTLY_NOTE not in line
    # Complement: without a direction the flash is main's exactly.
    plain = operator_client.post(url("regenerate"), regenerate_posted())
    assert flash(plain)[-1] == MAIN_FLASH  # flashes queue across POSTs in one client


def test_steered_apply_directly_note(operator_client: Client, engine: FakeEngine) -> None:
    regenerated(engine)
    response = operator_client.post(
        url("regenerate"), regenerate_posted(directive=GEORGE, apply_directly="on")
    )
    (line,) = flash(response)
    assert DIRECTED_DONE + APPLIED_DIRECTLY_NOTE in line


def test_lite_warning_note(operator_client: Client, engine: FakeEngine) -> None:
    regenerated(engine, warnings=[{"kind": "lite_no_reconciliation"}])
    response = operator_client.post(
        url("regenerate"), regenerate_posted(directive=GEORGE, resolution="threshold_high")
    )
    (line,) = flash(response)
    assert DIRECTED_DONE + LITE_NUDGE_NOTE in line
    assert line.index(LITE_NUDGE_NOTE) < line.index("timeline revision 7")


# --- A 202 ----------------------------------------------------------------------------------------


def test_held_202_lands_on_the_session(operator_client: Client, framed: FakeEngine) -> None:
    framed.respond(
        "POST", path("regenerate"), 202, json=directed(previous_variant={"turn": 2, "variant": 1})
    )
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    assert response.status_code == 303
    assert response["Location"] == SESSION_PAGE
    (message,) = list(get_messages(response.wsgi_request))
    assert message.level_tag == "warning"
    line = str(message)
    held = f"{HELD_STEM}; turn 2's variant 1 is still active"
    assert held_text({"turn": 2, "variant": 1}) == held
    assert line.startswith(held + ".")
    assert "timeline revision 20" in line
    assert "idempotency key 01JM7KY0000000000000000001" in line
    assert "operation" not in line  # nothing committed
    assert "Regenerated turn" not in line
    assert EngineOperation.objects.get(kind="regenerate").http_status == 202
    page = operator_client.get(response["Location"]).content.decode()
    assert "Directed regenerate" in page
    assert "Held reply (shown once applied)" in page
    assert "Bob lowers his eyes." in page
    assert "3 model calls to replay" in page  # E3's "N model call(s)", pluralised (E23)
    assert f'<a href="/stories/{STORY}/">← Back to the story</a>' in page
    assert "← All rewrites" in page
    assert "Regenerated turn" not in page
    assert [r for r in framed.requests if r.url.path.endswith("/apply")] == []


def test_back_to_the_story_only_for_a_directed_session(
    operator_client: Client, framed: FakeEngine
) -> None:
    other = session_read()
    other["kind"] = "rewrite"
    framed.respond("GET", "/stories/{story_id}/rewrites/{session_id}", 200, json=other)
    page = operator_client.get(SESSION_PAGE).content.decode()
    assert "← All rewrites" in page
    assert "Back to the story" not in page.split("session-links")[1].split("</p>")[0]


def test_held_flash_leaves_out_an_unnamed_variant(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", path("regenerate"), 202, json=directed(previous_variant={}))
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    (line,) = flash(response)
    assert line.startswith(HELD_STEM + ". — timeline revision 20")
    assert "still active" not in line


def test_binding_unmatched_warning(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        path("regenerate"),
        202,
        json=directed(warnings=["binding_unmatched", "scene_shifted"]),
    )
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    (line,) = flash(response)
    assert f"is still active.{BINDING_UNMATCHED_TEXT}. Warnings: Scene shifted." in line


def test_a_directed_answer_reaches_the_session_or_the_panel(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#292's test, updated: under the real urlconf (#297 registered) a ``DirectedPending`` is a
    303 to the session with the held flash; #43's panel shows only while ``stories:rewrite``
    doesn't resolve."""
    engine.respond("POST", path("regenerate"), 202, json=directed())
    linked = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    assert linked.status_code == 303
    assert linked["Location"] == SESSION_PAGE
    (line,) = flash(linked)
    assert line.startswith(held_text(directed()["previous_variant"]))
    assert HELD_FOR_REVIEW_TEXT not in line
    with monkeypatch.context() as patched:
        patched.setattr(timeline_views, "rewrite_session_url", lambda story_id, session_id: None)
        response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    assert response.status_code == 200
    page = response.content.decode()
    assert HELD_FOR_REVIEW_TEXT in page
    assert f"<code>{SESSION}</code>" in page
    assert f'href="/stories/{STORY}/"' in page
    assert len(flash(response)) == 1  # only the first POST's (queued); the panel adds none


# --- Refusals -------------------------------------------------------------------------------------

GUARDRAIL = {
    "violations": [
        {"name": "No real people", "policy_id": "pol-1", "locator": "body.directive.text"},
        {"name": "No gore", "policy_id": "pol-2", "locator": "body.directive.text"},
    ]
}


@pytest.mark.parametrize(
    ("code", "details", "text"),
    [
        (
            "guardrail_violation",
            GUARDRAIL,
            GUARDRAIL_TEXT.format(policies="No real people, No gore"),
        ),
        ("directive_unparseable", {}, DIRECTIVE_TEXT["directive_unparseable"]),
        ("directive_ambiguous", {}, DIRECTIVE_TEXT["directive_ambiguous"]),
    ],
)
def test_placed_directive_refusals(
    operator_client: Client, engine: FakeEngine, code: str, details: dict[str, Any], text: str
) -> None:
    refused(engine, 422, code, **details)
    response = operator_client.post(
        url("regenerate"), regenerate_posted(directive=GEORGE, temperature="0.4")
    )
    assert response.status_code == 200
    page = response.content.decode()
    field = direction_field(page)
    assert esc(text) in field
    assert GEORGE in field  # the typed direction kept
    assert 'value="0.4"' in page  # and the overrides
    assert esc(DIRECTED_LINE) in page
    assert page_key(page) != KEY  # a fresh key
    assert len(sent(engine)) == 1
    assert "Location" not in response


def test_guardrail_on_the_recorded_input_keeps_the_refused_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    refused(
        engine,
        422,
        "guardrail_violation",
        violations=[{"name": "No gore", "policy_id": "pol-2", "locator": "body.player_input.say"}],
    )
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    assert response.status_code == 200
    page = response.content.decode()
    assert "Regenerate refused" in page
    assert 'class="field field-directive"' not in page  # nothing placed on Direction


def test_module_off_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refused(engine, 409, "module_off")
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    page = response.content.decode()
    assert "Regenerate refused" in page
    assert esc(DIRECTIVE_UNAVAILABLE) in page


def test_module_off_naming_a_known_module(operator_client: Client, engine: FakeEngine) -> None:
    refused(engine, 409, "module_off", module="lite_rewrites")
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    page = response.content.decode()
    assert "<code>lite_rewrites</code> is off" in page or "`lite_rewrites` is off" in page
    assert f'href="/stories/{STORY}/config/">Config</a>' in page
    assert esc(DIRECTIVE_UNAVAILABLE) not in page


def test_rewrite_session_limit_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refused(engine, 409, "rewrite_session_limit", limit=3)
    response = operator_client.post(url("regenerate"), regenerate_posted(directive=GEORGE))
    page = response.content.decode()
    assert esc(SESSION_LIMIT_TEXT.format(limit="3")) in page
    assert f'<a href="/stories/{STORY}/rewrites/">Rewrites</a>' in page
