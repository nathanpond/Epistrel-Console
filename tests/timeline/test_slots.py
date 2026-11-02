"""#209: rewind or fork at an inter-turn slot ``N⁺k``. The optional Inter-turn slot field sends
``slot`` only when given (a blank one sends #53's exact body), an invalid one sends nothing, and
the question, lines, intro and flashes name ``turn N⁺ᵏ`` for k ≥ 1."""

from html import escape

import pytest
from django.test import Client

from console.forms.timeline import SLOT_INVALID, SLOT_TOO_HIGH
from console.world import SLOTS_NOT_LISTED
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.timeline.helpers import (
    KEY,
    REV,
    REVISION_HEADER,
    STORY,
    body_of,
    calls,
    error_json,
    fixture,
    flash,
    html,
    path,
    posted,
    url,
)

pytestmark = pytest.mark.django_db

FORK = engine_fixture("fork")
TRUNCATE = f"/stories/{STORY}/truncate"
FORK_PATH = f"/stories/{STORY}/fork"
SLOT_ERROR = error_json(
    "validation_error",
    "slot must be at most 32767",
    field="body.slot",
    errors=[{"field": "body.slot", "message": "slot must be at most 32767"}],
)


# --- Rewind ---------------------------------------------------------------------------------------


def test_rewind_sends_the_slot(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("truncate"), 200, json=fixture("truncate", to_turn=2))
    response = operator_client.post(url("rewind", 2), posted(slot="2"))
    (request,) = calls(engine, "POST", TRUNCATE)
    assert body_of(request) == {"to_turn": 2, "slot": 2}
    assert request.headers[REVISION_HEADER] == str(REV)
    assert request.headers["Idempotency-Key"] == KEY
    assert response.status_code == 302
    (line,) = flash(response)
    assert "Rewound to turn 2⁺²: next turn index 2, 0 discarded operations" in line


def test_rewind_blank_slot_sends_the_main_body(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: the literal body and flash ``test_operations.py`` asserts on main."""
    engine.respond("POST", path("truncate"), 200, json=fixture("truncate"))
    response = operator_client.post(url("rewind", 1), posted(slot=""))
    (request,) = calls(engine, "POST", TRUNCATE)
    assert body_of(request) == {"to_turn": 1}
    (line,) = flash(response)
    assert line.startswith("Rewound to turn 1: next turn index 2, 0 discarded operations — ")
    assert "⁺" not in line


def test_rewind_slot_zero_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("truncate"), 200, json=fixture("truncate", to_turn=2))
    response = operator_client.post(url("rewind", 2), posted(slot="0"))
    (request,) = calls(engine, "POST", TRUNCATE)
    assert body_of(request) == {"to_turn": 2, "slot": 0}
    (line,) = flash(response)
    assert line.startswith("Rewound to turn 2: ")
    assert "⁺" not in line


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("x", SLOT_INVALID),
        ("-1", SLOT_INVALID),
        ("+2", SLOT_INVALID),
        ("2.0", SLOT_INVALID),
        ("٢", SLOT_INVALID),
        ("32768", SLOT_TOO_HIGH),
        ("9" * 5000, SLOT_TOO_HIGH),
    ],
)
def test_invalid_slot_sends_nothing(
    operator_client: Client, engine: FakeEngine, value: str, error: str
) -> None:
    response = operator_client.post(url("rewind", 2), posted(slot=value))
    assert response.status_code == 200
    page = response.content.decode()
    assert escape(error) in page
    assert "Rewind <em>" in page  # the confirm page again
    assert calls(engine, "POST", TRUNCATE) == []


def test_slot_leading_zeros_and_bound(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("truncate"), 200, json=fixture("truncate", to_turn=2))
    operator_client.post(url("rewind", 2), posted(slot="007"))
    operator_client.post(url("rewind", 2), posted(slot=" 32767 "))
    first, second = calls(engine, "POST", TRUNCATE)
    assert body_of(first)["slot"] == 7
    assert body_of(second)["slot"] == 32767


@pytest.mark.parametrize("route", ["rewind", "fork"])
def test_slot_widget_has_no_min_max(
    operator_client: Client, engine: FakeEngine, route: str
) -> None:
    page = html(operator_client, url(route, 2))
    start = page.index('name="slot"')
    tag = page[page.rindex("<input", 0, start) : page.index(">", start) + 1]
    assert 'inputmode="numeric"' in tag
    assert 'size="6"' in tag
    assert "min=" not in tag
    assert "max=" not in tag
    assert 'type="number"' not in tag
    assert "Inter-turn slot (optional)" in page
    assert engine.requests == []


def test_rewind_confirm_names_the_slot(operator_client: Client, engine: FakeEngine) -> None:
    page = html(operator_client, url("rewind", 2, slot="3"))
    assert "</em> to turn 2⁺³?" in page
    assert (
        escape("Turn 2's inter-turn slots 1\u20133 are kept; later slots become inactive.") in page
    )
    assert "own inter-turn slots become inactive too" not in page
    assert 'name="slot" value="3"' in page
    one = html(operator_client, url("rewind", 2, slot="1"))
    assert escape("Turn 2's inter-turn slot 1 is kept; later slots become inactive.") in one
    start = html(operator_client, url("rewind", 0, slot="2"))
    assert "</em> to turn 0⁺²?" in start
    assert engine.requests == []  # Complement: the GET makes no Engine call


def test_rewind_confirm_without_a_slot(operator_client: Client, engine: FakeEngine) -> None:
    page = html(operator_client, url("rewind", 2))
    assert "</em> to turn 2?" in page
    assert (
        escape(
            "Turn 2's own inter-turn slots become inactive too, unless you keep slots 1..k below."
        )
        in page
    )
    lines = [
        page.index("3 later turns become inactive."),
        page.index(escape("Turn 2's own inter-turn slots")),
        page.index("Nothing is deleted: Undo rewind brings them back."),
    ]
    assert lines == sorted(lines)
    zero = html(operator_client, url("rewind", 0, slot="0"))
    assert "to the start (turn 0)?" in zero
    assert escape("Turn 0's own inter-turn slots become inactive too") in zero
    invalid = html(operator_client, url("rewind", 2, slot="x"))
    assert "</em> to turn 2?" in invalid  # slot-less question, the field error shown
    assert escape(SLOT_INVALID) in invalid
    assert engine.requests == []


def test_rewind_slot_422_beside_the_field(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("truncate"), 422, json=SLOT_ERROR)
    response = operator_client.post(url("rewind", 2), posted(slot="5"))
    assert response.status_code == 200
    page = response.content.decode()
    field = page[page.index('class="field field-slot"') :]
    assert "slot must be at most 32767" in field[: field.index("</div>")]
    assert 'name="slot" value="5"' in page
    assert "Rewind refused" not in page


def test_rewind_other_422_keeps_the_refusal(operator_client: Client, engine: FakeEngine) -> None:
    refused = error_json("validation_error", "to_turn out of range", field="body.to_turn")
    engine.respond("POST", path("truncate"), 422, json=refused)
    page = operator_client.post(url("rewind", 2), posted(slot="1")).content.decode()
    assert "Rewind refused" in page


# --- Fork -----------------------------------------------------------------------------------------


def test_fork_sends_the_slot(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("fork"), 201, json=FORK)
    response = operator_client.post(url("fork", 2), posted(new_name="Side", slot="1"))
    (request,) = calls(engine, "POST", FORK_PATH)
    assert body_of(request) == {"at_turn": 2, "name": "Side", "slot": 1}
    (line,) = flash(response)
    assert f"Forked Harbour at turn 2⁺¹ into {FORK['name']}" in line


def test_fork_sends_chain_and_slot(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("fork"), 201, json=FORK)
    operator_client.post(url("fork", 3), posted(new_name="Side", variant_chain="3:2", slot="2"))
    (request,) = calls(engine, "POST", FORK_PATH)
    assert body_of(request) == {
        "at_turn": 3,
        "name": "Side",
        "variant_chain": {"turn": 3, "variant": 2},
        "slot": 2,
    }


def test_fork_blank_slot_sends_the_main_body(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: ``test_fork.py``'s literal body and flash."""
    engine.respond("POST", path("fork"), 201, json=FORK)
    response = operator_client.post(
        url("fork", 2), posted(new_name="Harbour (fork at turn 2)", slot="")
    )
    (request,) = calls(engine, "POST", FORK_PATH)
    assert body_of(request) == {"at_turn": 2, "name": "Harbour (fork at turn 2)"}
    (line,) = flash(response)
    assert "Forked Harbour at turn 2 into Harbour (fork at turn 2)" in line


def test_fork_intro_names_the_slot(operator_client: Client, engine: FakeEngine) -> None:
    page = html(operator_client, url("fork", 2, slot="3"))
    assert (
        "Copy the story's history through turn 2⁺³ (its inter-turn slots 1\u20133 included) into"
        in page
    )
    one = html(operator_client, url("fork", 3, slot="1", variant_chain="3:2"))
    assert "through turn 3⁺¹ (its inter-turn slot 1 included) along chain 3:2 into" in one
    plain = html(operator_client, url("fork", 2))
    assert "Copy the story's history through turn 2 into a new story." in plain
    assert "Fork Harbour at turn 2<" in html(operator_client, url("fork", 2, slot="3"))
    invalid = html(operator_client, url("fork", 2, slot="-1"))
    assert escape(SLOT_INVALID) in invalid
    assert "through turn 2 into a new story." in invalid
    assert engine.requests == []


def test_fork_invalid_slot_sends_nothing(operator_client: Client, engine: FakeEngine) -> None:
    page = operator_client.post(url("fork", 2), posted(new_name="Side", slot="x")).content.decode()
    assert escape(SLOT_INVALID) in page
    assert calls(engine, "POST", FORK_PATH) == []


def test_fork_slot_422_beside_the_field(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", path("fork"), 422, json=SLOT_ERROR)
    response = operator_client.post(url("fork", 2), posted(new_name="Side", slot="5"))
    assert response.status_code == 200
    page = response.content.decode()
    field = page[page.index('class="field field-slot"') :]
    assert "slot must be at most 32767" in field[: field.index("</div>")]
    assert 'name="slot" value="5"' in page


def test_fork_candidates_carry_the_slot(operator_client: Client, engine: FakeEngine) -> None:
    refused = error_json(
        "unknown_chain",
        "turn 3 continues in more than one way",
        variant_chain={"turn": 3, "variant": 1},
        candidates=[{"turn": 4, "variant": 1}, {"turn": 4, "variant": 2}],
    )
    engine.respond("POST", path("fork"), 409, json=refused)
    page = operator_client.post(
        url("fork", 3), posted(new_name="Side", variant_chain="3:1", slot="2")
    ).content.decode()
    for variant in (1, 2):
        assert f"variant_chain=4%3A{variant}&amp;slot=2" in page


def test_help_names_the_history_gap(operator_client: Client, engine: FakeEngine) -> None:
    rewind = html(operator_client, url("rewind", 3))
    fork = html(operator_client, url("fork", 3))
    keep = (
        "Keep turn 3's inter-turn slots 1..k — State sets, clock advances and settlements "
        f"recorded after turn 3. Blank: none of them. {SLOTS_NOT_LISTED}."
    )
    assert escape(keep) in rewind
    assert escape("Copy" + keep[len("Keep") :]) in fork
