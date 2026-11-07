"""Reconciliation row wording (#249, ``console/reconciliation.py``), over inline listings: values
the schema-validating fake can't serve (a reason outside the enum)."""

from typing import Any

import pytest
from django.test import override_settings

from console.engine.models import ReconciliationListing, Story
from console.reconciliation import (
    REASON_TEXT,
    SETTLEABLE_REASONS,
    action_query,
    count_text,
    effect_text,
    listing_row,
    unreadable_text,
)

STORY = "01JM6ST0000000000000000001"
OUTBOX = "01JM6BX0000000000000000001"
MINUS = "\u2212"
ACTIONS = "tests.inspect.urls_reconciliation_actions"


def listing(**changes: Any) -> ReconciliationListing:
    body: dict[str, Any] = {
        "outbox_id": OUTBOX,
        "provider": "bank",
        "value": "balance",
        "status": "claimed",
        "reason": "late_success_after_expiry",
        "since": "2026-10-31T18:05:00Z",
        "effect": {"kind": "change", "state": "gold", "delta": -500, "reason": "buy sword"},
    }
    body.update(changes)
    parsed = ReconciliationListing.from_json({k: v for k, v in body.items() if v is not None})
    assert parsed is not None
    return parsed


def story(revision: int = 12) -> Story:
    from tests.fixtures import engine_fixture

    body = dict(engine_fixture("m6/story"))
    body["timeline_revision"] = revision
    parsed = Story.from_json(body)
    assert parsed is not None
    return parsed


@override_settings(ROOT_URLCONF=ACTIONS)
def test_an_unknown_reason_is_humanised_without_explanation_or_actions() -> None:
    row = listing_row(listing(reason="later_reason"), STORY, set())
    assert (row.reason_text, row.explanation) == ("Later reason", "")
    assert "later_reason" not in REASON_TEXT
    assert "later_reason" not in SETTLEABLE_REASONS
    assert row.actions == ()
    # **Complement:** a settleable reason under the same urlconf gets both.
    assert [a.label for a in listing_row(listing(), STORY, set()).actions] == [
        "Settle now",
        "Compensate",
    ]


@pytest.mark.parametrize(
    ("effect", "text"),
    [
        ({"state": "gold", "delta": -500, "reason": "buy sword"}, f"gold {MINUS}500 — “buy sword”"),
        ({"state": "gold", "delta": 2.5}, "gold +2.5"),
        ({"state": "item", "value": "sword"}, 'item "sword"'),
        ({"state": "flag", "value": True, "reason": "  "}, "flag true"),
        ({"state": "gold", "delta": 5, "value": 9}, "gold +5"),  # delta wins
        ({"state": "gold", "delta": "lots"}, 'gold "lots"'),  # non-numeric delta: JSON
        ({"state": "gold"}, "gold"),
        ({"delta": -5, "reason": "no state"}, "—"),
        ({"state": 3, "delta": -5}, "—"),
        (["not", "a", "dict"], "—"),
        (None, "—"),
    ],
)
def test_effect_text_cases(effect: Any, text: str) -> None:
    assert effect_text(effect) == text


def test_action_query_without_a_story_read_omits_expected_revision() -> None:
    query = action_query(listing(), None, "/next#outbox-1", with_state=True)
    assert "expected_revision" not in query
    assert query == {
        "provider": "bank",
        "value": "balance",
        "listed_reason": "late_success_after_expiry",
        "state": "gold",
        "next": "/next#outbox-1",
    }
    # **Complement:** with the story read it carries its revision; Settle now has no state.
    with_story = action_query(listing(), story(), "/next")
    assert with_story["expected_revision"] == "12"
    assert "state" not in with_story


def test_redacted_listing_row() -> None:
    redacted = ReconciliationListing.from_json(
        {
            "outbox_id": OUTBOX,
            "provider": "bank",
            "status": "failed",
            "reason": "failed",
            "since": "2026-10-31T18:05:00Z",
        }
    )
    assert redacted is not None
    row = listing_row(redacted, STORY, {OUTBOX})
    assert (row.redacted, row.effect_text, row.value, row.effect_json) == (
        True,
        "Redacted by an erasure",
        None,
        "",
    )


def test_reverification_cells() -> None:
    passed = listing_row(listing(reverification={"reverification": "passed"}), STORY, set())
    assert (passed.reverification, passed.reverification_title) == ("Passed", "passed")
    bare = listing_row(listing(reverification={"reverification": "failed"}), STORY, set())
    assert bare.reverification == "Failed"
    codes = {"reverification": "failed", "reasons": ["bounds:max", "later_code"]}
    failed = listing_row(listing(reverification=codes), STORY, set())
    assert failed.reverification == "Failed: it would break the `max` bound; `later_code`"


def test_count_and_unreadable_wording() -> None:
    assert (count_text(1), count_text(4), count_text(1234)) == (
        "1 effect listed",
        "4 effects listed",
        "1,234 effects listed",
    )
    assert unreadable_text(1) == "1 listing couldn't be read — see More"
    assert unreadable_text(3) == "3 listings couldn't be read — see More"


REQUEST = "req-01JM7RQ0000000000000000001"


@override_settings(ROOT_URLCONF=ACTIONS)
def test_a_listing_without_an_outbox_id_has_no_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    """#291 (Engine #661): a request-keyed row, even with a settleable reason, has no actions and
    anchors on ``request-<id>``; it is never redacted. **Complement:** ``reverse`` is never
    called with ``None``, and an outbox-keyed row with the same reason keeps both actions (a
    settleable reason: ``unconfirmed`` isn't one, so it would have none either way)."""
    from django.urls import reverse as real

    import console.reconciliation as reconciliation

    def strict(name: str, args: list[Any] | None = None, **kwargs: Any) -> str:
        assert None not in (args or []), (name, args)
        return real(name, args=args, **kwargs)

    monkeypatch.setattr(reconciliation, "reverse", strict)
    keyed = listing(outbox_id=None, request_id=REQUEST, value=None, effect=None)
    row = listing_row(keyed, STORY, set(), story=story())
    assert row.actions == ()
    assert (row.anchor, row.outbox_id, row.request_id) == (f"request-{REQUEST}", None, REQUEST)
    assert row.redacted is False
    assert row.effect_text == "—"
    outbox = listing_row(listing(), STORY, set(), story=story())
    assert outbox.anchor == f"outbox-{OUTBOX}"
    assert [a.label for a in outbox.actions] == ["Settle now", "Compensate"]
    assert all(f"%23outbox-{OUTBOX}" in a.url for a in outbox.actions)  # next= is the anchor
    neither = listing_row(listing(outbox_id=None), STORY, set())
    assert (neither.anchor, neither.actions) == (None, ())


def test_a_request_keyed_row_renders_its_anchor_and_request_id() -> None:
    from django.template.loader import render_to_string

    keyed = listing(outbox_id=None, request_id=REQUEST, value=None, effect=None)
    context = {"actions_gate": None, "gate_id": "gate"}
    html = render_to_string(
        "console/inspect/_listing_row.html",
        {**context, "row": listing_row(keyed, STORY, set())},
    )
    assert f'<tr id="request-{REQUEST}" class="listing">' in html
    assert '<td class="outbox">—</td>' in html
    assert f"<dt>Request id</dt><dd><code>{REQUEST}</code></dd>" in html
    assert html.index("<dt>Reason</dt>") < html.index("<dt>Request id</dt>")
    outbox = render_to_string(
        "console/inspect/_listing_row.html",
        {**context, "row": listing_row(listing(), STORY, set())},
    )
    assert f'<tr id="outbox-{OUTBOX}" class="listing">' in outbox
    assert "Request id" not in outbox
    bare = render_to_string(
        "console/inspect/_listing_row.html",
        {**context, "row": listing_row(listing(outbox_id=None), STORY, set())},
    )
    assert '<tr class="listing">' in bare
