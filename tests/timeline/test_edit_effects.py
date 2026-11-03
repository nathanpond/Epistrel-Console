"""#258: a rewind, undo rewind, swap or fork whose answer lists external effects it could not
undo or re-send renders the edit report page (200, no flash) with each list, full outbox ids
linked to Reconciliation, the D12 count link and D11's notice. With every list empty the edit
redirects with its flash exactly as before; regenerate always does (#245's strip owns its
list)."""

from typing import Any

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from console.effects import outbox_list_notice_html
from console.reconciliation import REDACTED, effect_text
from console.timeline import (
    CANCELLED_HEADING,
    CANCELLED_LINE,
    PENDING_HEADING,
    PENDING_LINE,
    REQUIRED_HEADING,
    REQUIRED_LINE,
)
from console.views.play import FORK_BANNER_KEY
from tests.fakes.engine import FakeEngine
from tests.timeline.helpers import (
    PLAY,
    REV,
    STORY,
    error_json,
    fixture,
    flash,
    path,
    posted,
    regenerate_posted,
    url,
)

pytestmark = pytest.mark.django_db

REPORT = "console/timeline/effects_report.html"
PARTIAL = "console/timeline/_edit_effects.html"
WITHOUT_RECONCILIATION = "tests.timeline.urls_without_reconciliation"
BOX1 = "01JM6BX0000000000000000001"
BOX2 = "01JM6BX0000000000000000002"
BOX3 = "01JM6BX0000000000000000003"
BOX4 = "01JM6BX0000000000000000004"
PENDING_EFFECT = {"state": "gold", "delta": -5}


def required(outbox_id: str, *, status: str = "confirmed", **changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "outbox_id": outbox_id,
        "provider": "bank",
        "status": status,
        "value": "balance",
    }
    row.update(changes)
    return row


def cancelled(outbox_id: str) -> dict[str, Any]:
    return required(outbox_id, status="cancelled")


def pending(outbox_id: str) -> dict[str, Any]:
    return {"outbox_id": outbox_id, "status": "claimed", "effect": PENDING_EFFECT}


def templates(response: Any) -> list[str]:
    return [t.name for t in response.templates if t.name]


def reconciliation(story_id: str = STORY) -> str:
    return reverse("inspect:reconciliation", args=[story_id])


def success_line(body: dict[str, Any], text: str) -> str:
    return (
        f"{text} — operation {body['operation_id']}, timeline revision "
        f"{body['timeline_revision']}, idempotency key {body['idempotency_key']}"
    )


def rewind(client: Client, engine: FakeEngine, body: dict[str, Any]) -> Any:
    engine.respond("POST", path("truncate"), 200, json=body)
    return client.post(url("rewind", 1), posted())


def rewound_text(body: dict[str, Any]) -> str:
    return (
        f"Rewound to turn {body['to_turn']}: next turn index {body['next_turn_index']}, "
        f"{body['discarded_operations']} discarded operations"
    )


# --- Rewind ---------------------------------------------------------------------------------------


def test_rewind_with_required_effects_renders_the_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("truncate", reconciliation_required=[required(BOX1)])
    response = rewind(operator_client, engine, body)
    assert response.status_code == 200
    assert REPORT in templates(response)
    page = response.content.decode()
    assert "<title>Rewind — Epistrel Console</title>" in page
    assert f'<p class="notice success">{success_line(body, rewound_text(body))}</p>' in page
    assert f"<h3>{REQUIRED_HEADING}</h3>" in page
    assert f"<p>{REQUIRED_LINE}</p>" in page
    assert (
        f'<a href="{reconciliation()}#outbox-{BOX1}"><code class="outbox-id">{BOX1}</code></a>'
        in page
    )
    assert "<td><code>bank</code></td>" in page
    assert "<td><code>balance</code></td>" in page
    assert '<td title="confirmed">Confirmed</td>' in page
    assert f'<a href="{reconciliation()}">1 effect to reconcile</a>' in page
    assert str(outbox_list_notice_html()) in page
    assert f'<a class="button" href="{PLAY}">Continue to the story</a>' in page
    # Complement: the report replaces the flash; nothing is queued.
    assert flash(response) == []


def test_rewind_without_effects_redirects_as_before(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("truncate")
    assert body["reconciliation_required"] == []
    response = rewind(operator_client, engine, body)
    assert response.status_code == 302
    assert response["Location"] == PLAY
    assert flash(response) == [
        "Rewound to turn 1: next turn index 2, 0 discarded operations — operation "
        "01JA0000000000000000000OP2, timeline revision 9, idempotency key "
        "01JA0000000000000000000KY2"
    ]
    assert REPORT not in templates(response)


def test_redacted_required_effect(operator_client: Client, engine: FakeEngine) -> None:
    row = required(BOX1)
    del row["value"]
    page = rewind(
        operator_client, engine, fixture("truncate", reconciliation_required=[row])
    ).content.decode()
    assert f'<td><span class="redacted">{REDACTED}</span></td>' in page
    assert "<code>balance</code>" not in page


def test_ids_unlinked_while_reconciliation_unregistered(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("truncate", reconciliation_required=[required(BOX1)])
    with override_settings(ROOT_URLCONF=WITHOUT_RECONCILIATION):
        page = rewind(operator_client, engine, body).content.decode()
    assert f'<td><code class="outbox-id">{BOX1}</code></td>' in page
    assert f"#outbox-{BOX1}" not in page
    # Complement: no D12 link either, only its words.
    assert '<p class="reconcile">1 effect to reconcile</p>' in page
    assert "effects/reconciliation/" not in page


def test_refusals_unchanged(operator_client: Client, engine: FakeEngine) -> None:
    conflict = error_json("revision_conflict", "stale", current_revision=REV + 1)
    engine.respond("POST", path("truncate"), 409, json=conflict)
    response = operator_client.post(url("rewind", 1), posted())
    assert response.status_code == 200
    assert "console/timeline/refused.html" in templates(response)
    assert "The story changed since you loaded this page" in response.content.decode()
    # Complement: a refusal has no answer and no lists.
    assert PARTIAL not in templates(response)
    assert REPORT not in templates(response)


# --- Undo rewind and swap -------------------------------------------------------------------------


def test_undo_rewind_lists_required_and_cancelled(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture(
        "untruncate",
        reconciliation_required=[required(BOX1)],
        cancelled_on_reactivation=[cancelled(BOX2)],
    )
    engine.respond("POST", path("untruncate"), 200, json=body)
    response = operator_client.post(url("undo-rewind"), posted())
    assert response.status_code == 200
    page = response.content.decode()
    assert "<title>Undo rewind — Epistrel Console</title>" in page
    text = (
        f"Rewind undone: restored head turn {body['next_turn_index'] - 1}, "
        f"{body['restored_operations']} restored operations"
    )
    assert success_line(body, text) in page
    assert page.index(REQUIRED_HEADING) < page.index(CANCELLED_HEADING)
    assert f"<p>{CANCELLED_LINE}</p>" in page
    assert '<td title="cancelled">Cancelled</td>' in page
    assert f"#outbox-{BOX2}" in page
    assert "2 effects to reconcile" in page
    assert flash(response) == []


def test_undo_rewind_as_a_swap_keeps_its_title(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("untruncate_swapped", cancelled_on_reactivation=[cancelled(BOX2)])
    engine.respond("POST", path("untruncate"), 200, json=body)
    page = operator_client.post(url("undo-rewind"), posted()).content.decode()
    assert "<title>Undo rewind — Epistrel Console</title>" in page
    assert "Rewind undone as a swap" in page


def test_swap_lists_cancelled_only(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("swap", cancelled_on_reactivation=[cancelled(BOX2)])
    engine.respond("POST", path("swap"), 200, json=body)
    response = operator_client.post(url("swap", chain="4:2"), posted(chain="4:2"))
    assert response.status_code == 200
    page = response.content.decode()
    assert "<title>Swap variant — Epistrel Console</title>" in page
    assert page.count("<h3>") == 1
    assert f"<h3>{CANCELLED_HEADING}</h3>" in page
    # Complement: an empty list renders nothing, heading included.
    assert REQUIRED_HEADING not in page
    assert PENDING_HEADING not in page
    assert "<h3></h3>" not in page


def test_swap_already_active_unchanged(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("swap"), 200, json=fixture("swap_active"))
    response = operator_client.post(url("swap", chain="4:2"), posted(chain="4:2", rev="13"))
    assert response.status_code == 302
    (line,) = flash(response)
    assert line.startswith("Already active — chain turn 4 · variant 2")
    assert REPORT not in templates(response)


def test_count_link_sums_the_lists(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture(
        "untruncate",
        reconciliation_required=[required(BOX1), required(BOX3, status="unknown")],
        cancelled_on_reactivation=[cancelled(BOX2)],
    )
    engine.respond("POST", path("untruncate"), 200, json=body)
    page = operator_client.post(url("undo-rewind"), posted()).content.decode()
    assert f'<a href="{reconciliation()}">3 effects to reconcile</a>' in page
    assert '<td title="unknown">Unknown</td>' in page


# --- Fork -----------------------------------------------------------------------------------------


def test_fork_lists_source_pending_effects(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("fork", effects_pending_in_source=[pending(BOX4)])
    new = body["story_id"]
    engine.respond("POST", path("fork"), 201, json=body)
    response = operator_client.post(url("fork", 2), posted(new_name="Harbour (fork at turn 2)"))
    assert response.status_code == 200
    page = response.content.decode()
    assert "<title>Fork — Epistrel Console</title>" in page
    assert "Forked Harbour at turn 2 into" in page
    assert f"<h3>{PENDING_HEADING}</h3>" in page
    assert f"<p>{PENDING_LINE}</p>" in page
    # The rows link to the source story's Reconciliation, never the new story's.
    assert f'<a href="{reconciliation()}#outbox-{BOX4}">' in page
    assert reconciliation(new) not in page
    assert f"<td>{effect_text(PENDING_EFFECT)}</td>" in page
    assert '<td title="claimed">Claimed</td>' in page
    assert "<th>Provider</th>" not in page
    assert (
        f'<p class="source-reconciliation"><a href="{reconciliation()}">'
        "Source story&#x27;s Reconciliation</a></p>" in page
    )
    assert "to reconcile" not in page  # pending rows aren't counted
    assert f'<a class="button" href="/stories/{new}/">Continue to the story</a>' in page
    assert FORK_BANNER_KEY.format(new) in operator_client.session
    assert flash(response) == []


def test_fork_without_pending_effects_redirects_as_before(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = fixture("fork")
    engine.respond("POST", path("fork"), 201, json=body)
    response = operator_client.post(url("fork", 2), posted(new_name="Harbour (fork at turn 2)"))
    assert response.status_code == 302
    assert response["Location"] == f"/stories/{body['story_id']}/"


# --- Regenerate (the complement) ------------------------------------------------------------------


def test_regenerate_redirects_as_before(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("regenerate", reconciliation_required=[required(BOX1)])
    engine.respond("POST", path("regenerate"), 200, json=body)
    response = operator_client.post(url("regenerate"), regenerate_posted())
    assert response.status_code == 302
    assert response["Location"].startswith(f"{PLAY}?turn=")
    (line,) = flash(response)
    assert line.startswith(f"Regenerated turn {body['turn']}")
    # Complement: #245's World strip owns regenerate's list; no report page.
    assert REPORT not in templates(response)
