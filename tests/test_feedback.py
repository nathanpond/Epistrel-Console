"""Rate, re-rate and retract feedback on a rendered message (#83): the gate on #48's play view and
#52's history, the widget's form, save, retract, history and context, and the #44 records."""

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core import signing
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineUnreachable
from console.engine_access import engine_client_for
from console.feedback import PREFILL_SALT
from console.models import ActingPrincipal, EngineOperation
from console.views import feedback as feedback_views
from console.views import history as history_views
from console.views import play as play_views
from console.views import stories as story_views
from tests.fakes.engine import FakeEngine
from tests.fakes.feedback import CONTEXT, FEEDBACK, OPERATION, SETTINGS, FeedbackStore
from tests.fakes.feedback import settings_json as feedback_settings
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.history import helpers as history_helpers
from tests.play.helpers import MARA, STORY, TOBIN, answer_page, history_json, story_json

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}
REPLY = "01JB2Z8Y5Q3W6K9M1N4P7R0MS1"  # turn 1's generated reply
OPENING = "01JB2Z8Y5Q3W6K9M1N4P7R0MS0"  # the scenario's opening narration (turn 0)
SAY = "01JB2Z8Y5Q3W6K9M1N4P7R0MS2"  # the player's input
INGESTED = "01JB2Z8Y5Q3W6K9M1N4P7R0MS3"  # the consumer's own narration
OP = "01JB2Z8Y5Q3W6K9M1N4P7R0OP9"
ME = "console:nate"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)  # #85: the play header's fingerprint

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (feedback_views, play_views, history_views, story_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


@pytest.fixture
def store(engine: FakeEngine) -> FeedbackStore:
    return FeedbackStore(engine, messages={REPLY: (1, 1), OPENING: (0, 1)}).install()


def act_as(user: User, principal: str) -> None:
    ActingPrincipal.objects.update_or_create(user=user, defaults={"principal": principal})


def item(message_id: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "turn": 1,
        "variant": 1,
        "beat": 1,
        "kind": "message_rendered",
        "actor_character_id": None,
        "render_character_ids": [TOBIN],
        "channel": "message",
        "text": f"text of {message_id[-3:]}",
        "operation_id": OP,
        "message_id": message_id,
        "origin": "generated",
    }
    body.update(changes)
    return body


def player_input(message_id: str = SAY, **changes: Any) -> dict[str, Any]:
    return item(
        message_id,
        kind="player_input",
        channel="say",
        actor_character_id=MARA,
        render_character_ids=None,
        **changes,
    )


def history_with(*items: dict[str, Any]) -> dict[str, Any]:
    body = history_json()
    body["items"] = list(items)
    return body


def open_url(message_id: str = REPLY, view: str = "play") -> str:
    return f"/stories/{STORY}/messages/{message_id}/feedback/?view={view}"


def retract_url(message_id: str = REPLY) -> str:
    return f"/stories/{STORY}/messages/{message_id}/feedback/retract/?view=play"


def opened(client: Client, message_id: str = REPLY) -> str:
    response = client.get(open_url(message_id), headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def token_of(html: str) -> str:
    found = re.search(r'name="prefill" value="([^"]+)"', html)
    assert found is not None
    return found.group(1)


def save(
    client: Client, html: str, *, message_id: str = REPLY, htmx: bool = True, **fields: Any
) -> Any:
    data: dict[str, Any] = {"prefill": token_of(html), "view": "play", "text": ""}
    data.update(fields)
    url = open_url(message_id)
    return client.post(url, data, headers=HTMX if htmx else {})


def requests(engine: FakeEngine, method: str, path_end: str = "/feedback") -> list[Any]:
    return [r for r in engine.requests if r.method == method and r.url.path.endswith(path_end)]


def widget_ids(html: str, view: str) -> list[str]:
    return re.findall(rf'id="feedback-{view}-([0-9A-Z]+)"', html)


# --- the gate -----------------------------------------------------------------------------------


def test_only_generated_messages_carry_a_control_in_the_play_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    history = history_with(
        item(OPENING, turn=0, beat=None, channel="narration", render_character_ids=None),
        player_input(),
        item(REPLY),
        item(INGESTED, turn=2, channel="narration", origin="ingested"),
        player_input("01JB2Z8Y5Q3W6K9M1N4P7R0MS4", turn=2),
    )
    answer_page(engine, history=history)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert widget_ids(html, "play") == [OPENING, REPLY]  # the opening narration is rateable
    assert f'href="/stories/{STORY}/messages/{REPLY}/feedback/?view=play&amp;next=' in html
    assert not requests(engine, "GET")  # collapsed: no read until it is opened


def test_split_parts_sharing_a_message_id_render_one_control(
    operator_client: Client, engine: FakeEngine
) -> None:
    history = history_with(
        player_input(),
        item(REPLY, channel="think", beat=1),
        item(REPLY, channel="message", beat=2),
        item(REPLY, channel="message", beat=3),
    )
    answer_page(engine, history=history)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert widget_ids(html, "play") == [REPLY]
    # the first rendered part (here the think part) carries it
    first = html.index(f'id="feedback-play-{REPLY}"')
    assert html.rfind("channel-think", 0, first) > html.rfind("channel-message", 0, first)


def test_the_history_page_shows_controls_in_full_width_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    history_helpers.answer(engine)
    html = operator_client.get(history_helpers.URL).content.decode()
    replies = [i["message_id"] for i in history_helpers.history_json()["items"]]
    rated = [
        i["message_id"]
        for i in history_helpers.history_json()["items"]
        if i["kind"] == "message_rendered"
    ]
    assert widget_ids(html, "transcript") == rated
    assert rated
    assert len(rated) < len(replies)
    assert '<tr class="feedback-row"><td colspan="7">' in html
    # audit records (channel audit) never get one
    assert html.count('class="feedback ') == len(rated)


def test_the_history_page_moves_the_control_to_the_first_visible_part(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = history_helpers.history_json()
    body["items"] = [
        item(REPLY, channel="latent", beat=1),
        item(REPLY, channel="message", beat=2),
        item(INGESTED, channel="message", origin="ingested", beat=3),
    ]
    body["audit"] = []
    history_helpers.answer(engine, history=body)
    html = operator_client.get(history_helpers.URL).content.decode()
    assert widget_ids(html, "transcript") == [REPLY]


def test_an_archived_story_still_offers_rating(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    archived = story_json(status="archived", archived_at="2026-10-24T22:00:00Z")
    answer_page(engine, story=archived, history=history_with(player_input(), item(REPLY)))
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert widget_ids(html, "play") == [REPLY]
    form = opened(operator_client)
    assert "disabled" not in form
    response = save(operator_client, form, rating="3")
    assert "Rated 3" in response.content.decode()


# --- opening the form ---------------------------------------------------------------------------


def test_opening_reads_as_the_acting_principal(
    operator_client: Client, operator: User, engine: FakeEngine, store: FeedbackStore
) -> None:
    act_as(operator, "alice")
    html = opened(operator_client)
    (read,) = requests(engine, "GET")
    assert read.headers["X-Epistrel-Principal"] == "alice"
    assert "Turn 1, variant 1" in html
    assert 'href="/principals/alice/"' in html
    assert "1 = worst, 5 = best" in html
    for tag in store.tags:
        assert f'value="{tag}"' in html
    assert 'maxlength="2000"' in html
    assert "Up to 2000 characters" in html
    assert not EngineOperation.objects.exists()  # reads are not recorded


def test_another_principal_sees_their_own_empty_rating(
    operator_client: Client, operator: User, engine: FakeEngine, store: FeedbackStore
) -> None:
    store.seed(STORY, REPLY, ME, rating=2, tags=["repetitive"])
    mine = opened(operator_client)
    assert "Rated <strong>2</strong>" in mine
    act_as(operator, "alice")
    hers = opened(operator_client)
    assert "Not rated" in hers
    assert "Rated <strong>" not in hers
    assert "checked" not in hers


def test_retract_is_shown_only_when_rated(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    assert ">Retract</button>" not in opened(operator_client)
    store.seed(STORY, REPLY, ME, rating=4)
    html = opened(operator_client)
    assert ">Retract</button>" in html
    assert re.search(r'name="rating" value="4"[^>]*checked', html)


def test_an_unknown_message_cannot_be_rated(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client, INGESTED)
    assert "Can't be rated (the Engine doesn't know this message)" in html
    assert "<form" not in html


def test_a_failed_settings_read_still_lets_the_operator_rate(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    engine.fail("GET", SETTINGS, EngineUnreachable("down"))
    html = opened(operator_client)
    assert "Couldn&#x27;t read feedback tags; you can still rate" in html
    assert "feedback-tags" not in html
    assert 'maxlength="2000"' in html
    assert "Rated 5" in save(operator_client, html, rating="5").content.decode()


def test_an_empty_tag_list_hides_the_tags_group(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    engine.respond("GET", SETTINGS, 200, json=feedback_settings(tags=[]))
    html = opened(operator_client)
    assert "feedback-tags" not in html
    assert "read feedback tags" not in html


def test_close_recollapses_with_zero_engine_calls(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = operator_client.get(open_url() + "&close=1", headers=HTMX).content.decode()
    assert not engine.requests
    assert ">Rate</a>" in html
    assert "<form" not in html


def test_the_context_block_names_what_the_rating_was_given_under(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    row = EngineOperation.objects.create(
        operator_username="nate",
        principal=ME,
        kind="respond",
        http_method="POST",
        path=f"/stories/{STORY}/respond",
        story_id=STORY,
        outcome="ok",
        http_status=200,
        request={"body": {}, "headers": {}},
        engine_operation_id=OPERATION,
    )
    store.seed(STORY, REPLY, ME, rating=3)
    html = opened(operator_client)
    assert f'href="/operations/{row.pk}/"><code>{OPERATION}</code>' in html
    assert f"<code>{CONTEXT['config_fingerprint']}</code>" in html
    assert 'href="/profiles/00000000000000000000PRF001/?version=3"' in html
    assert "generation: stub-gen (2026-09)" in html
    assert "extraction: stub-ext<" in html  # no reported version: no parenthesis
    assert "uncertified" in html


def test_the_context_block_is_hidden_without_a_rating(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    assert "feedback-context" not in opened(operator_client)


# --- saving -------------------------------------------------------------------------------------


def test_a_first_rating_puts_the_normalised_body_and_says_rated(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client)
    response = save(operator_client, html, rating="2", tags=["repetitive"], text="  Slow\r\npace ")
    body = response.content.decode()
    (put,) = requests(engine, "PUT")
    assert json.loads(put.content) == {"rating": 2, "tags": ["repetitive"], "text": "Slow\npace"}
    assert put.headers["X-Epistrel-Principal"] == ME
    assert "Idempotency-Key" not in put.headers
    assert "Rated 2" in body
    assert "2/5" in body
    assert f'id="feedback-play-{REPLY}"' in body
    assert "<form" not in body


def test_an_empty_text_is_sent_as_null(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    save(operator_client, opened(operator_client), rating="1", text="   ")
    (put,) = requests(engine, "PUT")
    assert json.loads(put.content) == {"rating": 1, "tags": [], "text": None}


def test_re_rating_says_updated_and_the_old_value_joins_the_history(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    save(operator_client, opened(operator_client), rating="2", tags=["repetitive"])
    response = save(operator_client, opened(operator_client), rating="4")
    assert "Updated to 4" in response.content.decode()
    html = opened(operator_client)
    assert "<summary>History (1)</summary>" in html
    assert "<td>replaced</td><td>2</td><td>repetitive</td>" in html


def test_a_200_with_the_same_updated_at_says_no_change(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    store.seed(STORY, REPLY, ME, rating=3)
    html = opened(operator_client)
    same = store.body(STORY, REPLY, ME)
    engine.respond("PUT", FEEDBACK, 200, json={**same, "rating": 3})
    response = save(operator_client, html, rating="3", text="new words")
    assert "No change (already rated 3)" in response.content.decode()


def test_an_unchanged_submission_makes_no_engine_call(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    store.seed(STORY, REPLY, ME, rating=3, tags=["repetitive", "off_topic"], text="Too long")
    html = opened(operator_client)
    engine.reset()
    response = save(
        operator_client, html, rating="3", tags=["off_topic", "repetitive"], text=" Too long\r\n"
    )
    assert not engine.requests
    assert not EngineOperation.objects.exists()
    assert "No change" in response.content.decode()


def test_an_unknown_tag_is_listed_and_marked(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    engine.respond("GET", SETTINGS, 200, json=feedback_settings(tags=[*store.tags, "ghost"]))
    html = opened(operator_client)
    response = save(operator_client, html, rating="2", tags=["ghost", "repetitive"], text="kept")
    body = response.content.decode()
    assert "Unknown tags: ghost" in body
    assert (
        'class="feedback-tag unknown-tag"><input type="checkbox" name="tags" value="ghost"' in body
    )
    assert "kept</textarea>" in body


def test_a_stale_tag_round_trips(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    store.tags.append("old_tag")  # the Engine still has it; the settings no longer list it
    engine.respond("GET", SETTINGS, 200, json=feedback_settings(tags=["repetitive"]))
    store.seed(STORY, REPLY, ME, rating=3, tags=["old_tag"])
    html = opened(operator_client)
    assert re.search(r'value="old_tag"[^>]*checked', html)
    assert "old_tag (no longer configured)" in html
    save(operator_client, html, rating="4", tags=["old_tag"])
    save(operator_client, opened(operator_client), rating="4")  # unticked: removed
    first, second = requests(engine, "PUT")
    assert json.loads(first.content)["tags"] == ["old_tag"]
    assert json.loads(second.content)["tags"] == []


def test_the_text_cap_is_enforced_before_any_call(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    engine.respond("GET", SETTINGS, 200, json=feedback_settings(tags=[], cap=5))
    html = opened(operator_client)
    assert 'maxlength="5"' in html
    assert "Up to 5 characters" in html
    engine.reset()
    body = save(operator_client, html, rating="2", text=" abcdef ").content.decode()
    assert "the limit is 5" in body
    assert not engine.requests
    body = save(operator_client, html, rating="2", text=" abcde \r\n").content.decode()
    assert "Rated 2" in body


def test_form_errors_make_no_call(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    many = [f"tag_{n}" for n in range(11)]
    engine.respond("GET", SETTINGS, 200, json=feedback_settings(tags=many))
    html = opened(operator_client)
    engine.reset()
    assert "Choose a rating from 1 to 5" in save(operator_client, html).content.decode()
    assert "Choose a rating from 1 to 5" in save(operator_client, html, rating="6").content.decode()
    body = save(operator_client, html, rating="2", tags=many).content.decode()
    assert "Choose at most 10 tags" in body
    assert not engine.requests


def test_a_text_validation_error_lands_on_the_text_field(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client)
    error = {
        "error": {
            "code": "validation_error",
            "message": "invalid",
            "details": {"errors": [{"field": "body.text", "message": "text is too long"}]},
        }
    }
    engine.respond("PUT", FEEDBACK, 422, json=error)
    body = save(operator_client, html, rating="2", text="words").content.decode()
    assert re.search(r'<span class="errors"><ul class="errorlist"[^>]*><li>text is too long', body)


def test_other_engine_errors_show_the_panel_above_the_kept_form(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client)
    error = {"error": {"code": "erasure_pending", "message": "held", "details": {}}}
    engine.respond("PUT", FEEDBACK, 503, json=error)
    body = save(operator_client, html, rating="2", text="mine").content.decode()
    assert "Engine error 503 erasure_pending" in body
    assert "mine</textarea>" in body


def test_a_404_on_save_makes_the_message_unrateable(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client)
    del store.messages[REPLY]
    body = save(operator_client, html, rating="2").content.decode()
    assert "Can't be rated (the Engine doesn't know this message)" in body


def test_an_expired_or_foreign_prefill_makes_no_call(
    operator_client: Client, operator: User, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client)
    engine.reset()
    tampered = operator_client.post(
        open_url(), {"prefill": "x" + token_of(html), "rating": "2"}, headers=HTMX
    )
    assert "This form expired — open it again" in tampered.content.decode()
    other = operator_client.post(
        open_url(SAY), {"prefill": token_of(html), "rating": "2"}, headers=HTMX
    )
    assert "This form expired — open it again" in other.content.decode()
    act_as(operator, "alice")
    switched = save(operator_client, html, rating="2")
    assert "This form expired — open it again" in switched.content.decode()
    old = signing.loads(token_of(html), salt=PREFILL_SALT)
    assert old["principal"] == ME
    assert not engine.requests


# --- retracting ---------------------------------------------------------------------------------


def test_retract_deletes_then_rereads(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    store.seed(STORY, REPLY, ME, rating=2, tags=["repetitive"])
    html = opened(operator_client)
    response = operator_client.post(retract_url(), {"prefill": token_of(html)}, headers=HTMX)
    body = response.content.decode()
    (delete,) = requests(engine, "DELETE")
    assert "Idempotency-Key" not in delete.headers
    assert "Rating retracted" in body
    assert "Not rated" in body
    assert "<summary>History (1)</summary>" in body
    assert "<td>retracted</td><td>2</td>" in body
    assert ">Retract</button>" not in body
    assert "feedback-context" not in body


def test_retract_without_a_rating_still_answers(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    html = opened(operator_client)
    body = operator_client.post(
        retract_url(), {"prefill": token_of(html)}, headers=HTMX
    ).content.decode()
    assert "Rating retracted" in body
    assert "Not rated" in body


# --- the operation log (#44) --------------------------------------------------------------------


def test_put_and_delete_are_recorded_without_a_key(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    save(operator_client, opened(operator_client), rating="2")
    html = opened(operator_client)
    operator_client.post(retract_url(), {"prefill": token_of(html)}, headers=HTMX)
    rows = list(EngineOperation.objects.order_by("pk"))
    assert [r.kind for r in rows] == ["put_message_feedback", "delete_message_feedback"]
    for row in rows:
        assert row.idempotency_key is None or row.idempotency_key == ""
        assert row.story_id == STORY
        assert (row.entity_type, row.entity_id) == ("story", STORY)
        assert row.principal == ME


# --- without JS ---------------------------------------------------------------------------------


def test_without_htmx_the_form_is_a_page_and_results_flash(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    page = operator_client.get(open_url() + "&next=/stories/" + STORY + "/history/")
    html = page.content.decode()
    assert "<!DOCTYPE html>" in html or "<html" in html
    assert f'<a class="back" href="/stories/{STORY}/history/">Back</a>' in html
    response = save(
        operator_client, html, htmx=False, rating="2", next=f"/stories/{STORY}/history/"
    )
    assert response.status_code == 302
    assert response["Location"] == f"/stories/{STORY}/history/"
    response = save(
        operator_client, opened(operator_client), htmx=False, rating="3", next="https://evil.test/"
    )
    assert response["Location"] == f"/stories/{STORY}/"
    assert "Updated to 3" in [str(m) for m in get_messages(response.wsgi_request)]


def test_without_htmx_a_retract_returns_to_the_open_form(
    operator_client: Client, engine: FakeEngine, store: FeedbackStore
) -> None:
    store.seed(STORY, REPLY, ME, rating=2)
    html = opened(operator_client)
    response = operator_client.post(retract_url(), {"prefill": token_of(html)})
    assert response.status_code == 302
    assert response["Location"].startswith(f"/stories/{STORY}/messages/{REPLY}/feedback/?view=play")
    assert "Rating retracted" in [str(m) for m in get_messages(response.wsgi_request)]
    assert len(requests(engine, "DELETE")) == 1
