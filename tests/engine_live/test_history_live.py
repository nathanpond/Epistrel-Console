"""#52 against the live Engine (stub model adapter): browse a played story's history at a
character viewpoint, omniscient with audit and summary, as of turn 0, its variants, and the
Engine's 422 for an ``as_of`` beyond the head."""

import pytest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.ids import new_ulid
from tests.plugins.engine_live import live_key, live_url

pytestmark = [pytest.mark.engine_live, pytest.mark.engine_auth]


def test_browse_history_live(operator_client: Client) -> None:
    url, key = live_url(), live_key()
    client = EngineClient(url, key, principal="console:smoke")
    ratings = client.list_content_ratings()
    created = client.create_story(
        {
            "name": "Console smoke history",
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
    mara, tobin = (c.id for c in created.characters)
    try:
        client.respond(
            story_id,
            {"actor_character_id": mara, "player_input": {"say": "Is the ferry late?"}},
            idempotency_key=new_ulid(),
            prefer_async=False,
        )
        base = f"/stories/{story_id}/history/"
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            seen = operator_client.get(f"{base}?viewpoint=character:{tobin}").content.decode()
            omniscient = operator_client.get(
                f"{base}?viewpoint=omniscient&include_audit=1&include_summary=1"
            ).content.decode()
            genesis = operator_client.get(f"{base}?as_of=0").content.decode()
            beyond = operator_client.get(f"{base}?as_of=9").content.decode()
        assert "Turn 1 · variant 1" in seen
        assert "Is the ferry late?" in seen
        assert f"<code>character:{tobin}</code> · as of 1" in seen
        assert "membership-active" in seen  # the variants panel
        assert "<code>omniscient</code> · as of 1" in omniscient
        assert "<h2>Summary</h2>" in omniscient
        assert "Engine error" not in omniscient
        assert "as of 0" in genesis
        assert "Turn 1 · variant 1" not in genesis
        assert "Engine error 422 validation_error" in beyond
        assert '<select name="viewpoint"' in beyond
    finally:
        client.delete_story(story_id, idempotency_key=new_ulid())
