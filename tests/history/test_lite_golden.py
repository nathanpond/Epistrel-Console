"""#147's byte-identity complement: a Lite history page and play transcript render exactly the
markup they rendered before per-beat traces existed (no tags, marks, Trace or legend).

The goldens in ``tests/fixtures/inspect/golden/`` were rendered from the pre-#147 templates with
this module's own extraction, from ``lite_history.json`` (a hand-written, schema-valid history
body: say + do of one message, a multi-line reply, a quarantined input with ``<script>`` in it,
a redacted reply, an ingested turn)."""

import json
from typing import Any

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.history.helpers import HISTORY, STORY, STORY_PATH, VARIANTS, variants_json
from tests.inspect.helpers import FIXTURES
from tests.play.helpers import answer_page, story_json

pytestmark = pytest.mark.django_db

GOLDEN = FIXTURES / "golden"


def lite_history() -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / "lite_history.json").read_text())
    return body


def golden(name: str) -> str:
    return (GOLDEN / name).read_text()


def items_markup(client: Client, engine: FakeEngine, query: str = "viewpoint=omniscient") -> str:
    """The history page's items section (everything between the controls and the variants)."""
    engine.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=4))
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, 200, json=lite_history())
    html = client.get(f"/stories/{STORY}/history/?{query}").content.decode()
    return html.split('<section class="items">', 1)[1].split('<section class="variants"', 1)[0]


def transcript_markup(client: Client, engine: FakeEngine) -> str:
    """The play page's ``#transcript`` div, turn controls included."""
    answer_page(engine, history=lite_history())
    html = client.get(f"/stories/{STORY}/").content.decode()
    return html.split('<div id="transcript">', 1)[1].split('<div id="turn-status"', 1)[0]


def test_lite_history_items_are_byte_identical(operator_client: Client, engine: FakeEngine) -> None:
    assert items_markup(operator_client, engine) == golden("lite_history_items.html")


def test_lite_transcript_is_byte_identical(operator_client: Client, engine: FakeEngine) -> None:
    assert transcript_markup(operator_client, engine) == golden("lite_transcript.html")


def test_the_goldens_hold_the_fixture() -> None:
    """The goldens aren't empty: they hold every fixture item, escaped."""
    for name in ("lite_history_items.html", "lite_transcript.html"):
        text = golden(name)
        assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in text
        assert "Rain on the harbour." in text
        for absent in ("beat-text", "beat-trace", "verdict-legend", "<mark title"):
            assert absent not in text
