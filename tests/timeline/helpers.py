"""Helpers for the timeline operation tests (#53): "Harbour" (Mara, Tobin) at revision 6 with
five turns, as the play page links its operations, and the Engine's answers captured from a
live ``edge`` (stub adapter)."""

import json
from typing import Any
from urllib.parse import urlencode

from django.test import Client
from django.urls import reverse

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import MARA, STORY, TOBIN, calls, error_json, snapshot, story_json

__all__ = ["MARA", "STORY", "TOBIN", "calls", "error_json"]

KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KY9"
REV = 6
HEAD = 5
NAME = "Harbour"
PLAY = f"/stories/{STORY}/"
REVISION_HEADER = "X-Epistrel-Expected-Revision"
NEW_STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0F00"


def path(action: str) -> str:
    return f"/stories/{{story_id}}/{action}"


def shown(**extra: str) -> dict[str, str]:
    """What a timeline link carries (and its confirm page posts back)."""
    return {"rev": str(REV), "head": str(HEAD), "name": NAME, "next": PLAY, **extra}


def url(route: str, *args: object, **extra: str) -> str:
    return f"{reverse(f'stories:{route}', args=[STORY, *args])}?{urlencode(shown(**extra))}"


def posted(**extra: str) -> dict[str, str]:
    """A confirm POST: the hidden fields plus the render's idempotency key."""
    return {"idempotency_key": KEY, **shown(**extra)}


def regenerate_posted(**extra: str) -> dict[str, str]:
    data = posted(snapshot=snapshot())
    data.update(
        length="",
        length_custom="",
        temperature="",
        top_p="",
        output_viewpoint="",
    )
    data.update(extra)
    return data


def body_of(request: Any) -> Any:
    return json.loads(request.content) if request.content else None


def html(client: Client, target: str) -> str:
    response = client.get(target)
    assert response.status_code == 200
    return response.content.decode()


def post(client: Client, target: str, data: dict[str, Any]) -> Any:
    return client.post(target, data)


def fixture(name: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture(name)
    body.update(changes)
    return body


def recheck_json() -> dict[str, Any]:
    """``RatingCheckStartedBody``: a re-check started before the edit applies (202)."""
    return {
        "timeline_revision": REV + 1,
        "job_id": "01JB2Z8Y5Q3W6K9M1N4P7R0JOB",
        "operation_id": "01JB2Z8Y5Q3W6K9M1N4P7R0CTL",
        "status": "checking",
        "config_revision": 3,
        "idempotency_key": KEY,
    }


def chain(turn: int, variant: int, membership: str, **changes: Any) -> dict[str, Any]:
    found: dict[str, Any] = {
        "turn": turn,
        "variant": variant,
        "membership": membership,
        "tip_turn": max(turn, 3),
        "turn_count": 1,
        "membership_tx": "2026-10-25T00:22:09.519962Z",
        "parent": {"turn": 1, "variant": 1} if turn > 1 else None,
        "requires_check": False,
    }
    found.update(changes)
    return found


def timeline_story(**changes: Any) -> dict[str, Any]:
    """Harbour after three turns (head 3, revision 4): turn 3 has a dormant variant 2."""
    story = story_json(
        next_turn_index=4,
        timeline_revision=4,
        undo_available=True,
        dormant_chains=[
            {
                "from_turn": 3,
                "variant": 2,
                "through_turn": 3,
                "turns": 1,
                "truncate_operation_id": None,
            }
        ],
        variant_chains=[
            chain(1, 1, "active", tip_turn=2, turn_count=2),
            chain(3, 1, "active"),
            chain(3, 2, "dormant"),
        ],
    )
    story.update(changes)
    return story


def answer_play(engine: FakeEngine, *, story: dict[str, Any] | None = None) -> None:
    """The play page's reads for :func:`timeline_story` (three turns of history)."""
    engine.respond("GET", "/stories/{story_id}", 200, json=story or timeline_story())
    engine.respond(
        "GET", "/stories/{story_id}/settings", 200, json=engine_fixture("get_story_settings")
    )
    engine.respond(
        "GET", "/stories/{story_id}/history", 200, json=engine_fixture("get_history_audit")
    )


def logged(kind: str) -> EngineOperation:
    return EngineOperation.objects.get(kind=kind)


def flash(response: Any) -> list[str]:
    return [str(m) for m in response.wsgi_request._messages]
