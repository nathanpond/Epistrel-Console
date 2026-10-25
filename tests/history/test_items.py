"""How history items render (#52): per-turn groups, channels, "no reply", operation links,
audit records with their rejected drafts and the summary."""

import re
from typing import Any

import pytest
from django.test import Client

from console.models import EngineOperation
from tests.fakes.engine import FakeEngine
from tests.history.helpers import (
    MARA,
    STORY,
    TOBIN,
    TURN3_OP,
    answer,
    calls_to,
    history_json,
    page,
)

pytestmark = pytest.mark.django_db
OP = "01M5R5XK5X7GQK27XMD1FZQV27"  # turn 1's operation in the fixture
DASH = "\N{EN DASH}"  # the summary label's turn range


def item(turn: int, beat: int, channel: str, text: str, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "turn": turn,
        "variant": 1,
        "beat": beat,
        "kind": "message_rendered" if channel == "message" else "player_input",
        "actor_character_id": None if channel == "message" else MARA,
        "render_character_ids": [TOBIN] if channel == "message" else None,
        "channel": channel,
        "text": text,
        "operation_id": OP,
        "message_id": f"01M5R5XK6GZ8J6WBVVD48H5B{turn}{beat}",
        "origin": "generated",
    }
    body.update(changes)
    return body


def turns(*numbers: int) -> list[dict[str, Any]]:
    return [{"turn": n, "variant": 1, "message": None} for n in numbers]


def row_of(html: str, text: str) -> str:
    rows: list[str] = re.findall(r'<tr class="item[^"]*">.*?</tr>', html, re.S)
    found = [row for row in rows if f"<td>{text}" in row]
    assert len(found) == 1, text
    return found[0]


def test_items_group_under_turn_headers(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient")
    for turn in (1, 2, 3):
        assert f"Turn {turn} · variant 1" in html
    assert html.index("Turn 1 · variant 1") < html.index("Turn 2 · variant 1")
    say = row_of(html, "Is the ferry late?")
    assert "<td>Mara</td>" in say
    assert "<code>player_input</code>" in say
    reply = row_of(html, "Tobin changes the subject.")
    assert "<td>—</td>" in reply  # no actor
    assert "<td>Tobin</td>" in reply  # render character


def test_think_and_latent_render_distinctly(operator_client: Client, engine: FakeEngine) -> None:
    items = [
        item(1, 1, "say", "Hello there."),
        item(1, 2, "think", "She is hiding something.", kind="beat"),
        item(1, 3, "latent", "A storm is coming.", kind="beat"),
        item(1, 4, "message", "Tobin nods."),
    ]
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None, summary=None))
    html = page(operator_client)
    think = row_of(html, "She is hiding something.")
    latent = row_of(html, "A storm is coming.")
    say = row_of(html, "Hello there.")
    assert 'class="item channel-think"' in think
    assert '<span class="badge-channel">think</span>' in think
    assert 'class="item channel-latent"' in latent
    assert '<span class="badge-channel">latent</span>' in latent
    # Complement: an ordinary beat has neither class.
    assert "channel-think" not in say
    assert "channel-latent" not in say
    assert '<span class="badge-channel">say</span>' in say


def test_a_reply_less_turn_says_no_reply(operator_client: Client, engine: FakeEngine) -> None:
    items = [
        item(1, 1, "say", "First."),
        item(1, 2, "message", "A reply."),
        item(2, 1, "say", "Ingested, no reply.", origin="ingested"),
    ]
    answer(engine, history=history_json(items=items, turns=turns(1, 2), audit=None))
    html = page(operator_client)
    assert html.count('<span class="no-reply">no reply</span>') == 1
    assert 'Turn 2 · variant 1 <span class="no-reply">no reply</span>' in html
    assert 'Turn 1 · variant 1 <span class="no-reply">' not in html


def test_narration_counts_as_a_reply_and_turn_zero_is_exempt(
    operator_client: Client, engine: FakeEngine
) -> None:
    items = [
        item(0, 1, "narration", "The harbour at dawn.", kind="narration", actor_character_id=None),
        item(1, 1, "narration", "Rain.", kind="narration", actor_character_id=None),
    ]
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None))
    assert "no reply" not in page(operator_client)
    answer(engine, history=history_json(items=[item(0, 1, "say", "Genesis.")], turns=[]))
    assert "no reply" not in page(operator_client)


def test_no_reply_is_suppressed_with_an_actor_filter(
    operator_client: Client, engine: FakeEngine
) -> None:
    items = [item(1, 1, "say", "Only Mara's line.")]
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None))
    assert "no reply" not in page(operator_client, f"actor_character_id={MARA}")
    assert "no reply" in page(operator_client)


def test_say_and_do_sharing_a_message_are_two_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    shared = "01M5R5XK6GZ8J6WBVVD48H5BZZ"
    items = [
        item(1, 1, "say", "Hello.", message_id=shared),
        item(1, 1, "do", "Waves.", message_id=shared),
        item(1, 2, "message", "Tobin waves back."),
    ]
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None))
    html = page(operator_client)
    assert "same message" in row_of(html, "Waves.")
    assert "same message" not in row_of(html, "Hello.")


def test_unknown_render_ids_show_raw(operator_client: Client, engine: FakeEngine) -> None:
    stranger = "01M5R5XK6GZ8J6WBVVD48H5ZZZ"
    items = [item(1, 2, "message", "Who speaks?", render_character_ids=[stranger])]
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None))
    assert f"<td>{stranger}</td>" in row_of(page(operator_client), "Who speaks?")


def _log(**fields: Any) -> EngineOperation:
    base: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "outcome": EngineOperation.Outcome.OK,
        "story_id": STORY,
    }
    base.update(fields)
    return EngineOperation.objects.create(**base)


def test_operation_links_only_when_logged(operator_client: Client, engine: FakeEngine) -> None:
    _log(engine_operation_id=OP)
    newest = _log(engine_operation_id=OP)
    answer(engine)
    html = page(operator_client)
    link = f'<a class="operation-link" href="/operations/{newest.pk}/"><code>{OP}</code></a>'
    assert link in row_of(html, "Is the ferry late?")
    # Complement: turn 3's operation was never logged here — plain text, no link.
    third = row_of(html, "Third?")
    assert "operation-link" not in third
    assert f"<code>{TURN3_OP}</code>" in third


def test_an_ingest_row_links_every_operation_in_its_results(
    operator_client: Client, engine: FakeEngine
) -> None:
    batch = _log(
        kind="ingest",
        path=f"/stories/{STORY}/ingest",
        engine_operation_id="01M5R5XK5X7GQK27XMD1FZQ000",
        response={
            "results": [
                {"client_event_id": "a", "operation_id": "01M5R5XK5X7GQK27XMD1FZQ000"},
                {"client_event_id": "b", "operation_id": TURN3_OP},
            ]
        },
    )
    answer(engine)
    html = page(operator_client)
    assert f'href="/operations/{batch.pk}/"' in row_of(html, "Third?")


def test_audit_records_show_under_their_turn(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient&include_audit=1")
    assert calls_to(engine, "/settings") == 1
    turn3 = html[html.index("Turn 3 · variant 1") :]
    assert turn3.count('<div class="audit-record">') == 3
    assert "guardrail_output_rejected — no_stub52" in turn3
    assert "no stub <code>no_stub52</code> — Stub generations are off-limits" in turn3
    assert "<summary>Rejected draft</summary>" in turn3
    assert "[stub generation 9442fa3ca9fd] Mara: &quot;Third?&quot;" in turn3
    assert "output_policy, attempt 0" in turn3
    # Only under turn 3.
    assert '<div class="audit-record">' not in html[: html.index("Turn 3 · variant 1")]
    # Complement: audit records are never table rows of their own.
    assert 'class="item channel-audit"' not in html


def test_audit_off_page_turns_are_dropped(operator_client: Client, engine: FakeEngine) -> None:
    body = history_json()
    body["items"] = [i for i in body["items"] if i["turn"] < 3]
    body["turns"] = [t for t in body["turns"] if t["turn"] < 3]
    answer(engine, history=body)
    html = page(operator_client, "viewpoint=omniscient&include_audit=1")
    assert '<div class="audit-record">' not in html


def test_audit_inside_items_is_routed_to_the_audit_display(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = history_json()
    body["items"] = body["items"] + body.pop("audit")
    answer(engine, history=body)
    html = page(operator_client, "viewpoint=omniscient&include_audit=1")
    assert html.count('<div class="audit-record">') == 3
    assert 'class="item channel-audit"' not in html


def test_summary_lists_level_turns_and_text(operator_client: Client, engine: FakeEngine) -> None:
    body = history_json()
    body["summary"] = [
        {"level": 1, "covers_from": 3, "covers_to": 3, "text": "Later.", "operation_id": OP},
        {"level": 0, "covers_from": 1, "covers_to": 2, "text": "Earlier.", "operation_id": OP},
    ]
    answer(engine, history=body)
    html = page(operator_client, "include_summary=1")
    assert "<h2>Summary</h2>" in html
    first = html.index(f"<strong>Level 0 · turns 1{DASH}2</strong>: Earlier.")
    assert first < html.index(f"<strong>Level 1 · turns 3{DASH}3</strong>: Later.")
    assert html.index("<h2>Summary</h2>") < html.index('<table class="history">')


def test_no_summary_section_unless_asked(operator_client: Client, engine: FakeEngine) -> None:
    body = history_json()
    body.pop("summary")
    answer(engine, history=body)
    assert "<h2>Summary</h2>" not in page(operator_client)
