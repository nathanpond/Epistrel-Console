"""The Outbox section's row wording (#325, ``console/outbox.py``), from inline dicts (E16)."""

from typing import Any

import pytest
from django.test import override_settings

from console.engine.models import OutboxRow
from console.forms.reconciliation import OutboxFilterForm, outbox_status_choices
from console.outbox import (
    IMPORTED_UNSETTLED_TITLE,
    NO_MORE_ROWS,
    NO_ROWS,
    NO_ROWS_MATCH,
    PAGE_SIZE,
    count_text,
    empty_text,
    outbox_row,
    unreadable_text,
)
from console.reconciliation import _outcome, outcome_cell

STORY = "01JM7ST0000000000000000001"
BOX = "01JM7XB0000000000000000001"
OTHER = "01JM7XB0000000000000000002"
RECONCILIATION = f"/stories/{STORY}/inspect/effects/reconciliation/"


def parsed(**changes: Any) -> OutboxRow:
    raw: dict[str, Any] = {
        "outbox_id": BOX,
        "provider": "bank",
        "status": "intended",
        "position": {"turn": 4, "variant": 1, "slot_k": 0, "beat": 2, "step": 0},
        "membership": "active",
        "created_at": "2026-11-04T11:00:00Z",
        "attempts": 1,
        "effect": {"state": "gold", "delta": 5},
    }
    raw.update(changes)
    row = OutboxRow.from_json(raw)
    assert row is not None
    return row


def test_a_null_turn_reads_a_dash() -> None:
    view = outbox_row(
        parsed(position={"turn": None, "variant": None, "slot_k": 0, "beat": 0, "step": 0}),
        STORY,
        set(),
    )
    assert view.position_text == "—"
    assert view.position_url is None
    assert '"turn": null' in view.position_json


def test_a_position_reads_c1() -> None:
    view = outbox_row(parsed(), STORY, set())
    assert view.position_text == "turn 4 · v1 · beat 2"
    assert view.position_url is not None
    assert "/history/" in view.position_url


def test_compensates_off_the_page_links_reconciliation() -> None:
    view = outbox_row(parsed(compensates=OTHER), STORY, {BOX})
    (link,) = view.compensation
    assert (link.label, link.outbox_id) == ("Compensates", OTHER)
    assert link.url == f"{RECONCILIATION}#outbox-{OTHER}"
    # On the page it is the row's own anchor.
    view = outbox_row(parsed(compensated_by=OTHER), STORY, {BOX, OTHER})
    assert [(c.label, c.url) for c in view.compensation] == [("Compensated by", f"#outbox-{OTHER}")]


def test_compensation_is_plain_while_reconciliation_is_unregistered() -> None:
    with override_settings(ROOT_URLCONF="tests.inspect.urls_world_no_sections"):
        view = outbox_row(parsed(compensates=OTHER), STORY, set())
    assert view.compensation[0].url is None


def test_an_unknown_membership_is_humanised() -> None:
    view = outbox_row(parsed(membership="half_pruned"), STORY, set())
    assert (view.membership, view.membership_text) == ("half_pruned", "Half pruned")
    view = outbox_row(parsed(status="later_status"), STORY, set())
    assert (view.status, view.status_text) == ("later_status", "Later status")
    assert view.imported_text is None


@pytest.mark.parametrize(
    ("attempts", "text"), [(0, "0 attempts"), (1, "1 attempt"), (1200, "1,200 attempts")]
)
def test_attempts(attempts: int, text: str) -> None:
    assert outbox_row(parsed(attempts=attempts), STORY, set()).attempts_text == text


def test_imported_unsettled_links_its_reconciliation_row() -> None:
    view = outbox_row(parsed(status="imported_unsettled"), STORY, set())
    assert view.imported_text == IMPORTED_UNSETTLED_TITLE
    assert view.imported_url == f"{RECONCILIATION}#outbox-{BOX}"


def test_a_redacted_row_keeps_ids_status_and_position_only() -> None:
    raw = {
        "outbox_id": BOX,
        "provider": "bank",
        "status": "failed",
        "position": {"turn": 1, "variant": 1, "slot_k": 0, "beat": 2, "step": 0},
        "membership": "active",
        "created_at": "2026-11-02T09:00:00Z",
        "redacted": True,
    }
    row = OutboxRow.from_json(raw)
    assert row is not None
    view = outbox_row(row, STORY, set())
    assert view.effect_text == "Redacted by an erasure"
    assert view.redacted
    assert (view.value, view.contingent_text, view.outcome, view.attempts_text) == (
        None,
        None,
        None,
        None,
    )
    assert view.compensation == ()
    assert view.effect_json == ""
    assert view.membership_text == "Active"
    # Redaction is the Engine's flag only: a row with no value isn't redacted.
    plain = outbox_row(parsed(value=None), STORY, set())
    assert not plain.redacted
    assert plain.effect_text == "gold +5"


def test_the_outcome_cell_is_shared() -> None:
    assert _outcome is outcome_cell
    assert outcome_cell(None) is None


def test_wording() -> None:
    assert PAGE_SIZE == 50
    assert count_text(1, filtered=False) == "1 outbox row on this page"
    assert count_text(3, filtered=True) == "3 outbox rows on this page, matching filters"
    assert unreadable_text(1) == "1 outbox row couldn't be read — see More"
    assert unreadable_text(2) == "2 outbox rows couldn't be read — see More"
    assert empty_text(cursor=BOX, filtered=True) == NO_MORE_ROWS
    assert empty_text(cursor=None, filtered=True) == NO_ROWS_MATCH
    assert empty_text(cursor=None, filtered=False) == NO_ROWS


def test_the_filter_form() -> None:
    form = OutboxFilterForm({"status": " later_status ", "provider": "  Bank "})
    assert form.is_valid()
    assert form.query() == {"status": "later_status", "provider": "Bank"}  # trimmed, case kept
    assert outbox_status_choices("later_status")[-1] == ("later_status", "Later status")
    assert outbox_status_choices("confirmed") == outbox_status_choices()
    form = OutboxFilterForm({"status": "s" * 65})
    assert not form.is_valid()
    assert "status" in form.errors
    blank = OutboxFilterForm({"status": "", "provider": " "})
    assert blank.is_valid()
    assert blank.query() == {}
    assert not blank.filtered
