"""Helpers for the play view tests (#48): the "Harbour" story (Mara, Tobin), its answers and a
turn POST as the page's HTMX form sends it."""

import copy
import json
from typing import Any

from django.test import Client
from django.urls import reverse

from console.engine.models import EffectiveConfig, Story
from console.views.play import PlayStory
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
MARA = "01JB2Z8Y5Q3W6K9M1N4P7R0T3A"
TOBIN = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
TURN_OP = "01JB2Z8Y5Q3W6K9M1N4P7R0OP1"
QUEUED_OP = "01JB2Z8Y5Q3W6K9M1N4P7R0OQ2"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KY1"
HTMX = {"HX-Request": "true"}
RESPOND = "/stories/{story_id}/respond"
OPERATION = "/operations/{operation_id}"


def story_json(**changes: Any) -> dict[str, Any]:
    story: dict[str, Any] = engine_fixture("get_story")
    settings = changes.pop("settings", None)
    if settings is not None:
        story["settings"] = {**story["settings"], **settings}
    story.update(changes)
    return story


def respond_json(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("respond_play")
    body.update(changes)
    return body


def history_json(*, with_turn: bool = True) -> dict[str, Any]:
    history: dict[str, Any] = engine_fixture("get_history")
    if not with_turn:
        history.update(items=[], turns=[], as_of=0, timeline_revision=1)
    return history


def answer_page(
    engine: FakeEngine,
    *,
    story: dict[str, Any] | None = None,
    history: dict[str, Any] | None = None,
    turn_deadline: float = 45.0,
) -> None:
    """The three reads the play page makes: the story, its settings and its history."""
    engine.respond("GET", "/stories/{story_id}", 200, json=story or story_json())
    settings = engine_fixture("get_story_settings")
    settings["effective_budget"]["turn_deadline"] = turn_deadline
    engine.respond("GET", "/stories/{story_id}/settings", 200, json=settings)
    engine.respond("GET", "/stories/{story_id}/history", 200, json=history or history_json())


def snapshot(
    *,
    story: dict[str, Any] | None = None,
    turn_deadline: float | None = 45.0,
    effective: dict[str, Any] | None = None,
) -> str:
    """The play page's signed snapshot; ``effective`` (an effective-config body, e.g.
    ``structured_v3()``) fills its gate fields (#260)."""
    parsed = Story.from_json(story or story_json())
    assert parsed is not None
    config = EffectiveConfig.from_json(effective) if effective is not None else None
    assert effective is None or config is not None
    return PlayStory.of(parsed, turn_deadline, config=config).signed()


def turn_data(**fields: Any) -> dict[str, Any]:
    """A turn POST: Mara says something, viewpoint the story default, everything else blank."""
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "snapshot": snapshot(),
        "actor": MARA,
        "say": "Is the ferry late again?",
        "do": "",
        "output_viewpoint": "default",
        "length": "",
        "length_custom": "",
        "temperature": "",
        "top_p": "",
        "recent_messages": "",
        "max_calls": "",
        "deadline_s": "",
    }
    data.update(fields)
    return data


def post_turn(client: Client, **fields: Any) -> str:
    response = client.post(reverse("stories:turn", args=[STORY]), turn_data(**fields), headers=HTMX)
    assert response.status_code == 200
    return response.content.decode()


def operation_json(status: str, *, result: Any = None, **changes: Any) -> dict[str, Any]:
    op: dict[str, Any] = copy.deepcopy(engine_fixture("get_operation"))
    op.update(
        id=QUEUED_OP,
        story_id=STORY,
        status=status,
        started_at="2099-01-01T00:00:00Z",
        committed_at=None if status != "committed" else "2099-01-01T00:00:01Z",
        idempotency_key=KEY,
        result=result,
    )
    op.update(changes)
    return op


def error_json(code: str, message: str = "refused", **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def calls(fake: FakeEngine, method: str | None = None, path: str | None = None) -> list[Any]:
    return [
        r
        for r in fake.requests
        if (method is None or r.method == method) and (path is None or r.url.path == path)
    ]


# --- the ingest tab (#51; shared with #260's tests) ------------------------------------------


def ingest_data(*rows: dict[str, str], **fields: Any) -> dict[str, Any]:
    """An ingest POST as the tab's form sends it."""
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "snapshot": snapshot(),
        "output_viewpoint": "default",
        "ingest-TOTAL_FORMS": str(len(rows)),
        "ingest-INITIAL_FORMS": "0",
        "ingest-MIN_NUM_FORMS": "1",
        "ingest-MAX_NUM_FORMS": "50",
    }
    for index, row in enumerate(rows):
        for name, value in row.items():
            data[f"ingest-{index}-{name}"] = value
    data.update(fields)
    return data


def post_ingest(
    client: Client, *rows: dict[str, str], htmx: bool = True, story: str = STORY, **fields: Any
) -> str:
    response = client.post(
        reverse("stories:ingest", args=[story]),
        ingest_data(*rows, **fields),
        headers=HTMX if htmx else {},
    )
    assert response.status_code == 200
    return response.content.decode()


def sent(engine: FakeEngine, story: str = STORY) -> dict[str, Any]:
    """The one ingest request's JSON body."""
    (request,) = calls(engine, "POST", f"/stories/{story}/ingest")
    body: dict[str, Any] = json.loads(request.content)
    return body
