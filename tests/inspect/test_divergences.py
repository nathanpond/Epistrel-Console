"""The Knowledge tab's Divergences section (#192): the story's lies.

Runs against the real urlconf on the schema-validating fake Engine, under #188's Structured v2
configuration and the shared Alice/Bob/Pat cast (``story_cast.json``; Pat is the player).
``divergences_page1.json`` (revision 7, ``next_after_id`` ``DV03``):

- ``DV01``: Bob asserts ``located_at(…EN01, …EN02)`` (the key in the study) against his belief
  ``BE01`` / ``PB01`` (in the hall, ``…EN03``), turn 1 · beat 2, not revealed;
- ``DV02``: Alice, believed ``BE02``, turn 2 · v1 · beat 3 (step 40000), revealed by ``EV02``;
- ``DV03``: the off-cast speaker ``…ZZ99``, believed ``BE03``, turn 3 · beat 1.

``divergences_page2.json`` (keyed on ``after_id=DV03``, C14) holds Pat's ``DV04`` and no cursor;
``divergences_unsettled.json`` Bob's ``DV05`` with ``believed`` null;
``divergences_unparsed.json`` ``DV06`` whose ``asserted`` doesn't parse (C2)."""

import copy
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.db import DatabaseError
from django.http import HttpRequest, QueryDict
from django.template.loader import render_to_string
from django.test import Client, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils.html import escape

from console.engine.client import EngineClient
from console.engine.models import Divergences
from console.engine_access import engine_client_for
from console.forms.inspect import AS_OF_INVALID, SUBJECT_INVALID, TURN_TOO_HIGH
from console.inspect import history_turn_url
from console.models import EngineOperation
from console.paging import CAST_UNAVAILABLE, CHARACTER_INVALID, CURSOR_DROPPED
from console.views import inspect as views
from console.views import inspect_divergences
from console.views.inspect_divergences import divergence_rows
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

URL = f"/stories/{STORY}/inspect/knowledge/divergences/"
DIVERGENCES = "/stories/{story_id}/inspect/divergences"
DIVERGENCES_PATH = f"/stories/{STORY}/inspect/divergences"
PREDICATES = "/stories/{story_id}/predicates"
PREDICATES_PATH = f"/stories/{STORY}/predicates"
BELIEFS_URL = f"/stories/{STORY}/inspect/knowledge/beliefs/"
P = "0000000000000000000000"
DV = {n: P + f"DV{n:02d}" for n in range(1, 7)}
KEY, STUDY, HALL = P + "EN01", P + "EN02", P + "EN03"
OUTSIDER = P + "ZZ99"
EVENT = P + "EV02"
NOT_FOUND = "Not found (it may have been deleted or truncated)"
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
    fake.respond("GET", DIVERGENCES, 200, json=fixture("divergences_page1"))
    fake.respond(
        "GET", DIVERGENCES, 200, json=fixture("divergences_page2"), query={"after_id": DV[3]}
    )
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def page(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == DIVERGENCES_PATH]


def registry_reads(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == PREDICATES_PATH]


def sent(engine: FakeEngine) -> dict[str, str]:
    (call,) = reads(engine)
    return dict(call.url.params)


def answer(engine: FakeEngine, status: int, body: dict[str, Any]) -> None:
    engine.respond("GET", DIVERGENCES, status, json=body)


def row(html: str, divergence_id: str) -> str:
    found = re.search(rf'<tr id="d-{divergence_id}" class="divergence-row">(.*?)</tr>', html, re.S)
    assert found is not None, divergence_id
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


def options(html: str) -> list[tuple[str, str]]:
    return re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', field(html, "speaker"))


def selected(html: str) -> str:
    found = re.search(r'<option value="([^"]*)"[^>]*selected', field(html, "speaker"))
    assert found is not None
    return found.group(1)


def count(html: str) -> str | None:
    found = re.search(r'<p class="divergences-count">(.*?)</p>', html, re.S)
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


# --- the route and its reads ---------------------------------------------------------------------


def test_reads_in_order_and_sub_nav(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert [r.url.path for r in engine.requests] == [
        f"/stories/{STORY}/effective-config",
        f"/stories/{STORY}",
        DIVERGENCES_PATH,
        PREDICATES_PATH,
    ]
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100"}
    assert f'<a class="active" aria-current="page" href="{URL}">Divergences</a>' in html
    assert "<h2>Divergences</h2>" in html
    assert "Utterances whose speaker asserted something it doesn&#x27;t itself believe." in html
    assert '<form method="get" id="divergences-filters"' in html
    assert "Clear</a>" not in html


def test_greyed_tab_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=partitions_off())
    html = page(operator_client)
    assert '<div class="panel unavailable">' in html
    assert reads(engine) == []
    assert registry_reads(engine) == []


def test_empty_page_no_registry_read(operator_client: Client, engine: FakeEngine) -> None:
    body = fixture("divergences_page2")
    body["items"] = []
    answer(engine, 200, body)
    calls: list[Any] = []

    def recorded(*args: Any) -> dict[str, int]:
        calls.append(args)
        return {}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(inspect_divergences, "operation_links", recorded)
        html = page(operator_client)
    assert registry_reads(engine) == []
    assert calls == []
    assert count(html) == "0 divergences · revision 7"


def test_not_a_post_route(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.post(URL).status_code == 405


# --- rows ----------------------------------------------------------------------------------------


def test_row_columns(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    bob = row(html, DV[1])
    when = cell(bob, "when")
    assert f'<a href="{escape(history_turn_url(STORY, 1))}">turn 1 · beat 2</a>' == when
    assert f'<span class="character" title="{BOB}">Bob</span>' in cell(bob, "speaker")
    asserted = text(cell(bob, "asserted"))
    assert asserted.startswith("…0000EN01 located_at …0000EN02")
    believes = cell(bob, "believes")
    assert text(believes).startswith("…0000EN01 located_at …0000EN03")
    belief = f"{BELIEFS_URL}?character={BOB}&amp;proposition_id={P}PB01#b-{P}BE01"
    assert f'<a class="belief-link" href="{belief}">belief</a>' in believes
    assert cell(bob, "revealed") == '<span class="muted">Not revealed</span>'
    assert cell(bob, "operation") == f"<code>{P}QP01</code>"
    more = cell(bob, "more")
    for value in (f"{P}UT01", f"{P}PA01", f"{P}BE01"):
        assert f"<code>{value}</code>" in more
    assert "<dt>Step</dt><dd>0</dd>" in more
    assert '<pre class="raw-item">' in more
    assert "&quot;utterance_id&quot;" in more
    assert 'class="actions"' not in html

    alice = row(html, DV[2])
    variant = escape(history_turn_url(STORY, 2, 1))
    assert cell(alice, "when") == f'<a href="{variant}">turn 2 · v1 · beat 3</a>'
    revealed = cell(alice, "revealed")
    assert (
        '<span class="revealed" title="The event of the reveal that settled it">Revealed</span>'
        in (revealed)
    )
    assert f'<code title="{EVENT}">…0000EV02</code>' in revealed
    assert "<dt>Step</dt><dd>40000</dd>" in cell(alice, "more")

    outsider = row(html, DV[3])
    assert f'<code title="{OUTSIDER}">…0000ZZ99</code>' in cell(outsider, "speaker")
    assert f"character={OUTSIDER}&amp;proposition_id={P}PB03#b-{P}BE03" in cell(
        outsider, "believes"
    )


def test_column_order(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    heads = re.findall(r"<th>([^<]*)</th>", html)
    assert heads == ["When", "Speaker", "Asserted", "Believes", "Revealed", "Operation", "More"]
    ids = re.findall(r'<tr id="d-([^"]+)"', html)
    assert ids == [DV[1], DV[2], DV[3]]


def test_player_speaker_marked(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?after_id={DV[3]}")
    assert f'<span class="character" title="{PAT}">Pat (player)</span>' in cell(
        row(html, DV[4]), "speaker"
    )


def test_unsettled_lie_wording(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 200, fixture("divergences_unsettled"))
    believes = cell(row(page(operator_client), DV[5]), "believes")
    title = "The speaker holds no contrary belief on record: a lie no record settles"
    assert believes == f'<span class="muted unsettled" title="{title}">No record settles it</span>'
    assert "<a" not in believes  # complement: no belief link
    assert "<dt>Believed belief id</dt><dd>—</dd>" in cell(
        row(page(operator_client), DV[5]), "more"
    )


def test_unparsed_asserted_shows_dash_and_no_subject_link() -> None:
    """C2: an ``asserted`` the Console can't read (the Engine's schema allows none, C15, so this
    is a view-model test) shows "—", no "only this subject" link, and the raw item in More."""
    body = Divergences.from_json(fixture("divergences_unparsed"))
    assert body is not None
    (first,) = divergence_rows(
        STORY,
        body.items,
        cast=None,
        minor=frozenset(),
        base=QueryDict(mutable=True),
        speaker_filter="",
        subject_filter="",
        links={},
    )
    assert first.asserted is None
    assert first.subject_url is None
    html = render_to_string(
        "console/inspect/_divergence_row.html", {"row": first, "story_id": STORY}
    )
    item = row(html, DV[6])
    assert cell(item, "asserted") == "—"
    assert "only this subject" not in item
    assert '<pre class="raw-item">' in cell(item, "more")


def test_registry_failure_shows_short_ids(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", PREDICATES, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client)
    assert "located_at" not in text(cell(row(html, DV[1]), "asserted"))
    assert 'class="engine-error"' not in html


def test_operation_link_only_when_logged(operator_client: Client, engine: FakeEngine) -> None:
    op = logged(f"{P}QP02")
    html = page(operator_client)
    assert cell(row(html, DV[2]), "operation") == (
        f'<a class="operation-link" href="/operations/{op.pk}/"><code>{P}QP02</code></a>'
    )
    unlogged = cell(row(html, DV[1]), "operation")
    assert "<a" not in unlogged  # complement
    assert unlogged == f"<code>{P}QP01</code>"


def test_operation_lookup_failure_leaves_codes(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged(f"{P}QP02")

    def broken(*args: Any) -> dict[str, int]:
        raise DatabaseError("down")

    monkeypatch.setattr(inspect_divergences, "operation_links", broken)
    html = page(operator_client)
    assert cell(row(html, DV[2]), "operation") == f"<code>{P}QP02</code>"


def test_belief_link_left_out_without_beliefs_route(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = reverse

    def no_beliefs(name: str, *args: Any, **kwargs: Any) -> str:
        if name == "inspect:beliefs":
            raise NoReverseMatch(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(inspect_divergences, "reverse", no_beliefs)
    html = page(operator_client)
    assert "belief-link" not in html


# --- filters -------------------------------------------------------------------------------------


def test_speaker_options(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client)
    assert options(html) == [("", "Any"), (ALICE, "Alice"), (BOB, "Bob"), (PAT, "Pat (player)")]
    assert '<input type="text" name="subject" size="30"' in field(html, "subject")
    as_of = field(html, "as_of")
    assert 'type="number"' in as_of
    assert 'step="1"' in as_of
    assert "min=" not in as_of
    assert "max=" not in as_of
    assert "Blank for now" in as_of


def test_filters_sent_as_query(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?speaker={BOB.lower()}&subject={KEY.lower()}&as_of=3")
    assert sent(engine) == {
        "viewpoint": "omniscient",
        "limit": "100",
        "speaker": BOB,
        "subject": KEY,
        "as_of": "3",
    }
    assert selected(html) == BOB
    assert f'value="{KEY}"' in field(html, "subject")


def test_any_and_blank_send_nothing(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, "?speaker=&subject=++&as_of=")
    assert sent(engine) == {"viewpoint": "omniscient", "limit": "100"}


def test_as_of_zero_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    page(operator_client, "?as_of=0")
    assert sent(engine)["as_of"] == "0"


@pytest.mark.parametrize(
    ("query", "name", "error"),
    [
        ("?subject=not-an-id", "subject", SUBJECT_INVALID),
        ("?as_of=-1", "as_of", AS_OF_INVALID),
        ("?as_of=1.5", "as_of", AS_OF_INVALID),
        ("?as_of=2147483648", "as_of", TURN_TOO_HIGH),
    ],
)
def test_invalid_filter_zero_calls(
    operator_client: Client, engine: FakeEngine, query: str, name: str, error: str
) -> None:
    html = page(operator_client, query)
    assert escape(error) in field(html, name)
    assert reads(engine) == []
    assert count(html) is None


def test_lowercase_speaker_selects_cast_option(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?speaker=+{ALICE.lower()}+")
    assert selected(html) == ALICE
    assert len(options(html)) == 4
    assert sent(engine)["speaker"] == ALICE


def test_malformed_speaker_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?speaker=bob")
    assert escape(CHARACTER_INVALID) in field(html, "speaker")
    assert reads(engine) == []


def test_unknown_speaker_beside_field(operator_client: Client, engine: FakeEngine) -> None:
    answer(
        engine,
        422,
        error_json(
            "validation_error", "bad speaker", field="query.speaker", reason="unknown_character"
        ),
    )
    html = page(operator_client, f"?speaker={OUTSIDER}")
    assert options(html)[-1] == (OUTSIDER, "…0000ZZ99 (not in the cast)")
    assert selected(html) == OUTSIDER
    assert sent(engine)["speaker"] == OUTSIDER
    assert escape(UNKNOWN_CHARACTER) in field(html, "speaker")
    assert count(html) is None


def test_other_422_beside_its_field(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, error_json("validation_error", "subject refused", field="query.subject"))
    html = page(operator_client, f"?subject={KEY}")
    assert "subject refused" in field(html, "subject")


def test_after_id_422_above_filters_with_first_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        DIVERGENCES,
        422,
        json=error_json("validation_error", "stale cursor", field="query.after_id"),
        query={"after_id": DV[3]},
    )
    html = page(operator_client, f"?speaker={BOB}&after_id={DV[3]}")
    errors = html[html.index('<div class="errors"') : html.index('<form method="get"')]
    assert "stale cursor" in errors
    assert f'<a class="first-page" href="?speaker={BOB}">First page</a>' in errors


def test_422_without_field_above_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 422, error_json("validation_error", "nope"))
    html = page(operator_client)
    errors = html[html.index('<div class="errors"') : html.index('<form method="get"')]
    assert "nope" in errors
    assert "First page" not in errors


def test_story_read_failed_free_text(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, f"?speaker={BOB.lower()}")
    speaker = field(html, "speaker")
    assert '<input type="text" name="speaker"' in speaker
    assert 'size="30"' in speaker
    assert f'value="{BOB}"' in speaker
    assert sent(engine)["speaker"] == BOB
    assert CAST_UNAVAILABLE in html
    assert html.index(CAST_UNAVAILABLE) < html.index('<table class="divergences">')
    assert f'<code title="{ALICE}">' in cell(row(html, DV[2]), "speaker")
    # "only this …" links compare on normalised ids in text-field mode too
    assert "only this speaker" not in cell(row(html, DV[1]), "speaker")
    assert "only this speaker" in cell(row(html, DV[2]), "speaker")


def test_story_read_failed_malformed_speaker(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 503, json=error_json("database_unavailable", "down"))
    html = page(operator_client, "?speaker=bob")
    assert escape(CHARACTER_INVALID) in field(html, "speaker")
    assert reads(engine) == []


# --- paging and "only this …" links --------------------------------------------------------------


def test_paging_links(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?speaker={BOB.lower()}")
    assert f'<a class="next-page" href="?speaker={BOB}&amp;after_id={DV[3]}">Next page</a>' in html
    assert "First page" not in html
    second = page(operator_client, f"?after_id={DV[3]}")
    assert sent_for(engine, DV[3]) == {"viewpoint": "omniscient", "limit": "100", "after_id": DV[3]}
    assert '<a class="first-page" href="?">First page</a>' in second
    assert "Next page" not in second
    assert re.findall(r'<tr id="d-([^"]+)"', second) == [DV[4]]


def sent_for(engine: FakeEngine, cursor: str) -> dict[str, str]:
    (call,) = [r for r in reads(engine) if r.url.params.get("after_id") == cursor]
    return dict(call.url.params)


def test_bad_after_id_dropped_not_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, "?after_id=nope&subject=bad")
    assert f'<p class="notice cursor-dropped">{CURSOR_DROPPED}</p>' in html
    assert escape(SUBJECT_INVALID) in field(html, "subject")  # shown alongside field errors
    assert html.index("cursor-dropped") < html.index('<form method="get"')
    assert reads(engine) == []
    page(operator_client, "?after_id=nope")
    assert "after_id" not in sent(engine)


def test_only_this_speaker_link_drops_cursor(operator_client: Client, engine: FakeEngine) -> None:
    html = page(operator_client, f"?subject={KEY.lower()}&as_of=9&after_id={DV[3]}")
    speaker = cell(row(html, DV[4]), "speaker")
    assert (
        f'<a class="only-speaker" href="?subject={KEY}&amp;as_of=9&amp;speaker={PAT}">'
        "only this speaker</a>"
    ) in speaker
    asserted = cell(row(html, DV[4]), "asserted")
    assert (
        f'<a class="only-subject" href="?subject={STUDY}&amp;as_of=9">only this subject</a>'
    ) in asserted
    assert "only this subject" not in cell(row(html, DV[4]), "believes")


def test_only_links_hidden_when_already_filtered(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = page(operator_client, f"?speaker={BOB.lower()}&subject={KEY.lower()}")
    bob = row(html, DV[1])
    assert "only this speaker" not in cell(bob, "speaker")
    assert "only this subject" not in cell(bob, "asserted")
    alice = row(html, DV[2])
    assert "only this speaker" in cell(alice, "speaker")
    assert "only this subject" in cell(alice, "asserted")  # Alice's lie is about Bob


# --- counts and empty states ---------------------------------------------------------------------


def test_count_label(operator_client: Client, engine: FakeEngine) -> None:
    assert count(page(operator_client)) == "3 divergences on this page · revision 7"
    assert count(page(operator_client, f"?after_id={DV[3]}")) == (
        "1 divergence on this page · revision 7"
    )
    answer(engine, 200, fixture("divergences_unsettled"))
    assert count(page(operator_client)) == "1 divergence · revision 7"
    assert count(page(operator_client, "?as_of=5")) == "1 divergence matching filters · revision 7"
    assert count(page(operator_client, f"?subject={KEY}")) == (
        "1 divergence matching filters · revision 7"
    )


def test_count_both_qualifiers(operator_client: Client, engine: FakeEngine) -> None:
    assert count(page(operator_client, f"?speaker={BOB}")) == (
        "3 divergences on this page, matching filters · revision 7"
    )


@pytest.mark.parametrize(
    ("query", "line"),
    [
        ("", "No divergences recorded"),
        (f"?speaker={ALICE}", "No divergences match these filters"),
        ("?as_of=0", "No divergences match these filters"),
        (
            f"?speaker={ALICE}&after_id={DV[3]}",
            "No divergences on this page — the list may have changed since the last page.",
        ),
    ],
)
def test_empty_states(operator_client: Client, engine: FakeEngine, query: str, line: str) -> None:
    empty = fixture("divergences_page2")
    empty["items"] = []
    answer(engine, 200, empty)
    engine.respond("GET", DIVERGENCES, 200, json=empty, query={"after_id": DV[3]})
    html = page(operator_client, query)
    assert f'<p class="none divergences-none">{escape(line)}</p>' in html
    line_count = count(html)
    assert line_count is not None
    assert line_count.endswith("· revision 7")


def test_no_verified_in_templates() -> None:
    for name in ("divergences.html", "_divergence_row.html"):
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
    assert field(html, "speaker")


def test_404(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, 404, error_json("not_found", "gone"))
    html = page(operator_client)
    assert f'<p class="not-found">{NOT_FOUND}</p>' in html
    assert count(html) is None
    assert field(html, "speaker")


@pytest.mark.parametrize("code", ["database_unavailable", "ledger_unavailable"])
def test_503_panel_keeps_filters(operator_client: Client, engine: FakeEngine, code: str) -> None:
    answer(engine, 503, error_json(code, "down"))
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
