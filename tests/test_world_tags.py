"""#235: the ``world_tags`` filters (D5, D6, D11): ``outbox_id``, ``clock_value`` and
``state_value``."""

from typing import Any

from django.template import engines

from console.engine.models import ClockValue, StoryState

OUTBOX = "01JM6BX0000000000000000001"


def render(source: str, **context: Any) -> str:
    return engines["django"].from_string("{% load world_tags %}" + source).render(context)


def state(**changes: Any) -> StoryState:
    body = {
        "name": "gold",
        "type": "number",
        "scope": "story",
        "visibility": "public",
        "owner": "story",
        "unit": "gold",
        "since_position": None,
        **changes,
    }
    parsed = StoryState.from_json(body)
    assert parsed is not None
    return parsed


def test_outbox_id_in_full() -> None:
    assert len(OUTBOX) == 26
    html = render("{{ id|outbox_id }}", id=OUTBOX)
    assert html == f'<code class="outbox-id">{OUTBOX}</code>'
    assert render("{{ id|outbox_id }}", id="<x>") == '<code class="outbox-id">&lt;x&gt;</code>'
    assert render("{{ id|outbox_id }}", id=None) == "—"


def test_clock_value() -> None:
    html = render("{{ c|clock_value }}", c=ClockValue(offset=130, display="+02h 10m"))
    assert html == '<span class="clock" title="offset 130 min">+02h 10m</span>'


def test_state_value() -> None:
    gold = state()
    assert render("{{ v|state_value:s }}", v=None, s=gold) == (
        '<span class="undefined">undefined</span>'
    )
    # Complement: a defined value has no span.
    assert render("{{ v|state_value:s }}", v=1200, s=gold) == "1,200 gold"
    assert render("{{ v|state_value:s }}", v="<b>", s=state(type="text", unit=None)) == "&lt;b&gt;"
    assert render("{{ v|state_value:s }}", v=True, s=None) == "true"
    assert render("{{ v|state_value }}", v=5) == "5"
