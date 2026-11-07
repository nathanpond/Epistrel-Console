"""The Play page's Direct tab (#300; FR-DIRECT-1,2,6, FR-LITE-8): the tab, its form and body, the
outcome of each disposition, the refusals and the omniscient viewpoint, against the fake Engine."""

import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client
from django.urls import reverse

from console.directives import (
    APPLIED_TEXT,
    AUTHOR_FACT_TEXT,
    DIRECT_VIEWPOINT_LINE,
    NUDGE_QUEUED_TEXT,
    ROUTED_TEXT,
    STANDING_HINT,
    STEER_QUEUED_TEXT,
)
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.directives import TEXT_REQUIRED, TEXT_TOO_LONG
from console.ingest import INGEST_ARCHIVED_NOTE
from console.inspect import unavailable_for
from console.operation_log import REPLAY_HEADER
from console.rewrites import AUTHOR_FACT_LIMIT_TEXT, DIRECTIVE_TEXT, STEER_BUDGET_TEXT
from console.story_pause import TURN_NOTE
from console.views import directives as direct_views
from console.wording import busy_notice
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.golden import normalise_html
from tests.play.helpers import (
    HTMX,
    KEY,
    answer_page,
    calls,
    error_json,
    history_json,
    snapshot,
)
from tests.play.helpers import STORY as HARBOUR

STORY = "01JM7ST0000000000000000001"
ALICE = "01JM7CH0000000000000000002"
ROUTED_SESSION = "01JM7RW0000000000000000007"
DIRECTIVES = "/stories/{story_id}/directives"
PATH = f"/stories/{STORY}/directives"
GOLDEN = Path(__file__).parent / "golden"
KEY_INPUT = re.compile(r'name="idempotency_key" value="([^"]*)"')


def m7(name: str) -> dict[str, Any]:
    found: dict[str, Any] = engine_fixture(f"m7/{name}")
    return found


def m7_story(**changes: Any) -> dict[str, Any]:
    return {**m7("story"), **changes}


@pytest.fixture
def fake(engine: FakeEngine, monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """The play fixture's fake, also behind the Direct view's client."""

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = engine.transport
        return client

    monkeypatch.setattr(direct_views, "engine_client_for", client_with_fake)
    return engine


def direct_data(**fields: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "snapshot": snapshot(story=m7_story()),
        "output_viewpoint": "default",
        "transcript_player_view": "",
        "text": "Make the next scene tense.",
        "kind": "",
        "resolution": "synchronous",
    }
    data.update(fields)
    return data


def post(client: Client, *, htmx: bool = True, **fields: Any) -> str:
    response = client.post(
        reverse("stories:direct", args=[STORY]), direct_data(**fields), headers=HTMX if htmx else {}
    )
    assert response.status_code == 200
    return response.content.decode()


def text(html: str) -> str:
    return html_lib.unescape(html)


def plain(html: str) -> str:
    """The text with its tags dropped (``<code>`` spans included)."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", html))


def posts(fake: FakeEngine) -> list[Any]:
    return [r for r in fake.requests if r.method == "POST"]


def sent(fake: FakeEngine) -> Any:
    (request,) = calls(fake, "POST", PATH)
    return request


def sent_body(fake: FakeEngine) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(sent(fake).content)
    return body


def key_in(html: str) -> str:
    keys = KEY_INPUT.findall(html)
    assert keys, "no idempotency key"
    return str(keys[0])


def text_errors(html: str) -> str:
    """The Text field's block (its errors)."""
    return html.split('class="field field-text"')[1].split('class="field field-kind"')[0]


def above_form(html: str) -> str:
    return html.split('<div id="direct">')[1].split('<form method="post"')[0]


def textarea(html: str) -> str:
    return html.split('name="text"')[1].split(">", 1)[1].split("</textarea>")[0]


def answered(fake: FakeEngine, name: str, status: int = 200, **changes: Any) -> dict[str, Any]:
    body = {**m7(name), **changes}
    fake.respond("POST", DIRECTIVES, status, json=body)
    return body


def tab(client: Client, name: str, **params: Any) -> str:
    query = {"tab": name, "snapshot": snapshot(story=m7_story()), "output_viewpoint": "default"}
    query.update(params)
    response = client.get(f"/stories/{STORY}/", query, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


# --- the tab ------------------------------------------------------------------------------------

_ULID_VALUE = re.compile(r'value="[0-9A-HJKMNP-TV-Z]{26}"')
_SNAPSHOT = re.compile(r'(name="snapshot" value=")[^"]*(")')


def _region(html: str) -> str:
    """One tab's own partial (after the tab nav), with ids and the signed snapshot fixed."""
    after = html.split("</nav>", 1)[1]
    after = _SNAPSHOT.sub(r"\g<1>SNAPSHOT\g<2>", normalise_html(after))
    return _ULID_VALUE.sub('value="ULID"', after)


def test_direct_tab_renders(operator_client: Client, fake: FakeEngine) -> None:
    answer_page(fake, story=m7_story(), history=history_json(with_turn=False))
    page = operator_client.get(f"/stories/{STORY}/", {"tab": "direct"}).content.decode()
    nav = page.split('<nav class="tabs">')[1].split("</nav>")[0]
    assert [m for m in re.findall(r">(Turn|Ingest|Direct)</a>", nav)] == [
        "Turn",
        "Ingest",
        "Direct",
    ]
    assert f'href="/stories/{STORY}/?tab=direct"' in nav
    assert f'hx-get="/stories/{STORY}/?tab=direct"' in nav
    assert 'aria-current="page">Direct</a>' in nav
    assert nav.count("aria-current") == 1
    shown = text(page)
    assert DIRECT_VIEWPOINT_LINE in shown
    assert f'action="/stories/{STORY}/direct/"' in page
    assert '<textarea name="text"' in page
    assert "maxlength" not in page.split('<div id="direct">')[1].split("</form>")[0]
    assert "<summary>Advanced</summary>" in page
    assert "If this changes the past (a rewrite session opens)" in page
    assert 'name="say"' not in page  # the turn form isn't shown


@pytest.mark.parametrize(
    ("name", "golden"), [("turn", "turn"), ("ingest", "ingest"), ("bogus", "turn")]
)
def test_turn_and_ingest_tabs_render_as_on_main(
    operator_client: Client, fake: FakeEngine, name: str, golden: str
) -> None:
    """E24: each tab's own partial against main's capture (only the tab nav gained a link)."""
    params = {"tab": name, "snapshot": snapshot(), "output_viewpoint": "default"}
    html = operator_client.get(f"/stories/{HARBOUR}/", params, headers=HTMX).content.decode()
    expected = (GOLDEN / f"{golden}_tab.html").read_text()
    assert _region(html) == _region(expected)
    nav = html.split('<nav class="tabs">')[1].split("</nav>")[0]
    assert 'aria-current="page">Direct' not in nav


def test_htmx_tab_swap_answers_the_form_area_alone(
    operator_client: Client, fake: FakeEngine
) -> None:
    html = tab(operator_client, "direct", output_viewpoint=f"character:{ALICE}")
    assert html.lstrip().startswith('<div id="play-form-area">')
    assert '<div id="direct">' in html
    assert '<div id="transcript"' not in html
    assert f'name="output_viewpoint" value="character:{ALICE}"' in html
    assert fake.requests == []  # no Engine read


def test_lite_kind_labels(operator_client: Client, fake: FakeEngine) -> None:
    lite = tab(operator_client, "direct", snapshot=snapshot(story=m7_story(), pipeline="lite"))
    assert '<option value="ephemeral">Nudge the next reply only</option>' in lite
    assert '<option value="durable">Lasting author fact</option>' in lite
    assert '<option value="" selected>Let the Engine decide</option>' in lite
    structured = tab(operator_client, "direct")
    assert '<option value="ephemeral">Steer the next reply only</option>' in structured
    assert '<option value="durable">Lasting change</option>' in structured
    assert "Nudge" not in structured
    assert "Lasting author fact" not in structured


# --- the body -----------------------------------------------------------------------------------


def test_body_without_kind(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer")
    post(operator_client)
    request = sent(fake)
    assert sent_body(fake) == {
        "text": "Make the next scene tense.",
        "preview": "preview_first",
        "resolution_mode": "synchronous",
    }
    assert dict(request.url.params) == {"viewpoint": "omniscient"}
    assert request.headers["Idempotency-Key"] == KEY
    assert "kind" not in sent_body(fake)
    assert "X-Epistrel-Expected-Revision" not in request.headers


def test_body_with_kind_and_modes(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_applied")
    post(operator_client, text="  It starts raining.  ", kind="durable", resolution="threshold_low")
    assert sent_body(fake) == {
        "text": "It starts raining.",
        "kind": "durable",
        "preview": "preview_first",
        "resolution_mode": {"threshold": "low"},
    }


def test_the_limit_is_measured_after_stripping(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer")
    post(operator_client, text="   " + "x" * 4000 + "   ")
    assert sent_body(fake)["text"] == "x" * 4000


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"text": "   "}, TEXT_REQUIRED),
        ({"text": "x" * 4001}, TEXT_TOO_LONG),
        ({"kind": "forever"}, "Select a valid choice"),
    ],
)
def test_invalid_form_sends_nothing(
    operator_client: Client, fake: FakeEngine, fields: dict[str, Any], error: str
) -> None:
    html = post(operator_client, **fields)
    assert error in text(html)
    assert calls(fake, "POST", PATH) == []
    assert posts(fake) == []


# --- outcomes -----------------------------------------------------------------------------------


def test_steer_outcome(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer")
    html = post(operator_client)
    shown = text(html)
    assert "Read as: Steer the next reply only" in shown
    assert f"{STEER_QUEUED_TEXT}: “Make this scene tense”" in shown
    assert textarea(html) == "\n"  # a fresh form: the text is empty
    assert key_in(html) != KEY
    assert "Applied at" not in shown
    assert "Open the rewrite session" not in shown
    more = html.split("<summary>More</summary>")[1]
    assert "01JM7DV0000000000000000001" in more  # directive_id
    assert "01JM7KY0000000000000000001" in more  # the idempotency key


def test_lite_nudge_outcome(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer")
    html = post(operator_client, snapshot=snapshot(story=m7_story(), pipeline="lite"))
    shown = text(html)
    assert "Read as: Nudge the next reply only" in shown
    assert f"{NUDGE_QUEUED_TEXT}: “Make this scene tense”" in shown


def test_applied_outcome(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_applied")
    html = post(operator_client, text="It starts raining.", kind="durable")
    shown = text(html)
    assert "Read as: Lasting change" in shown
    assert APPLIED_TEXT.format(position="4⁺1", revision="21") in shown
    assert "<li>1 fact</li>" in html
    assert "<li>1 State change</li>" in html
    more = html.split("<summary>More</summary>")[1].split("</details>")[0]
    assert "01JM7FA0000000000000000004" in more
    assert "01JM7SC0000000000000000001" in more


def test_applied_outcome_lines_and_null_position(operator_client: Client, fake: FakeEngine) -> None:
    applied = {
        "facts": ["01JM7FA0000000000000000004", "01JM7FA0000000000000000006"],
        "state_changes": [],
        "beliefs": ["01JM7BL0000000000000000001"],
        "clock": ["01JM7CK0000000000000000001"],
        "scheduled_events": ["01JM7EV0000000000000000001"],
        "outbox_ids": ["01JM7OB0000000000000000001"],
        "author_fact": None,
    }
    answered(fake, "directive_applied", applied=applied, position=None)
    html = post(operator_client)
    shown = text(html)
    assert APPLIED_TEXT.format(position="—", revision="21") in shown
    items = re.findall(r"<li>(.*?)</li>", html.split('class="direct-applied"')[1].split("</ul>")[0])
    assert items[:4] == ["2 facts", "Clock advanced", "1 belief", "1 scheduled event"]
    assert items[4].startswith("1 external effect ")
    assert "01JM7OB0000000000000000001" in items[4]
    assert "State change" not in shown


def test_lite_author_fact_outcome(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_lite")
    html = post(
        operator_client,
        snapshot=snapshot(story=m7_story(), pipeline="lite"),
        text="It has been raining since dawn.",
        kind="durable",
    )
    shown = text(html)
    assert "Read as: Lasting author fact" in shown
    assert AUTHOR_FACT_TEXT.format(text="It has been raining since dawn.") in shown
    assert APPLIED_TEXT.format(position="4⁺1", revision="21") in shown
    more = html.split("<summary>More</summary>")[1].split("</details>")[0]
    assert "01JM7FA0000000000000000005" in more


def test_lite_clock_only_author_fact_has_no_fact_line(
    operator_client: Client, fake: FakeEngine
) -> None:
    applied = {
        "author_fact": {"id": "01JM7FA0000000000000000005", "position": "4⁺1", "text": ""},
        "clock": ["01JM7CK0000000000000000001"],
    }
    answered(fake, "directive_lite", applied=applied)
    html = post(operator_client, snapshot=snapshot(story=m7_story(), pipeline="lite"))
    assert "Lasting author fact recorded" not in text(html)
    assert "<li>Clock advanced</li>" in html
    assert "01JM7FA0000000000000000005" in html.split("<summary>More</summary>")[1]


def test_routed_outcome_links_the_session(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_routed", 202)
    html = post(operator_client, text="Bob was a clerk at the harbour office")
    shown = text(html)
    assert ROUTED_TEXT in shown
    url = reverse("stories:rewrite", args=[STORY, ROUTED_SESSION])
    assert f'<a href="{url}" class="direct-session">Open the rewrite session</a>' in html
    session = html.split('class="rewrite-session"')[1].split(
        f'<form method="post" action="/stories/{STORY}/direct/"'
    )[0]
    assert "Ready to apply" in session  # E3's state text
    assert "Bob was a harbour clerk" in text(session)
    assert "<form" not in session  # no Apply form, no answers form
    assert [r.url.path for r in posts(fake)] == [PATH]
    assert not any(r.url.path.endswith("/apply") for r in fake.requests)


def test_routed_without_a_session(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_routed", 202, rewrite_session=None)
    html = post(operator_client)
    assert ROUTED_TEXT in text(html)
    assert "Open the rewrite session" not in html
    assert 'class="rewrite-session"' not in html
    assert "routed_to_rewrite" in html.split("<summary>More</summary>")[1]


def test_standing_hint_links_settings(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer", hints=["use_custom_instructions", "new_hint"])
    html = post(operator_client)
    shown = text(html)
    assert STANDING_HINT in shown
    assert f'<a href="{reverse("stories:settings", args=[STORY])}">Settings</a>' in html
    assert '<p class="direct-hint" title="new_hint">New hint</p>' in html


def test_unknown_disposition_is_not_applied(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_applied", disposition="deferred")
    html = post(operator_client)
    assert '<p class="direct-headline" title="deferred">Deferred</p>' in html
    more = html.split("<summary>More</summary>")[1].split("</details>")[0]
    assert "&quot;disposition&quot;: &quot;deferred&quot;" in more
    assert "Applied at" not in text(html)
    assert "State change" not in text(html)


def test_unknown_classification_is_humanised(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer", classification="standing_order")
    html = post(operator_client)
    assert '<p class="read-as" title="standing_order">Read as: Standing order</p>' in html


def test_a_replayed_key_says_so(operator_client: Client, fake: FakeEngine) -> None:
    fake.respond(
        "POST", DIRECTIVES, 200, json=m7("directive_steer"), headers={REPLAY_HEADER: "true"}
    )
    html = post(operator_client)
    assert f"{STEER_QUEUED_TEXT}: “Make this scene tense” (replayed)" in text(html)


# --- refusals -----------------------------------------------------------------------------------

GUARDRAIL = {"violations": [{"name": "No gore", "policy_id": "p1", "locator": "body.text"}]}
REFUSALS = [
    (422, "directive_no_changes", {}, DIRECTIVE_TEXT["directive_no_changes"], True),
    (422, "directive_unparseable", {}, DIRECTIVE_TEXT["directive_unparseable"], True),
    (
        422,
        "directive_split_required",
        {"now": "x"},
        DIRECTIVE_TEXT["directive_split_required"],
        True,
    ),
    (
        422,
        "guardrail_violation",
        GUARDRAIL,
        "That text breaks the story's input guardrails (No gore) — nothing was written",
        True,
    ),
    (
        422,
        "directive_ambiguous",
        {"candidates": ["Bobby Ruiz", 3, "Bobby Tan"]},
        DIRECTIVE_TEXT["directive_ambiguous"] + " (could be: Bobby Ruiz, Bobby Tan)",
        True,
    ),
    (409, "steer_budget_exceeded", {"tokens": 510, "max": 500}, STEER_BUDGET_TEXT, False),
    (
        409,
        "author_fact_limit",
        {"active": 20, "max": 20},
        AUTHOR_FACT_LIMIT_TEXT.format(max="20"),
        False,
    ),
    (
        409,
        "module_off",
        {"module": "lite_rewrites"},
        unavailable_for("lite_rewrites", None).reason,
        False,
    ),
    (
        422,
        "clock_backward",
        {"current": 600, "attempted": 300},
        "That would move the clock backward",
        False,
    ),
    (
        422,
        "state_constraint_violation",
        {"constraint": "min", "attempted": -1, "current": 3},
        "That breaks the State's",
        False,
    ),
    (423, "story_busy", {"reason": "in_flight"}, busy_notice({"reason": "in_flight"}), False),
]


@pytest.mark.parametrize(("status", "code", "details", "expected", "on_text"), REFUSALS)
def test_refusals(
    operator_client: Client,
    fake: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    expected: str,
    on_text: bool,
) -> None:
    fake.respond("POST", DIRECTIVES, status, json=error_json(code, **details))
    html = post(operator_client, text="Bobby believes he graduated.", kind="durable")
    shown_where = plain(text_errors(html)) if on_text else plain(above_form(html))
    assert expected.replace("`", "") in shown_where
    if not on_text:
        assert 'class="errorlist direct-refusal"' in html
    assert textarea(html) == "\nBobby believes he graduated."
    assert '<option value="durable" selected>' in html
    assert key_in(html) != KEY
    assert len(calls(fake, "POST", PATH)) == 1


def test_a_lite_rewrites_refusal_links_config(operator_client: Client, fake: FakeEngine) -> None:
    fake.respond("POST", DIRECTIVES, 409, json=error_json("module_off", module="lite_rewrites"))
    html = post(operator_client)
    assert f'<a href="{reverse("stories:config", args=[STORY])}">Config</a>' in html


def test_a_body_text_validation_error_goes_on_text(
    operator_client: Client, fake: FakeEngine
) -> None:
    errors = [{"field": "body.text", "message": "String should have at most 4000 characters"}]
    fake.respond("POST", DIRECTIVES, 422, json=error_json("validation_error", errors=errors))
    html = post(operator_client)
    assert "at most 4000 characters" in text(text_errors(html))
    assert key_in(html) != KEY


def test_an_unknown_refusal_is_the_panel(operator_client: Client, fake: FakeEngine) -> None:
    fake.respond("POST", DIRECTIVES, 409, json=error_json("something_new"))
    html = post(operator_client)
    assert 'class="engine-error' in above_form(html)
    assert key_in(html) != KEY


def test_an_unknown_outcome_keeps_the_key(operator_client: Client, fake: FakeEngine) -> None:
    fake.fail("POST", DIRECTIVES, httpx.ConnectError("down"))
    html = post(operator_client)
    assert 'class="engine-error' in above_form(html)
    assert key_in(html) == KEY
    assert textarea(html) == "\nMake the next scene tense."


# --- disabled, plain POST, viewpoint ------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "note"), [("archived", INGEST_ARCHIVED_NOTE), ("paused", TURN_NOTE)]
)
def test_archived_and_paused_disable_the_form(
    operator_client: Client, fake: FakeEngine, status: str, note: str
) -> None:
    html = tab(operator_client, "direct", snapshot=snapshot(story=m7_story(status=status)))
    direct = html.split('<div id="direct">')[1]
    assert "<fieldset disabled>" in direct
    assert note in text(direct)
    assert posts(fake) == []


def test_a_post_from_an_archived_page_is_still_sent(
    operator_client: Client, fake: FakeEngine
) -> None:
    fake.respond("POST", DIRECTIVES, 409, json=error_json("story_archived"))
    html = post(operator_client, snapshot=snapshot(story=m7_story(status="archived")))
    assert len(calls(fake, "POST", PATH)) == 1
    assert 'class="errorlist direct-refusal"' in html


def test_plain_post_renders_the_whole_page(operator_client: Client, fake: FakeEngine) -> None:
    answer_page(fake, story=m7_story(), history=history_json(with_turn=False))
    answered(fake, "directive_applied")
    html = post(operator_client, htmx=False)
    assert '<div id="transcript"' in html
    assert 'aria-current="page">Direct</a>' in html
    assert APPLIED_TEXT.format(position="4⁺1", revision="21") in text(html)
    # The turn inspector is unchanged: a directive row has no turn report (main's _inspectable).
    assert "Select a turn to see its report." in html
    assert 'class="messages"' not in html  # no flash


def test_character_viewpoint_never_sent(operator_client: Client, fake: FakeEngine) -> None:
    answered(fake, "directive_steer")
    html = post(operator_client, output_viewpoint=f"character:{ALICE}")
    request = sent(fake)
    assert dict(request.url.params) == {"viewpoint": "omniscient"}
    assert "output_viewpoint" not in sent_body(fake)
    assert "actor_character_id" not in str(request.url)
    assert f'name="output_viewpoint" value="character:{ALICE}"' in html
