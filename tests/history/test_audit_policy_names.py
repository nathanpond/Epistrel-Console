"""Audit records name their policies from ``GET /stories/{id}/settings`` (#52): a ``null`` name
(a pending catalog erasure) shows "(name withheld — erasure pending)" with the raw id."""

import pytest
from django.test import Client

from console.engine.models import HistoryItem, StorySettings
from console.history import NAME_WITHHELD, audit_record, policy_names
from tests.fakes.engine import FakeEngine
from tests.history.helpers import SETTINGS, answer, page, policy, settings_json

pytestmark = pytest.mark.django_db
AUDIT = "viewpoint=omniscient&include_audit=1"


def test_a_null_name_is_withheld_with_the_raw_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, settings=settings_json(effective_policies=[policy("no_stub52", None)]))
    html = page(operator_client, AUDIT)
    assert f"{NAME_WITHHELD} <code>no_stub52</code>" in html
    # Complement: the payload's own name is not shown in its place.
    assert "no stub <code>no_stub52</code>" not in html


def test_a_named_policy_shows_its_name(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, AUDIT)
    assert "no stub <code>no_stub52</code>" in html
    assert NAME_WITHHELD not in html


def test_an_unresolved_policy_shows_the_raw_id(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, settings=settings_json(effective_policies=[]))
    html = page(operator_client, AUDIT)
    assert "<li><code>no_stub52</code> — Stub generations are off-limits</li>" in html


def test_a_failed_settings_read_falls_back_to_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond(
        "GET",
        SETTINGS,
        503,
        json={"error": {"code": "database_unavailable", "message": "down", "details": {}}},
    )
    html = page(operator_client, AUDIT)
    assert "Policy names unavailable — showing ids." in html
    assert "<li><code>no_stub52</code> — Stub generations are off-limits</li>" in html


def test_policy_names_take_the_first_effective_entry() -> None:
    settings = StorySettings.from_json(
        settings_json(
            effective_policies=[policy("p", "first"), policy("p", "second")],
            inactive_policies=[policy("p", "inactive"), policy("q", None)],
        )
    )
    assert settings is not None
    assert policy_names(settings) == {"p": "first", "q": None}


def test_an_erasure_marker_shows_its_raw_text() -> None:
    marker = HistoryItem.from_json(
        {
            "turn": None,
            "variant": None,
            "beat": None,
            "kind": "erasure_marker",
            "actor_character_id": None,
            "render_character_ids": None,
            "channel": "audit",
            "text": "[erasure marker: subject-1, story]",
            "operation_id": "01M5R5XWJH0Y9SEDSFMZEG2SFF",
            "message_id": "01M5R5XWK4AD648CN1TXWRXFR4",
            "origin": "generated",
        }
    )
    assert marker is not None
    record = audit_record(marker, {})
    assert record.raw == "[erasure marker: subject-1, story]"
    assert record.draft is None
    assert record.policy_line == "erasure_marker"
