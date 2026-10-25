"""The history browser's controls and reads (#52): every control maps to exactly its query
parameter, audit needs omniscient, paging follows ``next_before_turn``, and Engine refusals show
the error panel with the controls intact."""

import re

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.history.helpers import (
    HISTORY,
    MARA,
    STORY,
    TOBIN,
    URL,
    VARIANTS,
    answer,
    calls_to,
    history_calls,
    history_json,
    page,
    query_of,
    variants_json,
)
from tests.play.helpers import answer_page

pytestmark = pytest.mark.django_db
HTMX = {"HX-Request": "true"}


def test_defaults_send_only_the_page_size(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client)
    assert history_calls(engine) == [{"limit": ["50"]}]
    assert '<option value="" selected>story default</option>' in html
    assert '<option value="50" selected>50</option>' in html
    assert "blank = head (3)" in html
    # Complement: no settings read without audit.
    assert calls_to(engine, "/settings") == 0


@pytest.mark.parametrize(
    ("query", "sent"),
    [
        (f"viewpoint=character:{TOBIN}", {"viewpoint": [f"character:{TOBIN}"]}),
        ("viewpoint=omniscient", {"viewpoint": ["omniscient"]}),
        ("as_of=2", {"as_of": ["2"]}),
        ("as_of=0", {"as_of": ["0"]}),
        (f"actor_character_id={MARA}", {"actor_character_id": [MARA]}),
        ("include_summary=1", {"include_summary": ["1"]}),
        (
            "viewpoint=omniscient&include_audit=1",
            {"viewpoint": ["omniscient"], "include_audit": ["1"]},
        ),
        ("before_turn=3", {"before_turn": ["3"]}),
        ("variant=3:2", {"variant": ["3:2"]}),
    ],
)
def test_each_control_maps_to_exactly_its_parameter(
    operator_client: Client, engine: FakeEngine, query: str, sent: dict[str, list[str]]
) -> None:
    answer(engine)
    page(operator_client, query)
    assert history_calls(engine) == [{**sent, "limit": ["50"]}]


@pytest.mark.parametrize("size", ["50", "200", "1000"])
def test_page_size_is_always_sent(operator_client: Client, engine: FakeEngine, size: str) -> None:
    answer(engine)
    html = page(operator_client, f"limit={size}")
    assert history_calls(engine) == [{"limit": [size]}]
    assert f'<option value="{size}" selected>{size}</option>' in html


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("limit=75", "limit"),
        ("as_of=-1", "as_of"),
        ("as_of=two", "as_of"),
        ("before_turn=-3", "before_turn"),
        ("viewpoint=character:NOBODY", "viewpoint"),
    ],
)
def test_bad_controls_are_form_errors_with_no_history_call(
    operator_client: Client, engine: FakeEngine, query: str, field: str
) -> None:
    answer(engine)
    html = page(operator_client, query)
    assert history_calls(engine) == []
    assert "errorlist" in html
    assert "nothing was read" in html
    # The variants panel still renders.
    assert "needs a guardrail re-check to activate" in html


@pytest.mark.parametrize("viewpoint", ["", f"character:{MARA}"])
def test_audit_is_refused_unless_omniscient(
    operator_client: Client, engine: FakeEngine, viewpoint: str
) -> None:
    answer(engine)
    html = page(operator_client, f"viewpoint={viewpoint}&include_audit=1")
    # Zero Engine calls carry include_audit; no history call at all.
    assert history_calls(engine) == []
    assert "Audit records need the omniscient viewpoint" in html
    assert re.search(r'name="include_audit" value="1" id="id_include_audit" disabled', html)
    assert "Variants" in html
    assert '<select name="viewpoint"' in html


def test_audit_box_is_enabled_only_with_omniscient(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient")
    assert re.search(r'id="id_include_audit">', html)
    assert "enabled only with the omniscient viewpoint" not in html
    other = page(operator_client, f"viewpoint=character:{TOBIN}")
    assert re.search(r'id="id_include_audit" disabled>', other)
    assert "enabled only with the omniscient viewpoint" in other


def test_viewpoint_select_rerenders_only_the_controls(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient&include_audit=1", **HTMX)
    assert html.lstrip().startswith('<div id="history-controls">')
    assert re.search(r'id="id_include_audit" checked>', html)
    assert "<html" not in html
    assert history_calls(engine) == []
    other = page(operator_client, "viewpoint=&include_audit=1", **HTMX)
    assert re.search(r'id="id_include_audit" disabled>', other)
    # The HTMX re-render shows no form errors.
    assert "Audit records need the omniscient viewpoint" not in other


def test_viewpoint_select_carries_its_htmx_attributes(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = page(operator_client)
    select = re.search(r'<select name="viewpoint"[^>]*>', html)
    assert select is not None
    tag = select.group(0)
    assert f'hx-get="{URL}"' in tag
    assert 'hx-target="#history-controls"' in tag
    assert 'hx-include="closest form"' in tag


def test_older_follows_next_before_turn(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, history=history_json(next_before_turn=2))
    html = page(operator_client, "viewpoint=omniscient&limit=200")
    older = re.search(r'<a href="([^"]+)">Older →</a>', html)
    assert older is not None
    assert query_of(older.group(1)) == {
        "viewpoint": ["omniscient"],
        "limit": ["200"],
        "before_turn": ["2"],
    }
    operator_client.get(older.group(1).replace("&amp;", "&"))
    assert history_calls(engine)[-1] == {
        "viewpoint": ["omniscient"],
        "limit": ["200"],
        "before_turn": ["2"],
    }


def test_no_older_link_on_the_oldest_page(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "before_turn=3")
    assert "Older →" not in html
    newest = re.search(r'<a href="([^"]+)">Newest page</a>', html)
    assert newest is not None
    assert "before_turn" not in newest.group(1)


def test_header_line_shows_the_echoed_read(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = page(operator_client, "viewpoint=omniscient")
    assert (
        "Viewpoint omniscient <code>omniscient</code> · as of 3 · revision 4 · audience filter none"
        in html
    )


def test_the_chain_line_keeps_variant_and_clears_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = page(operator_client, "variant=3:2&limit=200")
    assert "Viewing chain 3:2 — " in html
    assert "· chain 3:2</p>" in html
    assert '<input type="hidden" name="variant" value="3:2">' in html
    clear = re.search(r'Viewing chain 3:2 — <a href="([^"]+)">clear</a>', html)
    assert clear is not None
    assert query_of(clear.group(1)) == {"limit": ["200"]}


def test_as_of_datalist_runs_to_head_or_the_chain_tip(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = page(operator_client)
    datalist = re.search(r'<datalist id="as-of-turns">(.*?)</datalist>', html, re.S)
    assert datalist is not None
    values = re.findall(r'<option value="(\d+)"', datalist.group(1))
    assert values == ["0", "1", "2", "3"]
    assert '<option value="0">0 (genesis)</option>' in datalist.group(1)
    # A chain's datalist runs to its tip (the dormant 3:2 chain's tip is 3; a shorter tip):
    chains = variants_json()
    chains["items"][1]["tip_turn"] = 2
    engine.respond("GET", VARIANTS, 200, json=chains)
    chained = page(operator_client, "variant=3:2")
    found = re.search(r'<datalist id="as-of-turns">(.*?)</datalist>', chained, re.S)
    assert found is not None
    assert re.findall(r'<option value="(\d+)"', found.group(1)) == ["0", "1", "2"]
    assert "blank = head (2)" in chained


def test_engine_422_shows_the_panel_with_controls(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond(
        "GET",
        HISTORY,
        422,
        json={
            "error": {
                "code": "validation_error",
                "message": "as_of must be a turn of the story, from 0 to its head",
                "details": {"field": "query.as_of", "head": 3},
            }
        },
    )
    html = page(operator_client, "as_of=9")
    assert history_calls(engine) == [{"as_of": ["9"], "limit": ["50"]}]
    assert "Engine error 422 validation_error" in html
    assert "as_of must be a turn of the story" in html
    assert '<input type="number" name="as_of" value="9"' in html
    assert "history-header" not in html
    assert "needs a guardrail re-check to activate" in html


def test_engine_403_omniscient_required_shows_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    engine.respond(
        "GET",
        HISTORY,
        403,
        json={
            "error": {
                "code": "omniscient_required",
                "message": "this read returns private records and needs the omniscient viewpoint",
                "details": {"field": "query.include_audit"},
            }
        },
    )
    html = page(operator_client, "viewpoint=omniscient&include_audit=1")
    assert "Engine error 403 omniscient_required" in html
    assert '<select name="viewpoint"' in html
    assert "Variants" in html


def test_history_is_linked_from_the_list_and_the_play_header(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    html = operator_client.get(f"/stories/{STORY}/").content.decode()
    assert f'<a class="history-link" href="{URL}">History</a>' in html
    stories = engine_fixture("list_stories")
    engine.respond("GET", "/stories", 200, json=stories)
    listed = operator_client.get("/stories/").content.decode()
    for item in stories["items"]:
        link = f'<a class="history-link" href="/stories/{item["id"]}/history/">History</a>'
        # #87: a held story's row offers its detail link and Delete only.
        assert (link in listed) is (item["status"] != "blocked_by_erasure")
