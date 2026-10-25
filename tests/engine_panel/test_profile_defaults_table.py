"""The deployment's profile defaults (#50): a read-only table of dotted paths (M3 #84 edits)."""

import re
from typing import Any

from django.test import Client

from console.forms.deployment import profile_default_rows
from tests.engine_panel.helpers import answer_settings, settings_body
from tests.fakes.engine import FakeEngine

URL = "/engine/settings/"
NESTED = {
    "budgets": {"model_calls": None, "deadline_ms": 45000},
    "models": {
        "generation": {"response_length": "medium", "temperature": 0.8},
        "purposes": {"generation": {"model": None}, "lite_summary": {"model": "small"}},
    },
    "clock": {"scheduled_stops": [], "level": "off"},
    "pipeline": {},
    "pins": {"enabled": True},
}
LEAVES: list[tuple[str, Any]] = [
    ("budgets.model_calls", None),
    ("budgets.deadline_ms", 45000),
    ("models.generation.response_length", "medium"),
    ("models.generation.temperature", 0.8),
    ("models.purposes.generation.model", None),
    ("models.purposes.lite_summary.model", "small"),
    ("clock.scheduled_stops", []),
    ("clock.level", "off"),
    ("pipeline", {}),
    ("pins.enabled", True),
]


def test_every_leaf_flattens_to_a_dotted_path_in_engine_order() -> None:
    assert profile_default_rows(NESTED) == LEAVES
    assert profile_default_rows(None) == []


def test_the_table_shows_every_leaf_read_only(operator_client: Client, engine: FakeEngine) -> None:
    """The recorded settings' own profile defaults (the schema allows no other shape)."""
    answer_settings(engine)
    leaves = profile_default_rows(settings_body()["profile_defaults"])

    html = operator_client.get(URL).content.decode()

    section = re.search(r'<section class="profile-defaults">.*?</section>', html, re.S)
    assert section is not None
    table = section.group(0)
    assert table.count("<tr><td>") == len(leaves) > 40
    for path, _ in leaves:
        assert f"<tr><td><code>{path}</code></td>" in table
    # null is a value; maps (models.purposes) flatten by key; strings are JSON.
    assert "<tr><td><code>budgets.model_calls</code></td><td><code>null</code></td></tr>" in table
    assert (
        "<tr><td><code>models.purposes.lite_summary.model</code></td><td><code>null</code></td>"
    ) in table
    assert (
        "<tr><td><code>models.generation.response_length</code></td>"
        "<td><code>&quot;medium&quot;</code></td></tr>"
    ) in table
    assert "<tr><td><code>pins.enabled</code></td><td><code>true</code></td></tr>" in table
    # Engine order.
    assert table.index("<code>pins.enabled</code>") < table.index("<code>memory.hot_set.")
    # The editor is M3 #84's: plain text, not a link.
    assert "Edit profile defaults: <code>/engine/profile-defaults/edit/</code>" in table
    assert "<a " not in table


def test_the_page_offers_no_write_control_for_profile_defaults(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_settings(engine)

    html = operator_client.get(URL).content.decode()

    section = re.search(r'<section class="profile-defaults">.*?</section>', html, re.S)
    assert section is not None
    for control in ("<input", "<textarea", "<select", "<form", "<button"):
        assert control not in section.group(0)
    assert 'name="profile_defaults' not in html
