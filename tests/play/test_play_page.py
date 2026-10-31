"""The play page itself (#48): header, two columns, transcript at a viewpoint, restored
waiting cards and per-section degradation."""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_one_relation
from tests.play.helpers import (
    HTMX,
    MARA,
    QUEUED_OP,
    STORY,
    TOBIN,
    answer_page,
    calls,
    error_json,
    snapshot,
)

pytestmark = pytest.mark.django_db


def test_header_and_two_columns(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "<h1>Harbour</h1>" in html
    assert f'<li id="cast-{MARA}">Mara (player) <a class="button danger erase"' in html  # #86
    assert f'<li id="cast-{TOBIN}">Tobin · ' in html  # #61: role and weight, then the controls
    assert '<a class="button promote"' in html  # #72: Promote on non-players
    assert "<dt>Timeline revision</dt><dd>1</dd>" in html
    assert "Teen" in html
    left = html.split('<section class="play-left">')[1].split('<section class="play-right">')[0]
    assert '<div id="transcript">' in left
    assert '<div id="turn-form">' in left
    assert '<div id="inspector">' in html.split('<section class="play-right">')[1]
    assert "Is the ferry late again?" in left
    assert f"/stories/{STORY}/archive/" in html


def test_the_transcript_follows_the_viewpoint_select(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    url = reverse("stories:transcript", args=[STORY])
    html = operator_client.get(
        url, {"output_viewpoint": "default", "snapshot": snapshot()}, headers=HTMX
    ).content.decode()
    assert html.startswith('<div id="transcript">') or '<div id="transcript">' in html
    (history,) = calls(engine, "GET", f"/stories/{STORY}/history")
    assert history.url.params["viewpoint"] == f"character:{MARA}"  # the story default
    assert calls(engine, "GET", f"/stories/{STORY}") == []  # resolved from the snapshot
    engine.reset()
    operator_client.get(url, {"viewpoint": f"character:{TOBIN}"}, headers=HTMX)
    (history,) = calls(engine, "GET", f"/stories/{STORY}/history")
    assert history.url.params["viewpoint"] == f"character:{TOBIN}"


def test_the_transcript_works_as_a_plain_get(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    url = reverse("stories:transcript", args=[STORY])
    html = operator_client.get(url, {"viewpoint": f"character:{TOBIN}"}).content.decode()
    assert "<h1>Harbour</h1>" in html
    assert "as Tobin" in html
    assert f'<option value="character:{TOBIN}" selected>' in html


def test_a_failed_history_read_degrades_only_the_transcript(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    engine.respond(
        "GET", "/stories/{story_id}/history", 422, json=error_json("viewpoint_unresolved", "no")
    )
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "viewpoint_unresolved" in html.split('<div id="transcript">')[1]
    assert '<div id="turn-form">' in html


def test_older_link_when_there_is_more_history(operator_client: Client, engine: FakeEngine) -> None:
    from tests.play.helpers import history_json

    history = history_json()
    history["next_before_turn"] = 1
    answer_page(engine, history=history)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert f"/stories/{STORY}/history/?before_turn=1" in html
    assert operator_client.get(f"/stories/{STORY}/history/").status_code == 200


def queued_row(**fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {
            "body": {"output_viewpoint": f"character:{TOBIN}", "budget": {"deadline_s": 20}},
            "headers": {},
        },
        "http_status": 202,
        "engine_operation_id": QUEUED_OP,
        "outcome": "queued",
        "response": {"operation_id": QUEUED_OP, "position": 2},
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


def test_a_reload_restores_live_waiting_cards(operator_client: Client, engine: FakeEngine) -> None:
    queued_row()
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    status = html.split('<div id="turn-status">')[1].split('<div id="turn-form">')[0]
    assert "Waiting — queued, position 2" in status
    assert "deadline=20" in status
    assert f"viewpoint=character%3A{TOBIN}" in status
    assert "<fieldset disabled>" in html


def test_stale_queued_rows_get_no_card(operator_client: Client, engine: FakeEngine) -> None:
    from datetime import timedelta

    from django.utils import timezone

    queued_row(created_at=timezone.now() - timedelta(seconds=51))
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert "Waiting — queued" not in html
    assert "<fieldset disabled>" not in html


def test_a_failed_settings_read_uses_45_s(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    engine.respond(
        "GET", "/stories/{story_id}/settings", 503, json=error_json("database_unavailable", "x")
    )
    response = operator_client.get(f"/stories/{STORY}/")
    assert response.status_code == 200
    assert '<div id="turn-form">' in response.content.decode()


def test_the_ownership_panel_sits_collapsed_under_the_header(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#79: a collapsed ``<details>``, opened by ``?panel=ownership`` (where writes return)."""
    answer_page(engine)
    answer_one_relation(engine, "story", STORY, principal="team/alice", relation="player")
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert '<details id="ownership" class="ownership">' in html
    assert html.index('class="story-header"') < html.index('id="ownership"')
    assert html.index('id="ownership"') < html.index('<div class="play">')
    assert "relation=player&amp;principal=team%2Falice" in html
    opened = operator_client.get(f"/stories/{STORY}/?panel=ownership").content.decode()
    assert '<details id="ownership" class="ownership" open>' in opened


def test_transcript_shows_classification_at_omniscient(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#207: an omniscient transcript shows a classified say's tag and the legend's line."""
    from tests.play.helpers import history_json

    history = history_json()
    say = history["items"][0]
    say.update(
        propositions=[],
        labels={
            "intent": "sincere",
            "belief_relation": "inconsistent",
            "objective_relation": "false",
            "classification": "lie",
        },
    )
    answer_page(engine, history=history)
    url = reverse("stories:transcript", args=[STORY])
    html = operator_client.get(
        url, {"viewpoint": "omniscient", "snapshot": snapshot()}, headers=HTMX
    ).content.decode()
    (read,) = calls(engine, "GET", f"/stories/{STORY}/history")
    assert read.url.params["viewpoint"] == "omniscient"
    assert (
        '<span class="beat-label beat-classification classification-lie"'
        ' title="classification: lie">Lie</span>'
    ) in html
    assert "<dt>classification</dt>" in html
    assert html.index("<dt>classification</dt>") < html.index('<ol class="transcript">')
    assert "verified" not in html.lower()


def test_transcript_at_a_character_viewpoint_shows_no_classification(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#207 complement (#216): at a character viewpoint the Engine omits a say's labels, so no
    classification tag, "(corrected)" or classification legend line shows, and the Console adds
    nothing of its own."""
    from tests.play.helpers import history_json

    history = history_json()
    for item in history["items"]:
        item.pop("labels", None)
    history["items"][0]["propositions"] = []
    answer_page(engine, history=history)
    url = reverse("stories:transcript", args=[STORY])
    html = operator_client.get(
        url, {"viewpoint": f"character:{TOBIN}", "snapshot": snapshot()}, headers=HTMX
    ).content.decode()
    (read,) = calls(engine, "GET", f"/stories/{STORY}/history")
    assert read.url.params["viewpoint"] == f"character:{TOBIN}"
    assert '<ol class="transcript">' in html
    assert "beat-classification" not in html
    assert "(corrected)" not in html
    assert "<dt>classification</dt>" not in html
