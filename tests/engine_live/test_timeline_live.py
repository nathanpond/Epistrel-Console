"""#53 against the live Engine (stub model adapter): the issue's demo — regenerate the head
turn, rewind, undo the rewind, a stale page refused, swap back to the old reply, and fork — all
through the Console's timeline pages."""

from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.ids import new_ulid
from tests.plugins.engine_live import live_key, live_url

pytestmark = [pytest.mark.engine_live, pytest.mark.engine_auth]


def _post(client: Client, path: str, rev: int, head: int, **data: str) -> Any:
    shown = {"rev": str(rev), "head": str(head), "name": "Harbour"}
    return client.post(
        f"{path}?{urlencode(shown)}", {"idempotency_key": new_ulid(), **shown, **data}
    )


def _flash(response: Any) -> str:
    return " ".join(str(m) for m in response.wsgi_request._messages)


def test_timeline_operations_live(operator_client: Client) -> None:
    url, key = live_url(), live_key()
    engine = EngineClient(url, key, principal="console:smoke")
    ratings = engine.list_content_ratings()
    created = engine.create_story(
        {
            "name": "Harbour",
            "content_rating_id": ratings[0].id,
            "characters": [
                {"name": "Mara", "definition": "A pilot.", "true_age": 34, "is_player": True},
                {"name": "Tobin", "definition": "Her brother.", "true_age": 41, "is_player": False},
            ],
        },
        idempotency_key=new_ulid(),
    )
    story_id = created.story_id
    assert story_id is not None
    mara = created.characters[0].id
    forks: list[str] = []
    try:
        for line in ("Is the ferry late?", "And the tide?", "Shall we go?"):
            engine.respond(
                story_id,
                {"actor_character_id": mara, "player_input": {"say": line}},
                idempotency_key=new_ulid(),
                prefer_async=False,
            )
        base = f"/stories/{story_id}"
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            story = engine.get_story(story_id)
            page = operator_client.get(f"{base}/").content.decode()
            assert f"{base}/regenerate/?" in page
            assert f"{base}/rewind/2/?" in page

            regenerated = _post(operator_client, f"{base}/regenerate/", story.timeline_revision, 3)
            assert regenerated.status_code == 302
            assert "Regenerated turn 3: variant 2 is active, previous variant 1" in _flash(
                regenerated
            )
            variants = engine.list_variants(story_id)
            assert {(c.turn, c.variant) for c in variants if c.turn == 3} == {(3, 1), (3, 2)}

            story = engine.get_story(story_id)
            stale = story.timeline_revision
            rewound = _post(operator_client, f"{base}/rewind/1/", stale, 3)
            assert "Rewound to turn 1" in _flash(rewound)
            assert engine.get_story(story_id).next_turn_index == 2

            refused = _post(operator_client, f"{base}/rewind/1/", stale, 3)  # the other tab
            assert refused.status_code == 200
            assert "The story changed since you loaded this page" in refused.content.decode()

            story = engine.get_story(story_id)
            assert story.undo_available
            undone = _post(operator_client, f"{base}/undo-rewind/", story.timeline_revision, 1)
            assert "Rewind undone" in _flash(undone)
            assert engine.get_story(story_id).next_turn_index == 4

            story = engine.get_story(story_id)
            swapped = _post(
                operator_client, f"{base}/swap/", story.timeline_revision, 3, chain="3:1"
            )
            assert "Swapped: chain turn 3 · variant 1 is active" in _flash(swapped)

            story = engine.get_story(story_id)
            forked = _post(
                operator_client,
                f"{base}/fork/2/",
                story.timeline_revision,
                3,
                new_name="Harbour (fork at turn 2)",
            )
            assert forked.status_code == 302
            new_id = forked["Location"].strip("/").split("/")[-1]
            forks.append(new_id)
            banner = operator_client.get(forked["Location"]).content.decode()
            assert "Forked from <em>" in banner
            assert "at turn 2" in banner
            assert engine.get_story(new_id).next_turn_index == 3
    finally:
        for fork_id in forks:
            engine.delete_story(fork_id, idempotency_key=new_ulid())
        engine.delete_story(story_id, idempotency_key=new_ulid())
