"""#235: D10\u2013D13 and D19 through ``console/effects.py``: State-change lines, outbox ids, the
reconciliation count, staged-turn markers and the links to routes later stories register."""

from typing import Any

import pytest
from django.test import override_settings
from django.urls import NoReverseMatch, reverse

from console.effects import (
    DISPATCHED_NOTE,
    INCOMPLETE_TEXT,
    INCOMPLETE_TITLE,
    OUTBOX_LIST_NOTICE,
    OUTBOX_LIST_REQUEST,
    STAGED_LABEL,
    effect_line,
    engine_operation_url,
    outbox_list_notice_html,
    outbox_url,
    provider_url,
    reconciliation_link,
    reconciliation_text,
    stage_revision_text,
)
from console.engine.models import StateEffect

STORY = "01JM6ST0000000000000000001"
ALICE = "01JM6CH0000000000000000002"
NAMES = {ALICE: "Alice"}
OUTBOX = "01JM6BX0000000000000000001"
EFFECT_ID = "01JM6BX0000000000000000009"
OPERATION = "01JM6QP0000000000000000011"
STUB_URLS = "tests.urls_with_engine_operation"
WITHOUT_ENGINE_OPERATION = "tests.urls_without_engine_operation"

BASE: dict[str, Any] = {
    "state": "gold",
    "scope_key": None,
    "reason": "Paid the harbour fee",
    "source": "turn",
    "contingency": "unconditional",
    "status": "committed",
}


def effect(**changes: Any) -> StateEffect:
    parsed = StateEffect.from_json({**BASE, **changes})
    assert parsed is not None
    return parsed


def test_committed_set() -> None:
    line = effect_line(effect(old_value=120, new_value=125), NAMES, {"gold": "gold"})
    assert line.state == "gold"
    assert line.scope == "—"
    assert line.change == "120 gold → 125 gold"
    assert line.status == "Committed"
    assert line.contingency == ""
    assert line.notes == ()
    assert line.outbox_id is None
    assert line.more == ()


def test_held_reservation() -> None:
    line = effect_line(
        effect(status="held", contingency="reservation", scope_key=ALICE, delta=-5), NAMES
    )
    assert (line.status, line.contingency) == ("Held", "Reservation")
    assert line.scope == "Alice"
    assert line.change == "\u22125"  # a delta with no values: the signed delta alone


def test_pending_external() -> None:
    line = effect_line(
        effect(status="pending", owner="external", effect_id=EFFECT_ID, old_value=10), NAMES
    )
    assert "requested of the consumer" in line.notes
    assert line.more == (("Effect id", EFFECT_ID),)


def test_dispatched_outbox() -> None:
    line = effect_line(effect(outbox_id=OUTBOX, old_value=1, new_value=2), NAMES)
    assert DISPATCHED_NOTE == "dispatched · outbox"
    assert DISPATCHED_NOTE in line.notes
    assert line.outbox_id == OUTBOX
    assert all(OUTBOX not in note for note in line.notes)
    assert line.more == ()  # only an external owner carries its effect id


def test_capped_transfer_notes_in_order() -> None:
    line = effect_line(
        effect(
            capped=True,
            declared=200,
            kind="transfer_out",
            counterparty="harbour_master",
            owner="external",
            effect_id=EFFECT_ID,
            outbox_id=OUTBOX,
            delta=-150,
            old_value=150,
            new_value=0,
        ),
        NAMES,
        {"gold": "gold"},
    )
    assert line.notes == (
        "capped from 200 gold",
        "transfer out",
        "other side Harbour master",
        "requested of the consumer",
        DISPATCHED_NOTE,
    )
    assert line.change == "\u2212150 gold (150 gold → 0 gold)"


def test_time_advance() -> None:
    line = effect_line(effect(state="world_clock", kind="time_advance", minutes=30), NAMES)
    assert line.notes == ("+30 min requested",)


def test_reason_is_verbatim() -> None:
    line = effect_line(effect(reason="Mara paid_the fee — in full"), NAMES)
    assert line.reason == "Mara paid_the fee — in full"


def test_unknown_status_is_humanised() -> None:
    line = effect_line(effect(status="later_status", contingency="later_kind"), NAMES)
    assert line.status == "Later status"
    assert line.contingency == "Later kind"
    assert effect_line(effect(status=""), NAMES).status == "—"


def test_types_inferred_without_units() -> None:
    assert effect_line(effect(old_value=False, new_value=True), NAMES).change == ("false → true")
    assert effect_line(effect(old_value="open", new_value="shut"), NAMES).change == ("open → shut")


@pytest.mark.parametrize(
    ("count", "text"),
    [(None, None), (0, None), (1, "1 effect to reconcile"), (3, "3 effects to reconcile")],
)
def test_reconciliation_text(count: int | None, text: str | None) -> None:
    assert reconciliation_text(count) == text


def test_reconciliation_link() -> None:
    with override_settings(ROOT_URLCONF=STUB_URLS):
        url = reverse("inspect:reconciliation", args=[STORY])
        assert reconciliation_link(STORY, 0) is None
        assert reconciliation_link(STORY, None) is None
        assert reconciliation_link(STORY, 1) == ("1 effect to reconcile", url)
        assert reconciliation_link(STORY, 3) == (reconciliation_text(3), url)


def test_outbox_list_notice() -> None:
    html = outbox_list_notice_html()
    assert OUTBOX_LIST_REQUEST == "https://github.com/nathanpond/Epistrel/issues/679"
    assert html == (
        "Outbox rows are shown where the Engine reports them; there is no list of every row yet "
        f'(<a href="{OUTBOX_LIST_REQUEST}">Engine request (outbox list)</a>)'
    )
    assert "target" not in html
    assert "{link}" in OUTBOX_LIST_NOTICE


def test_d19_unregistered_links_are_none() -> None:
    # #250 registered ``operations:engine``: a urlconf lacking it pins the unlinked case.
    with override_settings(ROOT_URLCONF=WITHOUT_ENGINE_OPERATION):
        with pytest.raises(NoReverseMatch):
            reverse("operations:engine", args=[OPERATION])
        assert engine_operation_url(OPERATION) is None
    # The real urlconf links it (#250), and #240 registered ``engine:provider``, #249
    # ``inspect:reconciliation``.
    assert engine_operation_url(OPERATION) == f"/operations/engine/{OPERATION}/"
    reconciliation = f"/stories/{STORY}/inspect/effects/reconciliation/"
    assert outbox_url(STORY, OUTBOX) == f"{reconciliation}#outbox-{OUTBOX}"  # registered by #249
    assert reconciliation_link(STORY, 3) == ("3 effects to reconcile", reconciliation)
    assert provider_url("bank") == "/engine/providers/bank/"  # registered by #240


def test_d19_linked_with_the_stub_urlconf() -> None:
    with override_settings(ROOT_URLCONF=STUB_URLS):
        assert outbox_url(STORY, OUTBOX) == (
            f"{reverse('inspect:reconciliation', args=[STORY])}#outbox-{OUTBOX}"
        )
        assert reconciliation_link(STORY, 3) == (
            "3 effects to reconcile",
            reverse("inspect:reconciliation", args=[STORY]),
        )
        assert engine_operation_url(OPERATION) == f"/operations/engine/{OPERATION}/"
        assert provider_url("bank") == "/engine/providers/bank/"
        # Complement: malformed input stays unlinked.
        assert engine_operation_url("x") is None
        assert provider_url("") is None
        assert provider_url(None) is None
        assert outbox_url(STORY, "") is None


def test_staged_markers() -> None:
    assert INCOMPLETE_TEXT == "Incomplete"
    assert INCOMPLETE_TITLE == "A staged turn still waiting on its external outcome"
    assert STAGED_LABEL == "Staged turn"
    assert stage_revision_text(1200) == "revision 1200"
    assert stage_revision_text(None) == "—"
    assert stage_revision_text(True) == "—"
