"""Pinned memories and custom instructions (#54): the forms' bounds, row editing, saves and
the Engine's refusals."""

import re
from typing import Any

from django.test import Client

from console.models import EngineOperation
from tests.engine_panel.helpers import settings_body
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.settings.helpers import (
    INSTRUCTIONS,
    INSTRUCTIONS_URL,
    PINNED,
    PINNED_URL,
    URL,
    answer_page,
    body,
    error,
    form_data,
    writes,
)

FIRST = "01M5R89563XNHAKKC94WKCRC0Z"
SECOND = "01M5R89563XNHAKKC94WKCRC10"


def _page(client: Client, engine: FakeEngine, **answers: Any) -> str:
    answer_page(engine, **answers)
    html: str = client.get(URL).content.decode()
    return html


def _pins(html: str, texts: list[str], ids: list[str] | None = None) -> dict[str, Any]:
    """The pinned form posted with ``texts`` as its rows (``ids`` their Engine ids)."""
    data: dict[str, Any] = {
        k: v for k, v in form_data(html, PINNED_URL).items() if not k.startswith("pins-")
    }
    data["pins-TOTAL_FORMS"] = str(len(texts))
    data["pins-INITIAL_FORMS"] = "0"
    for index, text in enumerate(texts):
        data[f"pins-{index}-text"] = text
        data[f"pins-{index}-id"] = (ids or [])[index] if ids and index < len(ids) else ""
    return data


def _rows(page: str) -> list[str]:
    return [
        t.removeprefix("\n")
        for t in re.findall(r'<textarea name="pins-\d+-text"[^>]*>(.*?)</textarea>', page, re.S)
    ]


def _put_answer(engine: FakeEngine, **changes: Any) -> None:
    answer: dict[str, Any] = engine_fixture("put_pinned_memories")
    answer.update(changes)
    engine.respond("PUT", PINNED, 200, json=answer)


# --- pinned memories: the form --------------------------------------------------------------------


def test_the_list_shows_each_item_with_its_id(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)

    assert _rows(html) == ["Tobin owes Mara money.", "The ferry runs at dawn."]
    assert f'name="pins-0-id" value="{FIRST}"' in html
    assert '<small class="revision">revision 4</small>' in html


def test_51_items_are_refused_with_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()

    page = operator_client.post(
        PINNED_URL, _pins(html, [f"pin {i}" for i in range(51)])
    ).content.decode()

    assert engine.requests == []
    assert "At most 50 pinned memories (the deployment&#x27;s max_pinned_memories); 51 given." in (
        page
    )


def test_the_cap_is_the_deployments(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine, deployment=settings_body(max_pinned_memories=2))
    engine.requests.clear()

    assert 'name="add"' not in html  # at the cap: no Add
    page = operator_client.post(PINNED_URL, _pins(html, ["a", "b", "c"])).content.decode()

    assert engine.requests == []
    assert "At most 2 pinned memories" in page


def test_a_2001_character_item_is_refused_with_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()

    page = operator_client.post(PINNED_URL, _pins(html, ["ok", "x" * 2001])).content.decode()

    assert engine.requests == []
    assert "At most 2,000 characters (2,001 given)." in page
    assert _rows(page)[0] == "ok"  # input kept


def test_length_counts_code_points_after_nfc_and_strip(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    _put_answer(engine)
    decomposed = "é" * 1500  # 3,000 code points as typed, 1,500 after NFC

    operator_client.post(PINNED_URL, _pins(html, [f"  {decomposed}  "]))

    [put] = writes(engine)
    assert body(put) == {"items": [{"text": "é" * 1500}]}


def test_a_blank_item_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()

    page = operator_client.post(PINNED_URL, _pins(html, ["a", "   "])).content.decode()

    assert engine.requests == []
    assert "A pinned memory cannot be blank — remove the row instead." in page


def test_add_remove_and_move_rows_make_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.requests.clear()
    data = _pins(html, ["one", "two", "three"], [FIRST, SECOND])

    added = operator_client.post(PINNED_URL, {**data, "add": "1"}).content.decode()
    removed = operator_client.post(PINNED_URL, {**data, "remove": "0"}).content.decode()
    up = operator_client.post(PINNED_URL, {**data, "up": "2"}).content.decode()
    down = operator_client.post(PINNED_URL, {**data, "down": "0"}).content.decode()

    assert engine.requests == []
    assert _rows(added) == ["one", "two", "three", ""]
    assert "cannot be blank" not in added  # an edit re-renders without validating
    assert _rows(removed) == ["two", "three"]
    assert f'name="pins-0-id" value="{SECOND}"' in removed
    assert _rows(up) == ["one", "three", "two"]
    assert _rows(down) == ["two", "one", "three"]
    assert f'name="pins-1-id" value="{FIRST}"' in down  # the id moves with its row


# --- pinned memories: saving ----------------------------------------------------------------------


def test_save_puts_the_items_in_order(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    _put_answer(engine)

    response = operator_client.post(
        PINNED_URL, _pins(html, ["Tobin owes Mara money.", "New one."], [FIRST])
    )

    assert response.status_code == 302
    assert response["Location"] == f"{URL}#pinned"
    [put] = writes(engine)
    assert body(put) == {
        "items": [{"id": FIRST, "text": "Tobin owes Mara money."}, {"text": "New one."}]
    }
    assert "X-Epistrel-Expected-Revision" not in put.headers
    assert EngineOperation.objects.get().kind == "put_pinned_memories"
    flashed = operator_client.get(URL).content.decode()
    assert "Saved pinned memories — timeline revision 5, operation 01M5R895604STFZ0RAW2Y5FBKS" in (
        flashed
    )


def test_saving_zero_items_clears_them(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    _put_answer(engine, items=[], operation_id=None)

    operator_client.post(PINNED_URL, _pins(html, []))

    [put] = writes(engine)
    assert body(put) == {"items": []}
    flashed = operator_client.get(URL).content.decode()
    assert "No change to the pinned memories — nothing was written (timeline revision 5)" in (
        flashed
    )


def test_a_cap_422_shows_cap_limit_and_actual(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        PINNED,
        422,
        json=error(
            "validation_error",
            "pins.max_pinned is 1; the request has 2",
            field="body.items",
            cap="pins.max_pinned",
            limit=1,
            actual=2,
        ),
    )

    page = operator_client.post(PINNED_URL, _pins(html, ["a", "b"])).content.decode()

    assert "Over a cap — nothing was saved" in page
    assert "<dt>Cap</dt><dd>pins.max_pinned</dd>" in page
    assert "<dt>Limit</dt><dd>1</dd>" in page
    assert "<dt>Actual</dt><dd>2</dd>" in page
    assert _rows(page) == ["a", "b"]


def test_pin_limit_exceeded_shows_scope_limit_and_actual(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        PINNED,
        422,
        json=error("pin_limit_exceeded", "too many pins", scope="story", limit=2000, actual=2001),
    )

    page = operator_client.post(PINNED_URL, _pins(html, ["a"])).content.decode()

    assert "Pin limit exceeded — nothing was saved" in page
    assert "<dt>Scope</dt><dd>story</dd>" in page
    assert "<dt>Limit</dt><dd>2000</dd>" in page
    assert "<dt>Actual</dt><dd>2001</dd>" in page


def test_a_field_422_marks_its_row(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        PINNED,
        422,
        json=error(
            "validation_error",
            "a pinned memory appears twice",
            errors=[{"field": "body.items[1].text", "message": "a pinned memory appears twice"}],
        ),
    )

    page = operator_client.post(PINNED_URL, _pins(html, ["a", "a"])).content.decode()

    second = re.search(r'<li class="pinned-item" id="pin-1">.*?</li>\s*</ul>', page, re.S)
    assert second is not None
    assert "a pinned memory appears twice" in second.group(0)


def test_a_guardrail_rejection_shows_the_policy(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    violation = {
        "policy_id": "no_slurs",
        "name": "No slurs",
        "message": "Keep it clean.",
        "kind": "deterministic",
        "locator": "items[1].text",
    }
    engine.respond(
        "PUT",
        PINNED,
        422,
        json=error("guardrail_violation", "rejected", violations=[violation]),
    )

    page = operator_client.post(PINNED_URL, _pins(html, ["fine", "rude"])).content.decode()

    assert "Rejected by a guardrail — nothing was saved" in page
    assert "<td><code>no_slurs</code></td><td>No slurs</td><td>Keep it clean.</td>" in page
    row = re.search(r'<li class="pinned-item" id="pin-1">.*?</li>', page, re.S)
    assert row is not None
    assert "Guardrail No slurs: Keep it clean." in row.group(0)


def test_an_erased_subject_marks_its_row(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        PINNED,
        409,
        json=error(
            "erased_subject_referenced",
            "erased",
            erasure_id="01M5R895ERASURE00000000000",
            match_count=1,
            field="body.items[0].text",
        ),
    )

    page = operator_client.post(PINNED_URL, _pins(html, ["about Ada", "b"])).content.decode()

    assert "This text refers to an erased subject — nothing was saved" in page
    row = re.search(r'<li class="pinned-item" id="pin-0">.*?</li>', page, re.S)
    assert row is not None
    assert "This text refers to an erased subject" in row.group(0)


def test_busy_on_a_pinned_save_keeps_the_input(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        PINNED,
        423,
        json=error("story_busy", "busy", reason="in_flight", operation_id="01M5R8954SQXFAN5N4"),
        headers={"Retry-After": "2"},
    )

    page = operator_client.post(PINNED_URL, _pins(html, ["kept"])).content.decode()

    assert "Story busy — another turn is running" in page
    assert _rows(page) == ["kept"]


# --- custom instructions --------------------------------------------------------------------------


def _instructions(html: str, text: str) -> dict[str, Any]:
    data: dict[str, Any] = form_data(html, INSTRUCTIONS_URL)
    data["text"] = text
    return data


def _instructions_answer(engine: FakeEngine, **changes: Any) -> None:
    answer: dict[str, Any] = engine_fixture("put_custom_instructions")
    answer.update(changes)
    engine.respond("PUT", INSTRUCTIONS, 200, json=answer)


def test_instructions_are_shown_and_saved(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    assert "Keep replies short.</textarea>" in html
    _instructions_answer(engine)

    response = operator_client.post(INSTRUCTIONS_URL, _instructions(html, " Keep it short. "))

    assert response["Location"] == f"{URL}#instructions"
    [put] = writes(engine)
    assert body(put) == {"text": "Keep it short."}
    assert EngineOperation.objects.get().kind == "put_custom_instructions"
    flashed = operator_client.get(URL).content.decode()
    assert "Saved custom instructions — timeline revision 5" in flashed


def test_empty_or_blank_instructions_clear_them(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(operator_client, engine)
    _instructions_answer(engine, text="")

    operator_client.post(INSTRUCTIONS_URL, _instructions(html, ""))
    operator_client.post(INSTRUCTIONS_URL, _instructions(html, "  \n "))

    assert [body(r) for r in writes(engine)] == [{"text": ""}, {"text": ""}]


def test_instructions_over_the_cap_make_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = _page(
        operator_client, engine, deployment=settings_body(max_custom_instructions_chars=10)
    )
    engine.requests.clear()

    page = operator_client.post(INSTRUCTIONS_URL, _instructions(html, "x" * 11)).content.decode()

    assert engine.requests == []
    assert "At most 10 characters (11 given)." in page


def test_an_instructions_guardrail_rejection(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    violation = {
        "policy_id": "no_slurs",
        "name": "No slurs",
        "message": "Keep it clean.",
        "kind": "deterministic",
    }
    engine.respond(
        "PUT", INSTRUCTIONS, 422, json=error("guardrail_violation", "no", violations=[violation])
    )

    page = operator_client.post(INSTRUCTIONS_URL, _instructions(html, "rude")).content.decode()

    assert "Rejected by a guardrail — nothing was saved" in page
    assert "<td><code>no_slurs</code></td>" in page
    assert "\nrude</textarea>" in page


def test_an_instructions_erased_subject(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        INSTRUCTIONS,
        409,
        json=error("erased_subject_referenced", "erased", erasure_id="E1", match_count=2),
    )

    page = operator_client.post(INSTRUCTIONS_URL, _instructions(html, "Ada")).content.decode()

    assert "This text refers to an erased subject — nothing was saved" in page
    assert "<dt>Matches</dt><dd>2</dd>" in page


def test_busy_on_an_instructions_save(operator_client: Client, engine: FakeEngine) -> None:
    html = _page(operator_client, engine)
    engine.respond(
        "PUT",
        INSTRUCTIONS,
        423,
        json=error("story_busy", "busy", reason="queue_full", queue_depth=5),
    )

    page = operator_client.post(INSTRUCTIONS_URL, _instructions(html, "kept")).content.decode()

    assert "Story busy — queue full (5 waiting)" in page
    assert "\nkept</textarea>" in page
