"""How history items render (#52): per-turn groups, channels, "no reply", operation links,
audit records with their rejected drafts and the summary."""

import json
import re
from typing import Any

import pytest
from django.test import Client

from console.inspect import short_id
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
from tests.play.helpers import story_json

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
    found = [row for row in rows if f'<td class="text">{text}' in row]
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


# --- the Perception link (#195) -------------------------------------------------------------------

EFFECTIVE = "/stories/{story_id}/effective-config"


def perception_links(html: str) -> list[str]:
    found = re.findall(r'<a class="perception-link" href="([^"]+)">Perception</a>', html)
    return [h.replace("&amp;", "&") for h in found]


def effective_reads(engine: FakeEngine) -> int:
    return calls_to(engine, "/effective-config")


def _predicates() -> dict[str, Any]:
    from tests.inspect.helpers import FIXTURES

    body: dict[str, Any] = json.loads((FIXTURES / "predicates_list.json").read_text())
    return body


def _structured_turns() -> dict[str, Any]:
    items = [
        item(0, 1, "say", "Setup.", propositions=[]),
        item(1, 1, "say", "Is the ferry late?", propositions=[]),
        item(1, 2, "message", "Tobin shrugs."),
        item(2, 1, "say", "Fine.", kind="beat_say", fidelity="envelope"),  # fidelity alone
    ]
    return history_json(items=items, turns=turns(0, 1, 2), audit=None)


def test_structured_turn_links_perception(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=_structured_turns())
    engine.respond("GET", "/stories/{story_id}/predicates", 200, json=_predicates())
    html = page(operator_client)
    assert perception_links(html) == [
        f"/stories/{STORY}/turns/1/perception/",
        f"/stories/{STORY}/turns/2/perception/",
    ]
    assert effective_reads(engine) == 0  # the items say
    (header,) = [line for line in html.splitlines() if "turn-header" in line and "Turn 1 ·" in line]
    assert header.index("Explain retrieval") < header.index('class="perception-link"')
    engine.reset()
    chain = perception_links(page(operator_client, "variant=1:2"))
    assert chain == [
        f"/stories/{STORY}/turns/1/perception/?variant=1%3A2",
        f"/stories/{STORY}/turns/2/perception/?variant=1%3A2",
    ]


def test_do_only_turn_links_perception_when_story_structured(
    operator_client: Client, engine: FakeEngine
) -> None:
    from tests.inspect.helpers import structured

    items = [item(1, 1, "do", "Mara sits."), item(1, 2, "message", "Tobin shrugs.")]
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None))
    engine.respond("GET", EFFECTIVE, 200, json=structured())
    assert perception_links(page(operator_client)) == [f"/stories/{STORY}/turns/1/perception/"]
    assert effective_reads(engine) == 1
    engine.reset()
    assert perception_links(page(operator_client, "variant=1:2")) == []  # a chain view
    assert effective_reads(engine) == 0


def test_lite_turn_has_no_perception_link(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)  # the Lite fixture, on a Lite story
    html = page(operator_client)
    assert "Turn 1 · variant 1" in html
    assert perception_links(html) == []
    assert effective_reads(engine) == 1  # one lazy read for the whole page


# --- #202: a multi-render turn's render order; reactions indented ---------------------------------

MARCUS = "01JB2Z8Y5Q3W6K9M1N4P7R0T3D"
JENNI = "01JB2Z8Y5Q3W6K9M1N4P7R0T3C"
STORY_PATH = "/stories/{story_id}"


def demo_cast(engine: FakeEngine, items: list[dict[str, Any]]) -> None:
    """#52's reads with ``items`` as turn 1, and Marcus and Jenni in the cast (the #201 demo)."""
    answer(engine, history=history_json(items=items, turns=turns(1), audit=None, summary=None))
    story = story_json(next_turn_index=4)
    story["characters"] = [
        *story["characters"],
        {"id": MARCUS, "name": "Marcus", "is_player": False},
        {"id": JENNI, "name": "Jenni", "is_player": False},
    ]
    engine.respond("GET", STORY_PATH, 200, json=story)


def header(html: str) -> str:
    match = re.search(r'<tr class="turn-header"><th colspan="7">(Turn 1 .*?)</th></tr>', html)
    assert match is not None
    return match.group(1)


def test_multi_render_turn_header_names_the_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    outsider = "01JB2Z8Y5Q3W6K9M1N4P7R0ZZ9"
    items = [
        item(1, 1, "say", "Anyone here?"),
        item(
            1,
            2,
            "message",
            "Marcus and Jenni answer.",
            render_character_ids=[MARCUS, JENNI, MARCUS, outsider],
        ),
        item(1, 3, "message", "A later reply.", render_character_ids=[JENNI, MARCUS]),
    ]
    demo_cast(engine, items)
    html = header(page(operator_client))
    assert html.startswith(
        'Turn 1 · variant 1 · <span class="render-order">Renders: Marcus → Jenni → '
        f"{short_id(outsider)}</span>"
    )
    assert html.count("render-order") == 1  # the group's first message_rendered row only


def test_single_render_turn_header_has_no_renders_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: one rendering character (a duplicate id counts once) gives no line; the
    Render column is unchanged."""
    items = [
        item(1, 1, "say", "Hello."),
        item(1, 2, "message", "Marcus answers.", render_character_ids=[MARCUS, MARCUS]),
    ]
    demo_cast(engine, items)
    html = page(operator_client)
    assert "Renders:" not in html
    assert 'class="render-order"' not in html
    assert "<td>Marcus, Marcus</td>" in row_of(html, "Marcus answers.")


def test_reaction_rows_are_indented(operator_client: Client, engine: FakeEngine) -> None:
    items = [
        item(1, 1, "say", "Anyone here?"),
        item(
            1,
            2,
            "say",
            "Jenni gasps.",
            actor_character_id=JENNI,
            narrated_by=MARCUS,
            kind="beat_say",
        ),
        item(1, 3, "say", "Mara shrugs.", narrated_by="player", kind="beat_say"),
        item(1, 4, "message", "Marcus answers.", render_character_ids=[MARCUS, JENNI]),
    ]
    demo_cast(engine, items)
    html = page(operator_client)
    reaction = row_of(html, "↳ Jenni gasps.")
    assert re.match(r'<tr class="item channel-say reaction">', reaction)
    assert "<td>Jenni (narrated by Marcus)</td>" in reaction  # #62's label unchanged
    # Complement: a player-narrated beat (C16) and an own beat are neither marked nor prefixed.
    for text in ("Mara shrugs.", "Anyone here?"):
        row = row_of(html, text)
        assert "reaction" not in row
        assert "↳" not in row


def test_turn_header_links_report(operator_client: Client, engine: FakeEngine) -> None:
    """#203: a turn whose chosen item (its reply, else its last item) was generated links its
    play-page inspector, last in the header. **Complement:** an ingested turn has no link."""
    items = [
        item(1, 1, "say", "Anyone here?"),
        item(1, 2, "message", "Marcus answers.", render_character_ids=[MARCUS, JENNI]),
    ]
    demo_cast(engine, items)
    found = header(page(operator_client))
    url = f"/stories/{STORY}/?operation={OP}&amp;origin=generated"
    assert found.endswith(f' <a class="report-link" href="{url}">Report</a>')
    engine.reset()
    ingested = [
        item(1, 1, "say", "Anyone here?", origin="ingested"),
        item(1, 2, "message", "Tobin answers.", origin="ingested"),
    ]
    demo_cast(engine, ingested)
    html = page(operator_client)
    assert "report-link" not in header(html)
    assert "Report</a>" not in html
