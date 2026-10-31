"""The Knowledge tab's Exposures section (#193): what witnesses heard and whether it resolved.

Runs against the real urlconf on the schema-validating fake Engine, under #188's Structured v2
configuration and the shared Alice/Bob/Pat cast (``story_cast.json``; Pat is the player).
``exposures_page1.json`` (revision 7, ``next_after_id`` ``XP05``):

- ``XP01``: Alice heard Bob, turn 1 · beat 2, full, unresolved;
- ``XP02``: Bob heard Alice, turn 2 · v1 · beat 3 (step 40000), resolved by transition ``TR02``
  in operation ``QR02``;
- ``XP03``: Alice heard Bob, turn 3 · beat 1, resolved with both resolution ids null;
- ``XP04``: Bob heard the genesis opening (``source`` null), turn 0 · start, unresolved;
- ``XP05``: Alice caught part of what Pat said, turn 4 · beat 2, unresolved.

``exposures_page2.json`` (keyed on ``after_id=XP05``, C14) holds Bob's ``XP06`` and no cursor;
``exposures_empty.json`` no items."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.db import DatabaseError
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.inspect import AS_OF_INVALID, STATE_INVALID, TURN_TOO_HIGH
from console.inspect import history_turn_url
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE, CHARACTER_INVALID, CURSOR_DROPPED
from console.views import inspect as views
from console.views import inspect_exposures
from console.wording import UNKNOWN_CHARACTER
from tests.fakes.engine import FakeEngine
from tests.inspect.helpers import (
    ALICE,
    BOB,
    EFFECTIVE,
    FIXTURES,
    PAT,
    STORY,
    STORY_PATH,
    story_cast,
    structured_v2,
)
from tests.inspect.test_knowledge_shell import partitions_off
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

URL = f"/stories/{STORY}/inspect/knowledge/exposures/"
EXPOSURES = "/stories/{story_id}/inspect/exposures"
EXPOSURES_PATH = f"/stories/{STORY}/inspect/exposures"
PREDICATES = "/stories/{story_id}/predicates"
PREDICATES_PATH = f"/stories/{STORY}/predicates"
BELIEFS_URL = f"/stories/{STORY}/inspect/knowledge/beliefs/"
P = "0000000000000000000000"
XP = {n: P + f"XP{n:02d}" for n in range(1, 7)}
OUTSIDER = P + "ZZ99"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
BELIEF_TITLE = "Current belief in this proposition (may have changed since it resolved)"
RAW_READ = "A raw read: opening this list never resolves an exposure."
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "console" / "inspect"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return copy.deepcopy(body)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", EFFECTIVE, 200, json=structured_v2())
    fake.respond("GET", STORY_PATH, 200, json=story_cast())
    fake.respond("GET", PREDICATES, 200, json=fixture("predicates_list"))
    fake.respond("GET", EXPOSURES, 200, json=fixture("exposures_page1"))
    fake.respond("GET", EXPOSURES, 200, json=fixture("exposures_page2"), query={"after_id": XP[5]})
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == EXPOSURES_PATH]


def registry_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == PREDICATES_PATH]


def sent(engine: FakeEngine) -> dict[str, str]:
    (call,) = reads(engine)
    return dict(call.url.params)


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("GET", EXPOSURES, status, json=body)


def row(html: str, exposure_id: str) -> str:
    found = re.search(rf'<tr id="x-{exposure_id}" class="exposure-row">(.*?)</tr>', html, re.S)
    assert found is not None, exposure_id
    return found.group(1)


def cell(row_html: str, cls: str) -> str:
    found = re.search(rf'<td class="{cls}">(.*?)</td>', row_html, re.S)
    assert found is not None, cls
    return found.group(1)


def text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def field(html: str, name: str) -> str:
    found = re.search(rf'<div class="field field-{name}">(.*?)</div>', html, re.S)
    assert found is not None, name
    return found.group(1)


def options(html: str, name: str = "character") -> list[tuple[str, str]]:
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', field(html, name))


def selected(html: str, name: str = "character") -> str:
    found = re.search(r'<option value="([^"]*)"[^>]*selected', field(html, name))
    assert found is not None
    return found.group(1)


def filters_form(html: str) -> str:
    start = html.index('<form method="get" id="exposures-filters"')
    return html[start : html.index("</form>", start)]


def count(html: str) -> str | None:
    found = re.search(r'<p class="exposures-count">(.*?)</p>', html, re.S)
    return None if found is None else found.group(1)


def logged(operation_id: str) -> EngineOperation:
    return EngineOperation.objects.create(
        kind="write_belief",
        http_method="POST",
        path=f"/stories/{STORY}/beliefs",
        http_status=200,
        outcome="ok",
        engine_operation_id=operation_id,
        story_id=STORY,
    )


def belief_link(witness: str, n: int) -> str:
    href = f"{BELIEFS_URL}?character={witness}&amp;proposition_id={P}PX{n:02d}"
    return f'<a class="belief-link" href="{href}" title="{BELIEF_TITLE}">belief</a>'


# --- the route and its reads ---------------------------------------------------------------------


def test_reads_in_order_and_sub_nav(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        EXPOSURES_PATH,
        PREDICATES_PATH,
    ]
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100"}
    assert f'<a class="active" aria-current="page" href="{URL}">Exposures</a>' in html
    heading = html.index("<h2>Exposures</h2>")
    assert heading < html.index(RAW_READ) < html.index('<form method="get" id="exposures-filters"')


def test_operation_links_over_both_ids(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def recorded(story_id: str, ids: Any) -> dict[str, int]:
        calls.append(list(ids))
        return {}

    monkeypatch.setattr(inspect_exposures, "operation_links", recorded)
    page(operator_client)
    assert calls == [[P + "QX01", P + "QX02", P + "QR02", P + "QX03", P + "QX04", P + "QX05"]]


def test_greyed_tab_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=partitions_off())
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert reads(engine) == []
    assert registry_reads(engine) == []


def test_empty_page_no_registry_or_links_read(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer(engine, 200, fixture("exposures_empty"))
    calls: list[Any] = []

    def recorded(*args: Any) -> dict[str, int]:
        calls.append(args)
        return {}

    monkeypatch.setattr(inspect_exposures, "operation_links", recorded)
    html = page(operator_client)
    assert registry_reads(engine) == []
    assert calls == []
    assert count(html) == "0 exposures · revision 7"


def test_read_only(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client)
    page(operator_client, f"?after_id={XP[5]}")
    assert engine.requests
    assert {r.method for r in engine.requests} == {"GET"}
    assert not [r for r in engine.requests if r.method in ("POST", "PATCH", "DELETE", "PUT")]
    before = len(engine.requests)
    assert operator_client.post(URL).status_code == 405
    assert len(engine.requests) == before


# --- rows ----------------------------------------------------------------------------------------


def test_row_columns(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    heads = re.findall(r"<th>([^<]*)</th>", html)
    assert heads == [
        "When",
        "Witness",
        "Heard",
        "From",
        "Caught",
        "State",
        "Resolved by",
        "More",
    ]
    assert re.findall(r'<tr id="x-([^"]+)"', html) == [XP[n] for n in range(1, 6)]
    alice = row(html, XP[1])
    assert cell(alice, "when") == (
        f'<a href="{escape(history_turn_url(STORY, 1))}">turn 1 · beat 2</a>'
    )
    assert cell(alice, "witness") == f'<span class="character" title="{ALICE}">Alice</span>'
    assert text(cell(alice, "heard")).startswith("…0000EN01 located_at …0000EN02")
    assert "<small" not in cell(alice, "heard")
    assert cell(alice, "from") == f'<span class="character" title="{BOB}">Bob</span>'
    assert cell(alice, "caught") == '<span title="full">Full</span>'
    assert cell(alice, "state") == '<span title="unresolved">Unresolved</span>'
    # complement: an unresolved row has no belief link and shows "—"
    assert cell(alice, "resolved-by") == "—"
    more = cell(alice, "more")
    for value in (XP[1], f"{P}PX01", f"{P}EV01", f"{P}QX01"):
        assert f"<code>{value}</code>" in more
    assert "<dt>Kind</dt><dd>content</dd>" in more
    assert "<dt>Step</dt><dd>0</dd>" in more
    assert '<pre class="raw-item">' in more
    assert "&quot;source_event_id&quot;" in more
    assert 'class="actions"' not in html

    bob = row(html, XP[2])
    variant = escape(history_turn_url(STORY, 2, 1))
    assert cell(bob, "when") == f'<a href="{variant}">turn 2 · v1 · beat 3</a>'
    assert "<dt>Step</dt><dd>40000</dd>" in cell(bob, "more")


def test_genesis_source_wording(operator_client: Client, engine: FakeEngine) -> None:
    genesis = row(page(operator_client), XP[4])
    assert cell(genesis, "from") == '<span class="muted genesis">Genesis opening</span>'
    assert cell(genesis, "when").endswith(">turn 0 · start</a>")


def test_partial_fidelity_title(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    partial = row(html, XP[5])
    title = "partial — the witness caught only part of the claim"
    assert cell(partial, "caught") == f'<span title="{title}">Partial</span>'
    assert cell(partial, "from") == f'<span class="character" title="{PAT}">Pat (player)</span>'


def test_unknown_enum_values_humanised(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("exposures_page2")
    body["items"][0].update(kind="envelope", fidelity="garbled_bits", state="half_resolved")
    answer(engine, 200, body)
    item = row(page(operator_client), XP[6])
    assert '<small class="kind" title="envelope">Envelope</small>' in cell(item, "heard")
    assert cell(item, "caught") == '<span title="garbled_bits">Garbled bits</span>'
    assert cell(item, "state") == '<span title="half_resolved">Half resolved</span>'
    assert cell(item, "resolved-by") == "—"


def test_resolved_row_links_belief(operator_client: Client, engine: FakeEngine) -> None:
    resolved = cell(row(page(operator_client), XP[2]), "resolved-by")
    assert resolved == (
        f'<span class="transition">Transition <code title="{P}TR02">…0000TR02</code></span>'
        f" · <code>{P}QR02</code> · {belief_link(BOB, 2)}"
    )
    assert "as_of" not in resolved


def test_resolved_without_ids_links_belief_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    resolved = cell(row(page(operator_client), XP[3]), "resolved-by")
    assert resolved == belief_link(ALICE, 3)


def test_belief_link_never_carries_as_of(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?as_of=9")
    assert "as_of" not in cell(row(html, XP[2]), "resolved-by")


def test_resolution_operation_link_when_logged(operator_client: Client, engine: FakeEngine) -> None:
    resolution = logged(f"{P}QR02")
    recorded = logged(f"{P}QX01")
    html = page(operator_client)
    assert (
        f'<a class="operation-link" href="/operations/{resolution.pk}/"><code>{P}QR02</code></a>'
    ) in cell(row(html, XP[2]), "resolved-by")
    assert (
        f'<a class="operation-link" href="/operations/{recorded.pk}/"><code>{P}QX01</code></a>'
    ) in cell(row(html, XP[1]), "more")
    assert f"<dd><code>{P}QX02</code></dd>" in cell(row(html, XP[2]), "more")  # complement


def test_operation_lookup_failure_leaves_codes(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged(f"{P}QR02")

    def broken(*args: Any) -> dict[str, int]:
        raise DatabaseError("down")

    monkeypatch.setattr(inspect_exposures, "operation_links", broken)
    html = page(operator_client)
    assert f" · <code>{P}QR02</code> · " in cell(row(html, XP[2]), "resolved-by")
    assert "operation-link" not in html


def test_registry_failure_shows_short_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert "located_at" not in text(cell(row(html, XP[1]), "heard"))
    assert 'class="engine-error"' not in html


def test_belief_link_left_out_without_beliefs_route(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = reverse

    def no_beliefs(name: str, *args: Any, **kwargs: Any) -> str:
        if name == "inspect:beliefs":
            raise NoReverseMatch(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(inspect_exposures, "reverse", no_beliefs)
    html = page(operator_client)
    assert "belief-link" not in html
    assert cell(row(html, XP[2]), "resolved-by").endswith(f"<code>{P}QR02</code>")
    assert cell(row(html, XP[3]), "resolved-by") == "—"


# --- filters -------------------------------------------------------------------------------------


def test_filter_fields(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert options(html) == [("", "Any"), (ALICE, "Alice"), (BOB, "Bob")]
    assert options(html, "state") == [
        ("", "Any"),
        ("unresolved", "Unresolved"),
        ("resolved", "Resolved"),
    ]
    assert "Witness" in field(html, "character")
    as_of = field(html, "as_of")
    assert 'type="number"' in as_of
    assert 'step="1"' in as_of
    assert "min=" not in as_of
    assert "max=" not in as_of
    assert "State as the record stood through this turn" in as_of
    form = filters_form(html)
    assert form.index("field-character") < form.index("field-state") < form.index("field-as_of")
    assert form.index("field-as_of") < form.index(">Apply</button>")


def test_filters_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?character={BOB.lower()}&state=resolved&as_of=3")
    assert sent(engine) == {
        "viewpoint": "omniscient",
        "limit": "100",
        "character": BOB,
        "state": "resolved",
        "as_of": "3",
    }
    assert selected(html) == BOB
    assert selected(html, "state") == "resolved"


def test_any_and_blank_send_nothing(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, "?character=&state=&as_of=")
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100"}


def test_as_of_zero_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, "?as_of=0")
    assert sent(engine)["as_of"] == "0"


@pytest.mark.parametrize("state", ["open", "resolved ", "%20resolved"])
def test_bad_state_zero_calls(operator_client: Client, engine: FakeEngine, state: str) -> None:
    html = page(operator_client, f"?state={state}")
    assert STATE_INVALID in field(html, "state")
    assert reads(engine) == []
    assert count(html) is None


def test_state_not_case_folded(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?state=Resolved")
    assert STATE_INVALID in field(html, "state")
    assert reads(engine) == []


@pytest.mark.parametrize(
    ("query", "error"),
    [
        ("?as_of=-1", AS_OF_INVALID),
        ("?as_of=1.5", AS_OF_INVALID),
        ("?as_of=%EF%BC%91", AS_OF_INVALID),
        ("?as_of=2147483648", TURN_TOO_HIGH),
    ],
)
def test_bad_turn_zero_calls(
    operator_client: Client, engine: FakeEngine, query: str, error: str
) -> None:
    html = page(operator_client, query)
    assert escape(error) in field(html, "as_of")
    assert reads(engine) == []
    assert count(html) is None


def test_lowercase_witness_selects_cast_option(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?character=+{ALICE.lower()}+")
    assert selected(html) == ALICE
    assert len(options(html)) == 3
    assert sent(engine)["character"] == ALICE


def test_malformed_witness_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?character=alice")
    assert escape(CHARACTER_INVALID) in field(html, "character")
    assert reads(engine) == []


def test_player_witness_kept_and_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?character={PAT.lower()}")
    assert options(html)[-1] == (PAT, "Pat (player)")
    assert selected(html) == PAT
    assert sent(engine)["character"] == PAT


def test_unknown_witness_beside_field(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        422,
        error_json(
            "validation_error", "bad witness", field="query.character", reason="unknown_character"
        ),
    )
    html = page(operator_client, f"?character={OUTSIDER}")
    assert options(html)[-1] == (OUTSIDER, "…0000ZZ99 (not in the cast)")
    assert selected(html) == OUTSIDER
    assert sent(engine)["character"] == OUTSIDER
    assert escape(UNKNOWN_CHARACTER) in field(html, "character")
    assert count(html) is None


@pytest.mark.parametrize(("name", "query"), [("state", "?state=resolved"), ("as_of", "?as_of=2")])
def test_other_422_beside_its_field(
    operator_client: Client, engine: FakeEngine, name: str, query: str
) -> None:
    answer(engine, 422, error_json("validation_error", "refused here", field=f"query.{name}"))
    html = page(operator_client, query)
    assert "refused here" in field(html, name)
    assert count(html) is None


def test_character_422_without_reason_uses_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, 422, error_json("validation_error", "odd witness", field="query.character"))
    html = page(operator_client, f"?character={BOB}")
    assert "odd witness" in field(html, "character")


def test_after_id_422_above_filters_with_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        EXPOSURES,
        422,
        json=error_json("validation_error", "stale cursor", field="query.after_id"),
        query={"after_id": XP[5]},
    )
    html = page(operator_client, f"?state=unresolved&after_id={XP[5]}")
    errors = html[html.index('<div class="errors"') : html.index('<form method="get"')]
    assert "stale cursor" in errors
    assert '<a class="first-page" href="?state=unresolved">First page</a>' in errors


def test_422_without_field_above_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, error_json("validation_error", "nope"))
    html = page(operator_client)
    errors = html[html.index('<div class="errors"') : html.index('<form method="get"')]
    assert "nope" in errors
    assert "First page" not in errors


def test_story_read_failed_free_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, f"?character={BOB.lower()}")
    witness = field(html, "character")
    assert '<input type="text" name="character"' in witness
    assert 'size="30"' in witness
    assert f'value="{BOB}"' in witness
    assert sent(engine)["character"] == BOB
    assert CAST_UNAVAILABLE in html
    assert html.index(CAST_UNAVAILABLE) < html.index('<table class="exposures">')
    assert f'<code title="{ALICE}">' in cell(row(html, XP[1]), "witness")


def test_story_read_failed_malformed_witness(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, "?character=bob")
    assert escape(CHARACTER_INVALID) in field(html, "character")
    assert reads(engine) == []


# --- paging --------------------------------------------------------------------------------------


def test_paging_links(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?character={BOB.lower()}&state=unresolved")
    assert (
        f'<a class="next-page" href="?character={BOB}&amp;state=unresolved&amp;after_id={XP[5]}">'
        "Next page</a>"
    ) in html
    assert "First page" not in html
    second = page(operator_client, f"?after_id={XP[5]}")
    (call,) = [r for r in reads(engine) if r.url.params.get("after_id") == XP[5]]
    assert dict(call.url.params) == {"viewpoint": "omniscient", "limit": "100", "after_id": XP[5]}
    assert '<a class="first-page" href="?">First page</a>' in second
    assert "Next page" not in second
    assert re.findall(r'<tr id="x-([^"]+)"', second) == [XP[6]]


def test_apply_drops_cursor(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?after_id={XP[5]}")
    form = filters_form(html)
    assert "after_id" not in form


def test_bad_after_id_dropped_not_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?after_id=nope&state=bad")
    assert f'<p class="notice cursor-dropped">{CURSOR_DROPPED}</p>' in html
    assert STATE_INVALID in field(html, "state")  # shown alongside field errors
    assert html.index("cursor-dropped") < html.index('<form method="get"')
    assert reads(engine) == []
    lower = page(operator_client, f"?after_id={XP[5].lower()}")  # no case folding
    assert "after_id" not in sent(engine)
    assert "cursor-dropped" in lower
    section = lower[lower.index("<h2>Exposures</h2>") :]
    assert XP[5].lower() not in section  # absent from every link


# --- counts and empty states ---------------------------------------------------------------------


def test_count_label(operator_client: Client, engine: FakeEngine) -> None:
    assert count(page(operator_client)) == "5 exposures on this page · revision 7"
    assert count(page(operator_client, f"?after_id={XP[5]}")) == (
        "1 exposure on this page · revision 7"
    )
    answer(engine, 200, fixture("exposures_page2"))
    assert count(page(operator_client)) == "1 exposure · revision 7"
    assert count(page(operator_client, "?as_of=5")) == "1 exposure matching filters · revision 7"
    assert count(page(operator_client, "?state=resolved")) == (
        "1 exposure matching filters · revision 7"
    )
    assert count(page(operator_client, f"?character={BOB}")) == (
        "1 exposure matching filters · revision 7"
    )


def test_count_both_qualifiers(operator_client: Client, engine: FakeEngine) -> None:
    assert count(page(operator_client, f"?character={ALICE}")) == (
        "5 exposures on this page, matching filters · revision 7"
    )


@pytest.mark.parametrize(
    ("query", "line"),
    [
        ("", "No exposures recorded"),
        (f"?character={ALICE}", "No exposures match these filters"),
        ("?state=unresolved", "No exposures match these filters"),
        ("?as_of=0", "No exposures match these filters"),
        (
            f"?character={ALICE}&after_id={XP[5]}",
            "No exposures on this page — the list may have changed since the last page.",
        ),
        (
            f"?after_id={XP[5]}",
            "No exposures on this page — the list may have changed since the last page.",
        ),
    ],
)
def test_empty_states(operator_client: Client, engine: FakeEngine, query: str, line: str) -> None:
    empty = fixture("exposures_empty")
    answer(engine, 200, empty)
    engine.respond("GET", EXPOSURES, 200, json=empty, query={"after_id": XP[5]})
    html = page(operator_client, query)
    assert f'<p class="none exposures-none">{escape(line)}</p>' in html
    assert count(html) is not None


def test_no_verified_in_templates() -> None:
    for name in ("exposures.html", "_exposure_row.html"):
        assert "verified" not in (TEMPLATES / name).read_text().lower()


# --- errors --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"), [(422, "viewpoint_unresolved"), (403, "omniscient_required")]
)
def test_viewpoint_unresolved_text(
    operator_client: Client, engine: FakeEngine, status: int, code: str
) -> None:
    answer(engine, status, error_json(code, "omniscient please"))
    html = page(operator_client)
    assert '<p class="omniscient-required">This read needs the omniscient viewpoint</p>' in html
    assert count(html) is None
    assert field(html, "character")


def test_404(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 404, error_json("not_found", "gone"))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert count(html) is None
    assert field(html, "character")


def test_503_panel_keeps_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 503, error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert 'class="engine-error"' in html
    assert html.index('<form method="get"') < html.index('class="engine-error"')
    assert count(html) is None


def test_erasure_pending_held(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 503, error_json("erasure_pending", "held"))
    response = operator_client.get(URL)
    assert response.status_code == 503


def test_module_off_greyed(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        409,
        error_json(
            "module_off", "no partitions", module="character_knowledge.partitions", story_id=STORY
        ),
    )
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert "configuration changed since this page loaded" in html
    assert 'class="engine-error"' not in html
