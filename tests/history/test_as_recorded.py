"""History as originally recorded (#304): the View control, its query, the read-only page and
its refusals. The as-recorded answer is #292's ``m7/history_as_recorded.json``; the cast is
``m7/story.json``."""

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.utils.html import escape

from console.forms.history import HistoryForm
from console.forms.world import BOUNDARY_INVALID
from console.history import (
    AS_RECORDED_CONTROLS_NOTE,
    AS_RECORDED_NOTE,
    CHECKPOINT_INCOMPLETE,
    INVALID_VIEW_TEXT,
    NO_CHECKPOINT_YET,
    derived_rows,
    erasure_rows,
)
from console.rewrites import AS_RECORDED_LINE, OMNISCIENT_REQUIRED_TEXT
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.golden import normalise_html
from tests.history.helpers import (
    HISTORY,
    MARA,
    STORY_PATH,
    TOBIN,
    VARIANTS,
    answer,
    history_calls,
    history_json,
    page,
    query_of,
    variants_json,
)
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db
HTMX = {"HX-Request": "true"}
GOLDEN = Path(__file__).parent / "golden"
INSPECT = Path(__file__).parents[1] / "fixtures" / "inspect"
M7_STORY = "01JM7ST0000000000000000001"
M7_MARA = "01JM7CH0000000000000000001"
M7_ALICE = "01JM7CH0000000000000000002"
RECORDED_LINE = "I do. I forged it, if you must know."
CORRECTED_LINE = "I do. I keep it honestly, as my father did."
ERASURE_ID = "01JM7ER0000000000000000001"
PREDICATES = "/stories/{story_id}/predicates"


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def recorded(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("history_as_recorded")
    body.update(changes)
    return body


def corrected() -> dict[str, Any]:
    """The corrected counterpart (E16): the as-recorded answer with the amended line's text
    replaced and the as-recorded members dropped."""
    body = recorded(view="corrected")
    for key in ("boundary_tx", "erasure_markers", "derived"):
        body.pop(key)
    for item in body["items"]:
        if item["text"] == RECORDED_LINE:
            item["text"] = CORRECTED_LINE
    return body


def serve(engine: FakeEngine, history: dict[str, Any] | None = None) -> None:
    """The m7 story, its variants and the as-recorded history."""
    engine.respond("GET", STORY_PATH, 200, json=m7("story"))
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, 200, json=history if history is not None else recorded())


def refuse(
    engine: FakeEngine, status: int, code: str, message: str = "refused", **details: Any
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=m7("story"))
    engine.respond("GET", VARIANTS, 200, json=variants_json())
    engine.respond("GET", HISTORY, status, json=error_json(code, message, **details))


def show(client: Client, query: str = "") -> str:
    return page(client, query, story_id=M7_STORY)


def paths(engine: FakeEngine) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in engine.requests]


def controls_of(html: str) -> str:
    start = html.index('<div id="history-controls">')
    return html[start : html.index('<button type="submit">', start)]


def items_of(html: str) -> str:
    start = html.index('<section class="items">')
    return html[start : html.index('<section class="variants"', start)]


# --- Form and query ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "sent"),
    [
        ("", {"limit": ["50"]}),
        (
            "viewpoint=omniscient&include_audit=1&include_summary=1&limit=200",
            {
                "viewpoint": ["omniscient"],
                "include_audit": ["1"],
                "include_summary": ["1"],
                "limit": ["200"],
            },
        ),
        (
            f"viewpoint=character:{TOBIN}&player_view=character&actor_character_id={MARA}",
            {
                "viewpoint": [f"character:{TOBIN}"],
                "player_view": ["character"],
                "actor_character_id": [MARA],
                "limit": ["50"],
            },
        ),
        (
            "as_of=2&before_turn=3&variant=3:2&traces=1",
            {"as_of": ["2"], "before_turn": ["3"], "variant": ["3:2"], "limit": ["50"]},
        ),
        # Checkpoint is ignored on the corrected view, even when it is malformed.
        ("checkpoint=2%2B1&as_of=1", {"as_of": ["1"], "limit": ["50"]}),
        ("checkpoint=x", {"limit": ["50"]}),
    ],
)
def test_corrected_query_is_mains(
    operator_client: Client, engine: FakeEngine, query: str, sent: dict[str, list[str]]
) -> None:
    answer(engine)
    page(operator_client, query)
    assert history_calls(engine) == [sent]
    # Complement: no ``view`` parameter is ever sent on the corrected view.
    assert all("view" not in call for call in history_calls(engine))


def test_default_form_query_is_mains() -> None:
    form = HistoryForm({}, cast=[])
    assert form.is_valid(), form.errors
    assert form.query() == {
        "viewpoint": None,
        "as_of": None,
        "limit": 50,
        "before_turn": None,
        "actor_character_id": None,
        "variant": None,
        "include_audit": False,
        "include_summary": False,
        "player_view": None,
    }


def test_corrected_form_query_keys_are_mains() -> None:
    form = HistoryForm({"as_of": "2", "checkpoint": "x", "limit": "200"}, cast=[])
    assert form.is_valid(), form.errors
    assert set(form.query()) == {
        "viewpoint",
        "as_of",
        "limit",
        "before_turn",
        "actor_character_id",
        "variant",
        "include_audit",
        "include_summary",
        "player_view",
    }
    assert form.query()["as_of"] == 2
    assert form.as_recorded is False


def test_as_recorded_query(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    show(
        operator_client,
        f"view=as_recorded&checkpoint=2%2B1&viewpoint=character:{M7_ALICE}"
        f"&actor_character_id={M7_MARA}&include_audit=1&include_summary=1&limit=200"
        "&before_turn=3&player_view=character&as_of=1",
    )
    assert history_calls(engine) == [
        {"view": ["as_recorded"], "viewpoint": ["omniscient"], "as_of": ["2+1"]}
    ]
    sent = next(r for r in engine.requests if r.url.path.endswith("/history"))
    assert "as_of=2%2B1" in sent.url.query.decode()
    # Complement: nothing the Engine doesn't read on this view.
    for name in (
        "actor_character_id",
        "limit",
        "before_turn",
        "include_audit",
        "include_summary",
        "player_view",
    ):
        assert name not in history_calls(engine)[0]


def test_as_recorded_form_cleans_and_queries() -> None:
    form = HistoryForm(
        {
            "view": "as_recorded",
            "checkpoint": "2",
            "viewpoint": "character:NOBODY",
            "include_audit": "1",
            "limit": "75",
            "as_of": "-1",
            "variant": "3:2",
        },
        cast=[],
    )
    assert form.is_valid(), form.errors
    assert form.as_recorded
    assert form.cleaned_data["viewpoint"] == "omniscient"
    assert form.cleaned_data["include_audit"] is False
    assert form.query() == {
        "view": "as_recorded",
        "viewpoint": "omniscient",
        "as_of": "2",
        "variant": "3:2",
    }


def test_checkpoint_accepts_superscript(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    show(operator_client, "view=as_recorded&checkpoint=2⁺¹")
    assert history_calls(engine)[0]["as_of"] == ["2+1"]
    sent = next(r for r in engine.requests if r.url.path.endswith("/history"))
    assert "as_of=2%2B1" in sent.url.query.decode()


def test_blank_checkpoint_sends_no_as_of(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    show(operator_client, "view=as_recorded&checkpoint=")
    assert history_calls(engine) == [{"view": ["as_recorded"], "viewpoint": ["omniscient"]}]


@pytest.mark.parametrize("checkpoint", ["2%2B", "%2B1", "x"])
def test_bad_checkpoint_zero_calls(
    operator_client: Client, engine: FakeEngine, checkpoint: str
) -> None:
    serve(engine)
    html = show(operator_client, f"view=as_recorded&checkpoint={checkpoint}")
    assert history_calls(engine) == []
    assert BOUNDARY_INVALID in controls_of(html)
    assert "nothing was read" in html


def test_bad_view_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = show(operator_client, "view=sideways")
    assert history_calls(engine) == []
    field = re.search(r'<div class="field field-view">.*?</div>', html, re.S)
    assert field is not None
    assert "errorlist" in field.group(0)
    assert "nothing was read" in html


# --- Controls ------------------------------------------------------------------------------------


def test_as_recorded_disables_viewpoint_with_line(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve(engine)
    controls = controls_of(
        show(
            operator_client,
            f"view=as_recorded&viewpoint=character:{M7_ALICE}&actor_character_id={M7_MARA}"
            "&include_audit=1&limit=200",
        )
    )
    assert re.search(r'<select name="viewpoint" id="id_viewpoint" disabled>', controls)
    assert '<option value="omniscient" selected>' in controls
    assert AS_RECORDED_LINE in controls
    assert AS_RECORDED_CONTROLS_NOTE in controls
    for name in ("actor_character_id", "limit", "player_view"):
        assert re.search(rf'<select name="{name}"[^>]*disabled', controls), name
    for name in ("include_audit", "include_summary", "traces"):
        assert re.search(rf'name="{name}" value="1" id="id_{name}" disabled', controls), name
    # Checkpoint replaces "As of turn".
    assert 'name="checkpoint"' in controls
    assert 'name="as_of"' not in controls
    # The disabled controls' submitted values ride in hidden inputs.
    assert f'<input type="hidden" name="viewpoint" value="character:{M7_ALICE}">' in controls
    assert f'<input type="hidden" name="actor_character_id" value="{M7_MARA}">' in controls
    assert '<input type="hidden" name="include_audit" value="1">' in controls
    assert '<input type="hidden" name="limit" value="200">' in controls
    # The per-control notes are suppressed.
    assert "enabled only with the omniscient viewpoint" not in controls
    assert "applies to a character viewpoint" not in controls
    assert "one Engine read per turn" not in controls


def test_htmx_view_change_rerenders_controls(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, "view=as_recorded&as_of=2", story_id=M7_STORY, **HTMX)
    assert html.lstrip().startswith('<div id="history-controls">')
    assert "<html" not in html
    assert re.search(r'<select name="viewpoint" id="id_viewpoint" disabled>', html)
    assert AS_RECORDED_LINE in html
    # A valid as_of pre-fills Checkpoint.
    assert re.search(r'name="checkpoint"[^>]*value="2"', html)
    assert history_calls(engine) == []
    # The View select re-renders the fragment.
    view = re.search(r'<select name="view"[^>]*>', html)
    assert view is not None
    assert 'hx-get="/stories/' in view.group(0)
    assert 'hx-trigger="change"' in view.group(0)


def test_htmx_switch_back_prefills_as_of(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = page(operator_client, "view=&checkpoint=2%2B1", story_id=M7_STORY, **HTMX)
    assert re.search(r'name="as_of"[^>]*value="2"', html)
    assert 'name="checkpoint"' not in html


GOLDEN_STATES = {
    "controls_default": "",
    "controls_omniscient": (
        "viewpoint=omniscient&include_audit=1&include_summary=1&limit=200&as_of=2"
    ),
    "controls_character": (
        f"viewpoint=character:{TOBIN}&player_view=character&actor_character_id={MARA}"
        "&traces=1&variant=3:2"
    ),
}


@pytest.mark.parametrize("name", list(GOLDEN_STATES))
def test_corrected_controls_golden(operator_client: Client, engine: FakeEngine, name: str) -> None:
    """The fragment minus the View select equals main's (captured before #304, E24)."""
    answer(engine)
    html = page(operator_client, GOLDEN_STATES[name], **HTMX)
    view = re.compile(r'\s*<div class="field field-view">.*?</div>', re.S)
    assert len(view.findall(html)) == 1
    stripped = view.sub("", html, count=1)
    assert normalise_html(stripped) == normalise_html((GOLDEN / f"{name}.html").read_text())


# --- Page ----------------------------------------------------------------------------------------


def test_header_notice_erasures_and_records(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    items = items_of(show(operator_client, "view=as_recorded&checkpoint=2"))
    header = re.search(r'<p class="history-header as-recorded">(.*?)</p>', items)
    assert header is not None
    text = re.sub(r"<[^>]+>", "", header.group(1))
    assert text.startswith("As originally recorded · checkpoint turn 2 · committed ")
    assert "2026-11-02" in text
    assert text.endswith(" · revision 20")
    assert AS_RECORDED_NOTE in items
    erased = "Erased since: 1 erasure — erased content can't be shown as it was recorded"
    assert escape(erased) in items
    erasures = re.search(r'<details class="erasures">.*?</details>', items, re.S)
    assert erasures is not None
    assert f"<code>{ERASURE_ID}</code>" in erasures.group(0)
    assert "2026-11-03" in erasures.group(0)
    for line in ("Facts · 1", "Beliefs · 1", "Memories · 0", "States · 2"):
        assert line in items
    memories = re.search(r'<li class="derived-memories">.*?</li>', items, re.S)
    assert memories is not None
    assert "<details" not in memories.group(0)
    # The AC's block order: header, notice, erasures, back link, items table, records.
    order = [
        items.index('class="history-header as-recorded"'),
        items.index('class="notice as-recorded-note"'),
        items.index('class="warning erased-since"'),
        items.index('class="back-to-corrected"'),
        items.index('<table class="history">'),
        items.index('class="records-at-checkpoint"'),
    ]
    assert order == sorted(order)
    # The corrected header is gone.
    assert "Viewpoint omniscient" not in items
    assert "audience filter" not in items


def test_latest_checkpoint_header(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    items = items_of(show(operator_client, "view=as_recorded"))
    assert "checkpoint the latest (turn 2)" in items


def test_slot_checkpoint_header(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    items = items_of(show(operator_client, "view=as_recorded&checkpoint=2%2B1"))
    assert "checkpoint turn 2⁺¹" in items


def test_no_erasures_shows_nothing(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, recorded(erasure_markers=[]))
    items = items_of(show(operator_client, "view=as_recorded"))
    assert "Erased since" not in items
    assert 'class="erasures"' not in items


def test_recorded_text_differs_from_corrected(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    items = items_of(show(operator_client, "view=as_recorded&checkpoint=2"))
    assert RECORDED_LINE in items
    # Complement: the corrected line's text is absent; the corrected view shows it instead.
    assert CORRECTED_LINE not in items
    engine.respond("GET", HISTORY, 200, json=corrected())
    engine.respond(
        "GET", PREDICATES, 200, json=json.loads((INSPECT / "predicates_list.json").read_text())
    )
    corrected_items = items_of(show(operator_client, "as_of=2"))
    assert CORRECTED_LINE in corrected_items
    assert RECORDED_LINE not in corrected_items


def test_no_corrected_only_reads(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, recorded(items=[*recorded()["items"], _structured_item()]))
    show(operator_client, "view=as_recorded&checkpoint=2&traces=1&include_audit=1")
    assert paths(engine) == [
        f"GET /stories/{M7_STORY}",
        f"GET /stories/{M7_STORY}/variants",
        f"GET /stories/{M7_STORY}/history",
    ]
    # Complement: zero settings, effective-config, predicates and operation reads.
    for suffix in ("/settings", "/effective-config", "/predicates"):
        assert not any(r.url.path.endswith(suffix) for r in engine.requests)
    assert not any(r.url.path.startswith("/operations/") for r in engine.requests)


def _structured_item() -> dict[str, Any]:
    """A structured beat (``propositions`` set): the corrected view would read the predicates
    and, with traces, the turn's trace."""
    item = dict(recorded()["items"][1])
    item.update(turn=2, beat=3, message_id="01JM7MM0000000000000000203", propositions=[])
    return item


def test_no_row_write_controls(operator_client: Client, engine: FakeEngine) -> None:
    body = history_json()  # the corrected fixture's generated messages
    body.update(view="as_recorded", next_before_turn=None, derived=None, erasure_markers=[])
    serve(engine, body)
    items = items_of(show(operator_client, "view=as_recorded"))
    assert "feedback-row" not in items
    assert "<form" not in items
    assert "explain-link" not in items
    assert "perception-link" not in items
    assert "report-link" not in items
    assert 'class="paging"' not in items
    assert "Older →" not in items
    assert "Newest page" not in items
    # Complement: the corrected page for the same story does render feedback widgets.
    engine.respond("GET", HISTORY, 200, json=history_json())
    assert "feedback-row" in items_of(show(operator_client))


def test_back_link(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    items = items_of(show(operator_client, "view=as_recorded&checkpoint=2%2B1&variant=3:2"))
    link = re.search(r'<p class="back-to-corrected"><a href="([^"]+)">([^<]+)</a>', items)
    assert link is not None
    assert link.group(2) == "See turn 2 corrected"
    found = query_of(link.group(1))
    assert found["as_of"] == ["2"]
    assert found["variant"] == ["3:2"]
    assert "view" not in found
    assert "checkpoint" not in found
    # The latest: no as_of.
    items = items_of(show(operator_client, "view=as_recorded"))
    link = re.search(r'<p class="back-to-corrected"><a href="([^"]+)">', items)
    assert link is not None
    found = query_of(link.group(1))
    assert "as_of" not in found
    assert "view" not in found


def test_derived_null_leaves_section_out(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine, recorded(derived=None))
    items = items_of(show(operator_client, "view=as_recorded"))
    assert "Records at the checkpoint" not in items
    assert '<table class="history">' in items


def test_derived_rows_malformed_members() -> None:
    rows = derived_rows({"facts": "nope", "beliefs": [1, 2]})
    assert rows is not None
    assert [(r.label, r.count) for r in rows] == [
        ("Facts", "—"),
        ("Beliefs", "2"),
        ("Memories", "—"),
        ("States", "—"),
    ]
    assert derived_rows(None) is None


def test_erasure_rows_malformed_markers() -> None:
    rows = erasure_rows([{"erasure_id": "E1", "at": "2026-01-01T00:00:00+00:00"}, "x", {}])
    assert [(r.erasure_id, r.at, r.raw) for r in rows] == [
        ("E1", "2026-01-01T00:00:00+00:00", None),
        ("—", None, "x"),
        ("—", None, {}),
    ]
    assert erasure_rows(None) == []


def test_variant_links_keep_the_view(operator_client: Client, engine: FakeEngine) -> None:
    serve(engine)
    html = show(operator_client, "view=as_recorded&checkpoint=2")
    links = re.findall(r'<a class="view-chain" href="([^"]+)">', html)
    assert links
    for href in links:
        found = query_of(href)
        assert found["view"] == ["as_recorded"]
        assert found["checkpoint"] == ["2"]


# --- Errors --------------------------------------------------------------------------------------


def _beside_checkpoint(html: str, text: str) -> None:
    field = re.search(r'<div class="field field-checkpoint">.*?</div>', html, re.S)
    assert field is not None
    assert escape(text) in field.group(0)
    items = items_of(html)
    assert "Correct the controls above — nothing was read." in items
    assert "engine-error" not in items


def _controls_and_variants(html: str) -> None:
    assert '<div id="history-controls">' in html
    assert '<section class="variants" id="variants">' in html
    assert "view-chain" in html


def test_checkpoint_not_found(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "checkpoint_not_found")
    html = show(operator_client, "view=as_recorded&checkpoint=9")
    _beside_checkpoint(html, "No checkpoint at turn 9 was ever committed on this chain")
    _controls_and_variants(html)
    html = show(operator_client, "view=as_recorded&checkpoint=9%2B1")
    _beside_checkpoint(html, "No checkpoint at turn 9⁺¹ was ever committed on this chain")


def test_no_checkpoint_yet(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 404, "checkpoint_not_found")
    html = show(operator_client, "view=as_recorded")
    _beside_checkpoint(html, NO_CHECKPOINT_YET)
    _controls_and_variants(html)


def test_checkpoint_incomplete(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "checkpoint_incomplete")
    html = show(operator_client, "view=as_recorded&checkpoint=3")
    _beside_checkpoint(html, CHECKPOINT_INCOMPLETE)
    _controls_and_variants(html)


def test_validation_error_on_as_of(operator_client: Client, engine: FakeEngine) -> None:
    message = "as_of must be N or N+k with k >= 1"
    refuse(
        engine,
        422,
        "validation_error",
        message,
        field="query.as_of",
        errors=[{"field": "query.as_of", "message": message, "type": "value_error"}],
    )
    html = show(operator_client, "view=as_recorded&checkpoint=2%2B0")
    _beside_checkpoint(html, message)
    _controls_and_variants(html)


def test_omniscient_required_is_logged(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    refuse(engine, 403, "omniscient_required")
    with caplog.at_level(logging.WARNING):
        html = show(operator_client, "view=as_recorded")
    items = items_of(html)
    assert f'<p class="error">{OMNISCIENT_REQUIRED_TEXT}</p>' in items
    assert "omniscient_required" in items  # #43's panel, collapsed
    assert "omniscient_required" in caplog.text
    _controls_and_variants(html)


def test_invalid_view(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    refuse(engine, 422, "invalid_view")
    with caplog.at_level(logging.WARNING):
        html = show(operator_client, "view=as_recorded")
    assert f'<p class="error">{escape(INVALID_VIEW_TEXT)}</p>' in items_of(html)
    assert "invalid_view" in caplog.text
    _controls_and_variants(html)


def test_unknown_chain_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    refuse(engine, 409, "unknown_chain")
    html = show(operator_client, "view=as_recorded&variant=7:1")
    items = items_of(html)
    assert 'class="engine-error' in items
    assert "unknown_chain" in items
    assert "nothing was read" not in items
    _controls_and_variants(html)
