"""Co-author implausible-knowledge notices in the play inspector, with Override (#185).

Served by the schema-validating fake Engine under #239's Structured v3 and #232's
``m6/story.json`` cast (Mara the player, Alice, Bob). Answers are ``respond_json(notices=[…])``;
the ``correctable: false`` case is #232's ``m6/corrected.json`` notice (a turn a correction
wrote). Fix / Clarify links #263's ``stories:correct``, stubbed by D19's shared urlconf until
#263 lands; under the real urlconf (no ``stories:correct`` yet) there is no Fix control at all.
"""

import copy
import json
import re
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils.html import escape

from console.engine.models import EffectiveConfig, Story
from console.inspect import short_id
from console.models import EngineOperation
from console.notices import (
    NOT_CORRECTABLE_REASON,
    NOTICES_OVERRIDDEN_KEY,
    NOTICES_OVERRIDDEN_MAX,
    notice_cards,
)
from console.views.play import PlayStory
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import structured_v3
from tests.play.helpers import (
    HTMX,
    OPERATION,
    QUEUED_OP,
    RESPOND,
    answer_page,
    operation_json,
    respond_json,
    turn_data,
)

pytestmark = pytest.mark.django_db

STORY = "01JM6ST0000000000000000001"
MARA = "01JM6CH0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
OP = "01JM6QP0000000000000000185"
NOTICE = "01JM6NT0000000000000000185"
OTHER = "01JM6NT0000000000000000186"
STUB_URLS = "tests.urls_with_engine_operation"
EFFECTIVE = "/stories/{story_id}/effective-config"
PLAY = f"/stories/{STORY}/"
TURN_URL = f"/stories/{STORY}/turn/"
RECONCILIATION = f"/stories/{STORY}/inspect/effects/reconciliation/"
LINE = "Implausible knowledge — Alice had no way to know: “the key was in the drawer”"
NOW_KNOWS = "The turn committed anyway, and Alice now knows it."
WARNING = "This turn has an external effect a correction can't undo"


def override_url(notice_id: str = NOTICE) -> str:
    return reverse("stories:override-notice", args=[STORY, notice_id])


def notice(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": NOTICE,
        "kind": "implausible_knowledge",
        "character_id": ALICE,
        "claim_text": "the key was in the drawer",
        "turn": 3,
        "correctable": True,
        "external_effects": False,
    }
    body.update(changes)
    return body


def answer(*notices: dict[str, Any]) -> dict[str, Any]:
    """``respond_play.json`` as turn 3, recorded by ``OP``, carrying ``notices``."""
    body = copy.deepcopy(respond_json(turn=3))
    for beat in body["beats"]:
        beat.update(turn=3, operation_id=OP)
    if notices:
        body["notices"] = list(notices)
    return body


def m6_history() -> dict[str, Any]:
    history: dict[str, Any] = copy.deepcopy(engine_fixture("get_history"))
    for item in history["items"]:
        item.update(turn=3, operation_id=OP)
        if item.get("actor_character_id"):
            item["actor_character_id"] = MARA
        if item.get("render_character_ids"):
            item["render_character_ids"] = [ALICE]
    history["turns"][0].update(turn=3)
    history.update(as_of=3)
    return history


def serve(engine: FakeEngine) -> None:
    answer_page(engine, story=engine_fixture("m6/story"), history=m6_history())
    engine.side_effect("GET", EFFECTIVE, lambda request: httpx.Response(200, json=structured_v3()))


def snapshot() -> str:
    story = Story.from_json(engine_fixture("m6/story"))
    config = EffectiveConfig.from_json(structured_v3())
    assert story is not None
    assert config is not None
    return PlayStory.of(story, 45.0, config=config).signed()


def log_row(response: dict[str, Any], **fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {"body": {"actor_character_id": MARA}, "headers": {}},
        "http_status": 200,
        "engine_operation_id": OP,
        "timeline_revision": 13,
        "config_revision": 6,
        "idempotency_key": "01JM6QP0000000000000000K85",
        "outcome": "ok",
        "response": response,
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


def post_turn(client: Client) -> str:
    response = client.post(TURN_URL, turn_data(snapshot=snapshot(), actor=MARA), headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def inspect(client: Client, **extra: str) -> str:
    url = reverse("stories:inspector", args=[STORY])
    response = client.get(url, {"operation": OP, **extra}, headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def page_inspector(client: Client, **query: str) -> str:
    response = client.get(PLAY, query)
    assert response.status_code == 200
    return response.content.decode().split('<div id="inspector">')[1]


def section_of(html: str) -> str:
    match = re.search(r'<section class="notices" aria-label="Notices">.*?</section>', html, re.S)
    assert match, "no notices section"
    return match.group(0)


def card_of(html: str, notice_id: str = NOTICE) -> str:
    match = re.search(
        rf'<article class="notice-card" id="notice-{notice_id}">.*?</article>', html, re.S
    )
    assert match, f"no card for {notice_id}"
    return match.group(0)


def overridden_line(notice_id: str = NOTICE) -> str:
    return (
        f'<p class="notice-overridden" id="notice-{notice_id}">Overridden this session — '
        "Alice: “the key was in the drawer”</p>"
    )


def override(client: Client, notice_id: str = NOTICE, **data: str) -> Any:
    return client.post(override_url(notice_id), {"next": PLAY, **data})


# --- the cards ------------------------------------------------------------------------------------


def test_live_turn_shows_the_notice(operator_client: Client, engine: FakeEngine) -> None:
    claim = notice(id=OTHER, claim_text="Bob <b>hid</b> it", turn=3)
    engine.respond("POST", RESPOND, 200, json=answer(notice(), claim))
    serve(engine)
    html = post_turn(operator_client)
    section = section_of(html)
    card = card_of(section)
    assert f'<p class="notice-line">{LINE}</p>' in card
    assert f"<p>{NOW_KNOWS}</p>" in card
    # The claim is escaped and verbatim: a <b> renders as text.
    other = card_of(section, OTHER)
    assert "Alice had no way to know: “Bob &lt;b&gt;hid&lt;/b&gt; it”" in other
    assert "<b>hid</b>" not in html
    # Override is a POST form with CSRF, a next field and the HTMX swap of its own card.
    form = re.search(r"<form [^>]*>", card)
    assert form
    assert f'method="post" action="{override_url()}"' in form.group(0)
    assert f'hx-post="{override_url()}" hx-target="#notice-{NOTICE}" hx-swap="outerHTML"' in (
        form.group(0)
    )
    assert 'name="csrfmiddlewaretoken"' in card
    assert f'name="next" value="{PLAY}?operation={OP}"' in card
    assert '<button type="submit">Override</button>' in card
    # Beside the turn and apart from the reply: right after the title, before the reply.
    assert html.index("<h3>Turn 3 (variant 1)</h3>") < html.index('<section class="notices"')
    assert html.index('<section class="notices"') < html.index('<blockquote class="reply">')
    assert "the key was in the drawer" not in html.split('<blockquote class="reply">')[1]


@override_settings(ROOT_URLCONF=STUB_URLS)
def test_fix_shown_when_correctable_and_registered(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row(answer(notice()))
    card = card_of(inspect(operator_client, snapshot=snapshot()))
    query = urlencode({"next": f"{PLAY}?operation={OP}"})
    assert f'<a class="button" href="/stories/{STORY}/correct/3/?{query}">Fix / Clarify</a>' in card
    # correctable: false (a turn a correction wrote) → greyed with the informational reason.
    EngineOperation.objects.all().delete()
    corrected = engine_fixture("m6/corrected")
    log_row(answer(*corrected["notices"]))
    (written,) = corrected["notices"]
    card = card_of(inspect(operator_client, snapshot=snapshot()), written["id"])
    assert (
        f'<span class="disabled" aria-disabled="true" title="{escape(NOT_CORRECTABLE_REASON)}">'
        "Fix / Clarify</span>"
    ) in card
    assert 'class="button"' not in card
    assert '<button type="submit">Override</button>' in card  # Override on every card


def test_no_fix_control_without_the_correct_route(operator_client: Client) -> None:
    """**Complement:** under a urlconf without ``stories:correct`` (the real one, until #263)
    there is no Fix control at all, not even greyed."""
    corrected = engine_fixture("m6/corrected")
    log_row(answer(notice(), *corrected["notices"]))
    section = section_of(inspect(operator_client, snapshot=snapshot()))
    assert "Fix / Clarify" not in section
    assert "/correct/" not in section
    assert section.count('<button type="submit">Override</button>') == 2


def test_external_effects_warning(operator_client: Client) -> None:
    log_row(answer(notice(external_effects=True)))
    card = card_of(inspect(operator_client))
    assert (
        f'<p class="warning">{WARNING} — it stays <a href="{RECONCILIATION}">'
        "listed for reconciliation</a></p>"
    ) in card
    # **Complement:** false → no warning.
    EngineOperation.objects.all().delete()
    log_row(answer(notice()))
    card = card_of(inspect(operator_client))
    assert "warning" not in card
    assert "reconciliation" not in card


@override_settings(ROOT_URLCONF="tests.inspect.urls_world_no_sections")
def test_external_effects_warning_unlinked_without_reconciliation(operator_client: Client) -> None:
    """D19: while ``inspect:reconciliation`` doesn't resolve the words are plain text."""
    log_row(answer(notice(external_effects=True)))
    card = card_of(inspect(operator_client))
    assert f'<p class="warning">{WARNING} — it stays listed for reconciliation</p>' in card


def test_logged_and_polled_turns_show_notices(operator_client: Client, engine: FakeEngine) -> None:
    # ?operation= on the full page, labelled from the story read.
    log_row(answer(notice()))
    serve(engine)
    assert LINE in card_of(page_inspector(operator_client, operation=OP))
    # The HTMX stories:inspector, labelled from the snapshot with no Engine read.
    engine.reset()
    assert LINE in card_of(inspect(operator_client, snapshot=snapshot()))
    assert engine.requests == []
    # Without a snapshot: the short id (C4).
    assert f"{short_id(ALICE)} had no way to know" in card_of(inspect(operator_client))
    # ?turn=N after a regenerate.
    EngineOperation.objects.all().delete()
    regenerated = engine_fixture("regenerate") | {"turn": 3, "variant": 2}
    regenerated["notices"] = [notice()]
    log_row(regenerated, kind="regenerate", path=f"/stories/{STORY}/regenerate")
    serve(engine)
    inspector = page_inspector(operator_client, turn="3")
    assert "<dt>Previous variant</dt>" in inspector
    assert LINE in card_of(inspector)
    # A poll result.
    EngineOperation.objects.all().delete()
    engine.respond(
        "POST",
        RESPOND,
        202,
        json=engine_fixture("respond_queued") | {"operation_id": QUEUED_OP},
        headers={"Location": f"/operations/{QUEUED_OP}"},
    )
    post_turn(operator_client)
    committed = operation_json(
        "committed",
        result={"status": 200, "body": answer(notice())},
        story_id=STORY,
        viewpoint="omniscient",
        timeline_revision=13,
    )
    engine.respond("GET", OPERATION, 200, json=committed)
    url = reverse("stories:poll", args=[STORY, QUEUED_OP])
    polled = operator_client.get(
        url, {"viewpoint": "omniscient", "deadline": "45", "position": "1"}, headers=HTMX
    )
    assert polled.status_code == 200
    assert LINE in card_of(polled.content.decode())


def test_first_load_shows_the_latest_turns_notices(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row(answer(notice()))
    serve(engine)
    assert LINE in card_of(page_inspector(operator_client))


# --- Override -------------------------------------------------------------------------------------


def test_override_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    log_row(answer(notice()))
    rows = EngineOperation.objects.count()
    engine.reset()
    response = override(operator_client, next=f"{PLAY}?operation={OP}")
    assert response.status_code == 302
    assert response["Location"] == f"{PLAY}?operation={OP}"
    # **Complement:** the override made no Engine call and added no operation-log row.
    assert engine.requests == []
    assert EngineOperation.objects.count() == rows
    assert operator_client.session[NOTICES_OVERRIDDEN_KEY] == [NOTICE]
    # Re-rendering the page shows the overridden line, with no controls.
    serve(engine)
    inspector = page_inspector(operator_client, operation=OP)
    assert overridden_line() in inspector
    assert '<article class="notice-card"' not in inspector
    assert "Override</button>" not in inspector
    # The turn itself is unchanged: its reply still shows.
    assert '<blockquote class="reply">' in inspector


def test_override_without_next_goes_to_the_play_page(operator_client: Client) -> None:
    response = operator_client.post(override_url(), {"next": "https://evil.example/"})
    assert response.status_code == 302
    assert response["Location"] == PLAY


def test_override_htmx_answers_the_line(operator_client: Client) -> None:
    data = {
        "next": PLAY,
        "claim_text": "the key was in the drawer",
        "character_id": ALICE,
        "label": "posted label",
        "snapshot": snapshot(),
    }
    response = operator_client.post(override_url(), data, headers=HTMX)
    assert response.status_code == 200
    html = response.content.decode().strip()
    assert html == overridden_line()  # the snapshot's label wins; no <section> wrapper
    # Without a snapshot: the posted label, then the short id, then nothing.
    data.pop("snapshot")
    html = operator_client.post(override_url(), data, headers=HTMX).content.decode()
    assert "Overridden this session — posted label: “the key was in the drawer”" in html
    data.pop("label")
    html = operator_client.post(override_url(), data, headers=HTMX).content.decode()
    assert f"Overridden this session — {short_id(ALICE)}: “the key was in the drawer”" in html
    html = operator_client.post(override_url(), {}, headers=HTMX).content.decode().strip()
    assert html == f'<p class="notice-overridden" id="notice-{NOTICE}">Overridden this session</p>'


def test_override_is_per_session(
    operator_client: Client, operator: Any, engine: FakeEngine
) -> None:
    log_row(answer(notice()))
    override(operator_client)
    assert overridden_line() in inspect(operator_client, snapshot=snapshot())
    # **Complement:** another browser (a new session) sees the full card.
    other = Client()
    other.force_login(operator)
    assert LINE in card_of(inspect(other, snapshot=snapshot()))


def test_override_refuses_get_and_bad_ids(operator: Any, operator_client: Client) -> None:
    assert operator_client.get(override_url()).status_code == 405
    bad = f"/stories/{STORY}/notices/not-a-ulid/override/"
    assert operator_client.post(bad, {"next": PLAY}).status_code == 404
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(operator)
    assert strict.post(override_url(), {"next": PLAY}).status_code == 403
    assert strict.post(override_url(), {"next": PLAY}, headers=HTMX).status_code == 403
    assert NOTICES_OVERRIDDEN_KEY not in strict.session


def test_override_keeps_newest_500(operator_client: Client) -> None:
    ids = [f"01JM6NT{n:019d}" for n in range(NOTICES_OVERRIDDEN_MAX + 1)]
    session = operator_client.session
    session[NOTICES_OVERRIDDEN_KEY] = ids[:-1]
    session.save()
    assert override(operator_client, ids[-1]).status_code == 302
    stored = operator_client.session[NOTICES_OVERRIDDEN_KEY]
    assert len(stored) == NOTICES_OVERRIDDEN_MAX
    assert ids[0] not in stored
    assert stored[-1] == ids[-1]
    # Re-overriding moves an id to newest.
    override(operator_client, ids[1])
    stored = operator_client.session[NOTICES_OVERRIDDEN_KEY]
    assert stored[-1] == ids[1]
    assert stored.count(ids[1]) == 1


def test_corrupt_session_value_reads_empty(operator_client: Client) -> None:
    session = operator_client.session
    session[NOTICES_OVERRIDDEN_KEY] = {"not": "a list"}
    session.save()
    log_row(answer(notice()))
    assert LINE in card_of(inspect(operator_client, snapshot=snapshot()))
    override(operator_client)
    assert operator_client.session[NOTICES_OVERRIDDEN_KEY] == [NOTICE]


# --- unreadable and absent notices ----------------------------------------------------------------


def test_unreadable_notice_kept_raw(operator_client: Client) -> None:
    bad = {k: v for k, v in notice(id=OTHER).items() if k != "claim_text"}
    log_row(answer(notice(), bad))
    section = section_of(inspect(operator_client, snapshot=snapshot()))
    assert LINE in card_of(section)
    assert '<p class="warning">1 notice couldn&#x27;t be read</p>' in section
    more = section.split('<details class="more"><summary>More</summary>')[1]
    assert escape(json.dumps(OTHER)) in more
    assert section.count('<article class="notice-card"') == 1
    # The unread line comes last in the section.
    assert section.index("</article>") < section.index("couldn&#x27;t be read")


def test_unknown_kind_reads_humanised_with_no_fix() -> None:
    cards = notice_cards(
        [notice(kind="stale_claim")],
        STORY,
        {ALICE: "Alice"},
        overridden=frozenset(),
        latest=None,
    )
    (card,) = cards.cards
    assert card.kind_text == "Stale claim"
    assert (card.fix_url, card.fix_disabled_reason) == ("", "")
    assert not card.implausible


def test_no_notices_no_section(operator_client: Client) -> None:
    log_row(engine_fixture("respond") | {"beats": answer()["beats"]})
    html = inspect(operator_client)
    assert '<section class="notices"' not in html
    assert '<tr class="notices">' not in html
    assert '<article class="notice-card"' not in html
    assert "<h3>" in html
