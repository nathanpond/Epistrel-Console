"""#54 against the live Engine: the issue's demo on a fresh story. Queue with size 3 (marked
"story", its config change by ``console:nate``), reset it, pin a memory, save instructions, and
a story policy that would relax a rule refused with ``cannot_relax``. #197: player authority and
interaction mode set, read back as "story", and reset."""

import re
from typing import Any

import pytest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.ids import new_ulid
from tests.engine_panel.helpers import form_data
from tests.plugins.engine_live import live_key, live_url

pytestmark = [pytest.mark.engine_live, pytest.mark.engine_auth]


def _marker(html: str, name: str) -> str:
    match = re.search(
        rf'<tr class="setting setting-{name}">.*?<td class="marker marker-\w+">([^<]*)</td>',
        html,
        re.S,
    )
    assert match is not None, name
    return match.group(1)


def _create_story(client: EngineClient) -> str:
    ratings = client.list_content_ratings()
    created = client.create_story(
        {
            "name": "Console smoke settings",
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
    return story_id


def test_story_settings_demo_live(operator_client: Client) -> None:
    url, key = live_url(), live_key()
    client = EngineClient(url, key, principal="console:smoke")
    story_id = _create_story(client)
    page = f"/stories/{story_id}/settings/"
    try:
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            html = operator_client.get(page).content.decode()
            assert _marker(html, "turn_concurrency") == "default"
            data = form_data(html, page)
            data.update(turn_concurrency="queue", turn_queue_max="3")
            assert operator_client.post(page, data).status_code == 302

            html = operator_client.get(page).content.decode()
            assert _marker(html, "turn_concurrency") == "story"
            assert _marker(html, "turn_queue_max") == "story"
            assert re.search(
                r"<code>turn_concurrency</code></td>\s*<td>prospective</td>\s*<td class=\"diff\">"
                r"\s*<code>&quot;reject&quot;</code>\s*→\s*<code>&quot;queue&quot;</code>",
                html,
            )
            assert "console:nate" in html

            data = form_data(html, page)
            data.update(reset_turn_concurrency="on", reset_turn_queue_max="on")
            assert operator_client.post(page, data).status_code == 302
            html = operator_client.get(page).content.decode()
            assert _marker(html, "turn_concurrency") == "default"
            assert _marker(html, "turn_queue_max").startswith("default (")

            pinned = f"{page}pinned/"
            data = form_data(html, pinned)
            data.update({"pins-TOTAL_FORMS": "1", "pins-0-text": "Tobin owes Mara money."})
            assert operator_client.post(pinned, data).status_code == 302
            html = operator_client.get(page).content.decode()
            assert "Saved pinned memories — timeline revision" in html
            assert "Tobin owes Mara money.</textarea>" in html

            instructions = f"{page}instructions/"
            data = form_data(html, instructions)
            data["text"] = "Keep replies short."
            assert operator_client.post(instructions, data).status_code == 302
            html = operator_client.get(page).content.decode()
            assert "Saved custom instructions — timeline revision" in html

            policies = f"{page}policies/"
            posted: dict[str, Any] = form_data(html, policies)
            posted.update(
                name="Allow the war",
                applies_to=["text_output"],
                rule_type="wordlist",
                words="the war",
                message="Anything goes.",
                effect="allow",
            )
            refused = operator_client.post(policies, posted).content.decode()
            assert "A story policy can only add restrictions" in refused

            posted.update(name="No war", effect="deny", message="No war talk.")
            assert operator_client.post(policies, posted).status_code == 302
            html = operator_client.get(page).content.decode()
            assert "Added story policy “No war”" in html
            assert "<code>policy:" in html
    finally:
        client.delete_story(story_id, idempotency_key=new_ulid())


def test_player_authority_and_interaction_mode_live(operator_client: Client) -> None:
    url, key = live_url(), live_key()
    client = EngineClient(url, key, principal="console:smoke")
    story_id = _create_story(client)
    page = f"/stories/{story_id}/settings/"
    try:
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            html = operator_client.get(page).content.decode()
            assert _marker(html, "player_authority") == "default"
            assert _marker(html, "interaction_mode").startswith("default (")
            data = form_data(html, page)
            data.update(player_authority="co_author", interaction_mode="simultaneous")
            assert operator_client.post(page, data).status_code == 302

            html = operator_client.get(page).content.decode()
            assert (
                "Player authority: Co-author (may narrate others&#x27; observable actions), "
                "Interaction mode: Simultaneous" in html
            )
            assert _marker(html, "player_authority") == "story"
            assert _marker(html, "interaction_mode") == "story"

            data = form_data(html, page)
            data.update(reset_player_authority="on", reset_interaction_mode="on")
            assert operator_client.post(page, data).status_code == 302
            html = operator_client.get(page).content.decode()
            assert _marker(html, "player_authority") == "default"
            assert _marker(html, "interaction_mode").startswith("default (")
    finally:
        client.delete_story(story_id, idempotency_key=new_ulid())
