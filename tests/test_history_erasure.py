"""Erasure in the transcript and held stories (#87): the "erased" badge on redacted items,
``[erased]`` spans marked without ever trusting item text as HTML, erasure markers with
omniscient audit, held stories in the list and on every story page, and the health reason."""

import copy
from collections.abc import Iterator
from typing import Any

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine.models import HealthResult
from console.engine_access import engine_client_for
from console.engine_probe import classify
from console.held import held_text
from console.templatetags.erasure import mark_erased
from console.views import history as history_views
from console.views import play as play_views
from console.views import stories as story_views
from console.views import story_settings as settings_views
from console.views.status import erasure_line
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.history.helpers import answer, history_json, page
from tests.play.helpers import STORY, answer_page, story_json
from tests.ratings.helpers import error
from tests.stories.helpers import summary

pytestmark = pytest.mark.django_db

AUDIT = "viewpoint=omniscient&include_audit=1"
SUBJECT = "01JP0000000000000000PERSA1"
HELD = "01JB2Z8Y5Q3W6K9M1N4P7HELD1"
HELD_NAMED = "01JB2Z8Y5Q3W6K9M1N4P7HELD2"
ACTIVE = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
HTMX = {"HX-Request": "true"}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine behind the history, play, stories and settings views."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (history_views, play_views, story_views, settings_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def item(text: str, **changes: Any) -> dict[str, Any]:
    """A turn-1 ``do`` item of Mara's."""
    body: dict[str, Any] = copy.deepcopy(history_json()["items"][0])
    body.update({"channel": "do", "kind": "beat", "text": text, **changes})
    return body


def marker(text: str, turn: int | None = None) -> dict[str, Any]:
    return {
        "turn": turn,
        "variant": None,
        "beat": None,
        "kind": "erasure_marker",
        "actor_character_id": None,
        "render_character_ids": None,
        "channel": "audit",
        "text": text,
        "operation_id": "01M5R5XWJH0Y9SEDSFMZEG2SFF",
        "message_id": "01M5R5XWK4AD648CN1TXWRXFR4",
        "origin": "generated",
        "true_ages": {},
    }


def with_items(*items: dict[str, Any], audit: list[dict[str, Any]] | None = None) -> Any:
    body = history_json()
    body["items"] = [*body["items"], *items]
    if audit is not None:
        body["audit"] = [*body["audit"], *audit]
    return body


# --- the transcript -------------------------------------------------------------------------------


def test_a_redacted_item_has_the_erased_badge(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=with_items(item("[erased] waved.", redacted=True), item("Plain.")))
    html = page(operator_client)
    rows = [r for r in html.split('<tr class="item') if "waved." in r or "Plain." in r]
    assert len(rows) == 2
    assert '<span class="badge-erased">erased</span>' in rows[0]
    assert "badge-erased" not in rows[1]  # complement: not on an unredacted item


def test_erased_spans_are_marked(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=with_items(item("[erased] met [erased] at the pier.", redacted=True)))
    html = page(operator_client)
    assert (
        '<mark class="erased">[erased]</mark> met <mark class="erased">[erased]</mark> at the pier.'
        in html
    )


def test_item_text_stays_escaped_through_the_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, history=with_items(item('<script>alert("x")</script> [erased]', redacted=True)))
    html = page(operator_client)
    assert "<script>alert" not in html
    assert (
        '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; <mark class="erased">[erased]</mark>'
        in html
    )


@pytest.mark.parametrize("variant", ["[ERASED]", "[erased ]", "[Erased]", "erased"])
def test_other_spellings_are_not_marked(variant: str) -> None:
    assert mark_erased(f"a {variant} b") == f"a {variant} b"
    assert "<mark" not in mark_erased(f"a {variant} b")


def test_the_filter_escapes_and_marks() -> None:
    assert (
        mark_erased("<b>[erased]</b>") == '&lt;b&gt;<mark class="erased">[erased]</mark>&lt;/b&gt;'
    )
    assert mark_erased(None) == ""


def test_summary_segments_are_marked(operator_client: Client, engine: FakeEngine) -> None:
    body = history_json()
    body["summary"][0]["text"] = "So far [erased] <i>left</i>."
    answer(engine, history=body)
    html = page(operator_client, "include_summary=1")
    assert 'So far <mark class="erased">[erased]</mark> &lt;i&gt;left&lt;/i&gt;.' in html


def test_a_marker_shows_with_omniscient_audit_in_turn_zero(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(
        engine,
        history=with_items(audit=[marker(f"[erasure marker: {SUBJECT}, story]")]),
    )
    html = page(operator_client, AUDIT)
    turn0 = html[html.index('id="turn-0-0"') : html.index('id="turn-1-1"')]
    assert (
        f'<p class="erasure-marker">Erasure marker: <code>{SUBJECT}</code>, story scope</p>'
        in turn0
    )


def test_a_marker_goes_in_its_own_turn(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=with_items(audit=[marker("[erasure marker: x1, catalog]", turn=2)]))
    html = page(operator_client, AUDIT)
    turn2 = html[html.index('id="turn-2-1"') : html.index('id="turn-3-1"')]
    assert "Erasure marker: <code>x1</code>, catalog scope" in turn2
    assert 'id="turn-0-0"' not in html


def test_a_marker_with_no_turn_is_not_pinned_to_a_newer_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = with_items(audit=[marker("[erasure marker: x1, story]")])
    body["next_before_turn"] = 1  # older turns exist: turn 0 is not on this page
    answer(engine, history=body)
    html = page(operator_client, AUDIT)
    assert "Erasure marker" not in html


def test_an_unknown_scope_shows_raw(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=with_items(audit=[marker("[erasure marker: x1, galaxy]", turn=1)]))
    assert "Erasure marker: <code>x1</code>, galaxy scope" in page(operator_client, AUDIT)


def test_an_unparsable_marker_renders_escaped_raw(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, history=with_items(audit=[marker("<b>marker</b> [erased]", turn=1)]))
    html = page(operator_client, AUDIT)
    assert (
        '<pre class="audit-raw">&lt;b&gt;marker&lt;/b&gt; '
        '<mark class="erased">[erased]</mark></pre>' in html
    )
    assert "Erasure marker:" not in html


def test_no_marker_without_audit(operator_client: Client, engine: FakeEngine) -> None:
    # Complement: an Engine answer carrying no audit array falls back to audit-channel items
    # in ``items``; a marker there still shows only with omniscient audit.
    body = with_items(marker("[erasure marker: x1, story]", turn=1))
    body.pop("audit")
    answer(engine, history=body)
    assert "Erasure marker" not in page(operator_client)
    assert "Erasure marker" not in page(operator_client, "viewpoint=omniscient")


def test_the_play_transcript_marks_erased_text(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine, history=with_items(item("[erased] <waved>", redacted=True)))
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert '<mark class="erased">[erased]</mark> &lt;waved&gt;' in html
    assert '<span class="badge-erased">erased</span>' in html


# --- held stories ---------------------------------------------------------------------------------


def serve_list(engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/stories",
        200,
        json={
            "items": [
                summary(ACTIVE, "Harbour", "active"),
                summary(HELD, None, "blocked_by_erasure"),
                summary(HELD_NAMED, "Old pier", "blocked_by_erasure"),
            ]
        },
    )
    engine.respond("GET", "/stories/{story_id}", 200, json=story_json())


def row(html: str, story_id: str) -> str:
    start = html.index(f"<code>{story_id}</code>")
    return html[html.rindex("<tr>", 0, start) : html.index("</tr>", start)]


def test_held_stories_in_the_list(operator_client: Client, engine: FakeEngine) -> None:
    serve_list(engine)
    html = operator_client.get("/stories/").content.decode()
    unnamed, named = row(html, HELD), row(html, HELD_NAMED)
    assert f'<a href="/stories/{HELD}/">(held — erasure pending)</a>' in unnamed
    assert "badge-held" not in unnamed
    assert (
        f'<a href="/stories/{HELD_NAMED}/">Old pier</a> <span class="badge-held">held</span>'
        in (named)
    )
    for held in (unnamed, named):
        assert '<td class="status">Held (erasure pending)</td>' in held
        assert "Archive" not in held  # Unarchive included
        assert "held=1" in held
        assert "Delete" in held
        assert "<td>—</td>" in held  # the cast back-fill is skipped
    # Only the active story's GET /stories/{id} back-fill was made.
    reads = [r.url.path for r in engine.requests if r.url.path.startswith("/stories/")]
    assert reads == [f"/stories/{ACTIVE}"]


def test_the_status_filter_offers_held(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories", 200, json={"items": []})
    html = operator_client.get("/stories/?status=blocked_by_erasure").content.decode()
    assert '<option value="blocked_by_erasure" selected>Held</option>' in html
    (listing,) = [r for r in engine.requests if r.url.path == "/stories"]
    assert listing.url.params["status"] == "blocked_by_erasure"
    assert "No held stories." in html


def held_answer(engine: FakeEngine, path: str, *, method: str = "GET", retry: str = "30") -> None:
    headers = {"Retry-After": retry} if retry else None
    engine.respond(
        method, path, 503, json=error("erasure_pending", "held", story_id=HELD), headers=headers
    )


@pytest.mark.parametrize("suffix", ["", "history/", "settings/"])
def test_every_story_page_renders_the_held_message(
    operator_client: Client, engine: FakeEngine, suffix: str
) -> None:
    held_answer(engine, "/stories/{story_id}")
    held_answer(engine, "/stories/{story_id}/settings")
    response = operator_client.get(f"/stories/{HELD}/{suffix}")
    assert response.status_code == 503
    assert response["Retry-After"] == "30"
    html = response.content.decode()
    assert "Held until the erasure ledger is re-applied — retry in 30 s" in html
    assert f"Story <code>{HELD}</code>" in html
    assert f'<a class="retry" href="/stories/{HELD}/{suffix}">Retry</a>' in html
    assert f"/stories/{HELD}/delete/?held=1" in html
    assert "Engine error 503" not in html
    assert "hx-trigger" not in html.split("<main")[-1]  # no auto-refresh


def test_the_held_message_drops_an_unusable_retry_after(
    operator_client: Client, engine: FakeEngine
) -> None:
    held_answer(engine, "/stories/{story_id}", retry="")
    response = operator_client.get(f"/stories/{HELD}/")
    assert "Held until the erasure ledger is re-applied</p>" in response.content.decode()
    assert "Retry-After" not in response


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("12", "Held until the erasure ledger is re-applied — retry in 12 s"),
        ("soon", "Held until the erasure ledger is re-applied"),
        ("1.5", "Held until the erasure ledger is re-applied"),
        (None, "Held until the erasure ledger is re-applied"),
    ],
)
def test_held_text(value: str | None, shown: str) -> None:
    assert held_text(value) == shown


def test_an_htmx_request_is_redirected_to_the_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    held_answer(engine, "/stories/{story_id}/history")
    held_answer(engine, "/stories/{story_id}")
    response = operator_client.get(f"/stories/{HELD}/history/", headers=HTMX)
    assert response.status_code == 200
    assert response["HX-Redirect"] == f"/stories/{HELD}/"


def test_another_503_is_still_the_error_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}", 503, json=error("database_unavailable", "down"))
    html = operator_client.get(f"/stories/{HELD}/").content.decode()
    assert "Engine error 503 database_unavailable" in html
    assert "Held until" not in html


def test_the_held_delete_confirm_uses_the_id(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"/stories/{HELD}/delete/?held=1&name=x").content.decode()
    assert f"Delete held story <em>{HELD}</em>?" in html
    assert 'name="held" value="1"' in html
    assert engine.requests == []


def test_a_held_delete_refusal_renders_the_held_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    held_answer(engine, "/stories/{story_id}", method="DELETE")
    response = operator_client.post(f"/stories/{HELD}/delete/", {"held": "1"})
    assert response.status_code == 503
    assert "Held until the erasure ledger is re-applied — retry in 30 s" in (
        response.content.decode()
    )


# --- health ---------------------------------------------------------------------------------------


def health(status: str, http_status: int = 200, **checks: Any) -> HealthResult:
    raw = {"status": status, "checks": {"database": "ok", **checks}}
    return HealthResult(http_status, None, raw, 5)


def test_the_health_reason_names_pending_erasures() -> None:
    degraded = classify(health("degraded", erasure_pending=2, oldest_overdue_job_s=400.0))
    assert degraded.reason == "job overdue 400 s, erasure pending: 2"  # last
    unhealthy = classify(health("unavailable", 503, database="error", erasure_pending=1))
    assert unhealthy.reason == "database error, erasure pending: 1"  # whatever the state
    assert classify(health("degraded", erasure_pending=0)).reason == "status degraded"


def test_the_status_page_line() -> None:
    assert erasure_line({"erasure_pending": 3}) == ("Erasure pending: 3", "/erasure/")
    assert erasure_line({"erasure_pending": 0}) == ("Erasure pending: none", "/erasure/")
    assert erasure_line({"erasure_pending": None}) == ("Erasure pending: none", "/erasure/")
    assert erasure_line(None) is None
