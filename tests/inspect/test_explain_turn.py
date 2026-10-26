"""Explain a past turn's retrieval (#142): "Explain retrieval" on a structured turn in history
(#52) and in the turn inspector (#48) opens the Retrieval explorer (#141) re-running that turn's
input as the character who acted, as of the turn before, under a banner saying it is
reconstructed and how it differs from the turn's own retrieval.

Runs on the real urlconf against the schema-validating fake Engine: the "Harbour" story (Mara
the player, Tobin) with head 7."""

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient, EngineError
from console.engine.models import HistoryItem, RespondResponse
from console.engine_access import engine_client_for
from console.history import ExplainQuery, explain_query, explain_url
from console.inspect import history_turn_url
from console.models import EngineOperation
from console.views import history as history_views
from console.views import inspect as inspect_views
from console.views import play as play_views
from console.views import stories as story_views
from console.views.play import error_inspector, result_inspector
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.fixtures import engine_fixture
from tests.inspect.helpers import EFFECTIVE, FIXTURES, STORY, STORY_PATH, structured
from tests.play.helpers import HTMX, MARA, TOBIN, error_json, respond_json, story_json

pytestmark = pytest.mark.django_db

HISTORY = "/stories/{story_id}/history"
VARIANTS = "/stories/{story_id}/variants"
SETTINGS = "/stories/{story_id}/settings"
RETRIEVE = "/stories/{story_id}/retrieve"
RETRIEVE_PATH = f"/stories/{STORY}/retrieve"
HISTORY_URL = f"/stories/{STORY}/history/"
RETRIEVAL_URL = f"/stories/{STORY}/inspect/retrieval/"
OP7 = "01JB2Z8Y5Q3W6K9M1N4P7R0OP7"
LITE = object()  # an item without ``propositions`` (a Lite turn's)
BANNER = "Reconstructed now, not recorded at the time."
STRUCTURED_CAPS = ["budgets", "structured", "models", "episodic"]


# --- Engine bodies ------------------------------------------------------------------------------


def item(
    turn: int,
    text: str,
    *,
    kind: str = "player_input",
    channel: str = "say",
    actor: str | None = TOBIN,
    message: str = "",
    propositions: Any = (),
    variant: int = 1,
    beat: int = 1,
    origin: str = "generated",
    redacted: bool = False,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "turn": turn,
        "variant": variant,
        "beat": beat,
        "kind": kind,
        "actor_character_id": actor,
        "render_character_ids": None,
        "channel": channel,
        "text": text,
        "operation_id": OP7,
        "message_id": message or f"01M5R0QNTZV2CSB478YYC5{turn:02d}{variant:02d}",
        "origin": origin,
        "redacted": redacted,
    }
    if propositions is not LITE:
        body["propositions"] = list(propositions)
    return body


def reply(turn: int, text: str = "Tobin shrugs.", *, variant: int = 1) -> dict[str, Any]:
    return item(
        turn,
        text,
        kind="message_rendered",
        channel="message",
        actor=None,
        message=f"01M5R0QNTZV2CSB478YYC5{turn:02d}R{variant}",
        propositions=LITE,  # the Engine sets ``propositions`` on beats and inputs only
        variant=variant,
        beat=3,
    )


def beat(turn: int, text: str = "Tobin sits.", *, actor: str = TOBIN) -> dict[str, Any]:
    """A structured turn's ``beat_do`` item: the Engine always sets its ``propositions``."""
    return item(
        turn,
        text,
        kind="beat_do",
        channel="do",
        actor=actor,
        message=f"01M5R0QNTZV2CSB478YYC5{turn:02d}B1",
        beat=2,
    )


def structured_turn(
    turn: int, say: str = "say", do: str = "do", *, actor: str = TOBIN, variant: int = 1
) -> list[dict[str, Any]]:
    """A structured turn: the input's say and do items (one message), then the reply."""
    return [
        item(turn, say, actor=actor, variant=variant),
        item(turn, do, channel="do", actor=actor, variant=variant, beat=2),
        reply(turn, variant=variant),
    ]


def do_only_turn(turn: int, do: str = "Wren sits", *, actor: str = TOBIN) -> list[dict[str, Any]]:
    """A structured turn whose input is do-only: the Engine sends the input without
    ``propositions`` (it carries no labels), so only the turn's beat marks it structured."""
    return [
        item(turn, do, channel="do", actor=actor, propositions=LITE),
        beat(turn, actor=actor),
        reply(turn),
    ]


def unseen_do_only_turn(turn: int, do: str = "Wren sits") -> list[dict[str, Any]]:
    """#181: a do-only turn at a character viewpoint that perceived none of its beats: the
    Engine leaves the ``beat_*`` items out, so no shown item carries ``propositions``."""
    return [item(turn, do, channel="do", actor=MARA, propositions=LITE), reply(turn)]


def history_body(items: list[dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("get_history")
    turns = sorted({(i["turn"], i["variant"]) for i in items})
    replies = {(i["turn"], i["variant"]): i for i in items if i["channel"] == "message"}
    body.update(
        items=items,
        turns=[
            {
                "turn": t,
                "variant": v,
                "message": (
                    {"message_id": replies[t, v]["message_id"], "text": replies[t, v]["text"]}
                    if (t, v) in replies
                    else None
                ),
            }
            for t, v in turns
        ],
        as_of=7,
        timeline_revision=8,
    )
    return body


def parsed(items: list[dict[str, Any]]) -> list[HistoryItem]:
    found = [HistoryItem.from_json(i) for i in items]
    assert all(found)
    return [i for i in found if i is not None]


# --- The fake Engine ----------------------------------------------------------------------------


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """One fake behind the history, play, story and Inspect views: head 7, turn 7 structured."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (history_views, play_views, story_views, inspect_views):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    fake.respond("GET", STORY_PATH, 200, json=story_json(next_turn_index=8))
    fake.respond("GET", VARIANTS, 200, json=engine_fixture("list_variants"))
    fake.respond("GET", SETTINGS, 200, json=engine_fixture("get_story_settings"))
    fake.respond("GET", HISTORY, 200, json=history_body(structured_turn(7)))
    # #147: a page showing a structured beat reads the predicate registry once.
    registry = json.loads((FIXTURES / "predicates_list.json").read_text())
    fake.respond("GET", "/stories/{story_id}/predicates", 200, json=registry)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def answer_inspect(engine: FakeEngine) -> None:
    """The Retrieval tab's reads: the structured effective config and a retrieve answer."""
    engine.respond("GET", EFFECTIVE, 200, json=structured())
    body = json.loads((FIXTURES / "retrieval.json").read_text())
    engine.respond("GET", RETRIEVE, 200, json=body)


def get(client: Client, url: str, **headers: str) -> str:
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    return response.content.decode()


def explain_links(html: str) -> list[str]:
    return [
        h.replace("&amp;", "&") for h in re.findall(r'<a class="explain-link" href="([^"]+)"', html)
    ]


def history_links(html: str) -> list[str]:
    found = re.findall(r'<a class="explain-history-link" href="([^"]+)"', html)
    return [h.replace("&amp;", "&") for h in found]


def query_of(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def retrieve_calls(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == RETRIEVE_PATH]


def inspector_of(html: str) -> str:
    return html.split('<div id="inspector">')[1]


def log_row(**fields: Any) -> EngineOperation:
    values: dict[str, Any] = {
        "operator_username": "nate",
        "principal": "console:nate",
        "story_id": STORY,
        "kind": "respond",
        "http_method": "POST",
        "path": f"/stories/{STORY}/respond",
        "request": {"body": {"say": "say", "do": "do"}, "headers": {}},
        "http_status": 200,
        "engine_operation_id": OP7,
        "timeline_revision": 8,
        "config_revision": 0,
        "idempotency_key": "01JB2Z8Y5Q3W6K9M1N4P7R0KY7",
        "outcome": "ok",
        "response": respond_json(turn=7, variant=1, capabilities=STRUCTURED_CAPS),
    }
    values.update(fields)
    return EngineOperation.objects.create(**values)


# --- explain_query / explain_url ----------------------------------------------------------------


def url_for(items: list[dict[str, Any]], turn: int = 7, variant: int | None = 1) -> str | None:
    found = explain_query(parsed(items), turn, variant)
    return explain_url(STORY, found) if found is not None else None


def test_a_structured_turns_link_re_runs_its_input_before_the_turn() -> None:
    url = url_for(structured_turn(7), variant=2)
    assert url is not None
    assert urlsplit(url).path == RETRIEVAL_URL
    assert urlsplit(url).query == urlencode(
        [("q", "say\ndo"), ("as_of", 6), ("actor", TOBIN), ("explain", 7), ("explain_variant", 2)]
    )
    assert [k for k, _ in parse_qsl(urlsplit(url).query)] == [
        "q",
        "as_of",
        "actor",
        "explain",
        "explain_variant",
    ]


@pytest.mark.parametrize("variant", [0, None])
def test_no_variant_parameter_below_1(variant: int | None) -> None:
    url = url_for(structured_turn(7), variant=variant)
    assert url is not None
    assert "explain_variant" not in query_of(url)
    assert query_of(url)["explain"] == ["7"]


def test_a_do_only_structured_turn_links_its_do_text() -> None:
    url = url_for(do_only_turn(7))
    assert url is not None
    assert query_of(url)["q"] == ["Wren sits"]
    assert query_of(url)["actor"] == [TOBIN]


def test_the_player_as_actor_is_sent_too() -> None:
    url = url_for(structured_turn(7, actor=MARA))
    assert url is not None
    assert query_of(url)["actor"] == [MARA]


def test_a_long_input_is_cut_to_2000_code_points() -> None:
    long = "é" * 3000
    found = explain_query(parsed(structured_turn(7, say=long, do="")), 7, 1)
    assert found is not None
    assert found.q == "é" * 2000


def test_an_empty_part_is_skipped_in_the_join() -> None:
    found = explain_query(parsed(structured_turn(7, say="", do="  walks  ")), 7, 1)
    assert found == ExplainQuery("  walks  ", 6, TOBIN, 7, 1)


def test_an_ingest_batch_uses_its_first_input_message() -> None:
    items = [
        item(7, "first", message="01M5R0QNTZV2CSB478YYC5AAA1", origin="ingested"),
        item(7, "second", message="01M5R0QNTZV2CSB478YYC5AAA2", origin="ingested", beat=2),
    ]
    found = explain_query(parsed(items), 7, 1)
    assert found is not None
    assert found.q == "first"


@pytest.mark.parametrize(
    ("items", "turn"),
    [
        pytest.param(structured_turn(0), 0, id="turn 0"),
        pytest.param(
            [item(7, "The tide comes in.", kind="narration", channel="narration", actor=None)],
            7,
            id="narration-only ingest",
        ),
        pytest.param(
            [
                item(7, "say", propositions=LITE),
                item(7, "do", channel="do", propositions=LITE, beat=2),
            ],
            7,
            id="lite turn",
        ),
        pytest.param(structured_turn(7, say="  ", do="\n"), 7, id="whitespace input"),
        pytest.param([item(7, "say", actor=None)], 7, id="null actor"),
        pytest.param([reply(7)], 7, id="input hidden"),
        pytest.param([beat(7), reply(7)], 7, id="input hidden, beat shown"),
        pytest.param(
            [item(7, "do", channel="do", propositions=LITE), reply(7)], 7, id="lite do-only turn"
        ),
    ],
)
def test_no_link_without_a_structured_input(items: list[dict[str, Any]], turn: int) -> None:
    assert explain_query(parsed(items), turn, 1) is None


def test_a_structured_story_links_a_do_only_turn_with_no_beat_in_view() -> None:
    found = explain_query(parsed(unseen_do_only_turn(7)), 7, 1, lambda: True)
    assert found == ExplainQuery("Wren sits", 6, MARA, 7, 1)


def test_a_lite_story_leaves_a_do_only_turn_unlinked() -> None:
    assert explain_query(parsed(unseen_do_only_turn(7)), 7, 1, lambda: False) is None


@pytest.mark.parametrize(
    ("items", "turn"),
    [
        pytest.param(structured_turn(7), 7, id="propositions in view"),
        pytest.param(do_only_turn(7), 7, id="beat in view"),
        pytest.param([reply(7)], 7, id="no input"),
        pytest.param(unseen_do_only_turn(0), 0, id="turn 0"),
    ],
)
def test_the_story_is_asked_only_when_the_items_do_not_say(
    items: list[dict[str, Any]], turn: int
) -> None:
    def never() -> bool:
        raise AssertionError("the story's pipeline was read")

    explain_query(parsed(items), turn, 1, never)


# --- History ------------------------------------------------------------------------------------


def test_history_shows_explain_retrieval_in_the_turn_header(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = get(operator_client, HISTORY_URL)
    header = re.search(r'<tr class="turn-header">.*?</tr>', html, re.S)
    assert header is not None
    assert 'class="explain-link"' in header.group(0)
    (link,) = explain_links(html)
    assert link == url_for(structured_turn(7))
    assert retrieve_calls(engine) == []


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([item(7, "say", propositions=LITE), reply(7)], id="lite"),
        # The Engine's actor filter leaves out Tobin's input; Mara's beat still marks the turn.
        pytest.param([beat(7, actor=MARA), reply(7)], id="actor filter hides the input"),
    ],
)
def test_history_has_no_link_without_a_structured_input(
    operator_client: Client, engine: FakeEngine, items: list[dict[str, Any]]
) -> None:
    engine.respond("GET", HISTORY, 200, json=history_body(items))
    html = get(operator_client, f"{HISTORY_URL}?actor_character_id={MARA}")
    (sent,) = [r for r in engine.requests if r.url.path == f"/stories/{STORY}/history"]
    assert sent.url.params.get("actor_character_id") == MARA  # #178: the filter is driven
    assert explain_links(html) == []


def test_history_links_a_do_only_structured_turn(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", HISTORY, 200, json=history_body(do_only_turn(7)))
    (link,) = explain_links(get(operator_client, HISTORY_URL))
    assert link == url_for(do_only_turn(7))


CHARACTER_VIEW = f"viewpoint=character:{MARA}"


def effective_calls(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == f"/stories/{STORY}/effective-config"]


def test_history_links_a_do_only_turn_with_no_beat_in_view_on_a_structured_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured())
    engine.respond("GET", HISTORY, 200, json=history_body(unseen_do_only_turn(7)))
    (link,) = explain_links(get(operator_client, f"{HISTORY_URL}?{CHARACTER_VIEW}"))
    assert query_of(link)["q"] == ["Wren sits"]
    assert query_of(link)["actor"] == [MARA]
    assert len(effective_calls(engine)) == 1


def test_history_reads_the_story_config_once_for_several_unseen_turns(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured())
    items = [*unseen_do_only_turn(5, "Mara waits"), *unseen_do_only_turn(7)]
    engine.respond("GET", HISTORY, 200, json=history_body(items))
    links = explain_links(get(operator_client, f"{HISTORY_URL}?{CHARACTER_VIEW}"))
    assert [query_of(u)["q"] for u in links] == [["Mara waits"], ["Wren sits"]]
    assert len(effective_calls(engine)) == 1


def test_history_reads_no_story_config_when_the_items_say(
    operator_client: Client, engine: FakeEngine
) -> None:
    (link,) = explain_links(get(operator_client, HISTORY_URL))
    assert link == url_for(structured_turn(7))
    assert effective_calls(engine) == []


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param({}, id="lite story"),
        pytest.param({"status": 503}, id="config read fails"),
    ],
)
def test_history_leaves_a_do_only_turn_with_no_beat_in_view_unlinked(
    operator_client: Client, engine: FakeEngine, answer: dict[str, Any]
) -> None:
    if answer:
        body = error_json("database_unavailable", "down")
        engine.respond("GET", EFFECTIVE, answer["status"], json=body)
    engine.respond("GET", HISTORY, 200, json=history_body(unseen_do_only_turn(7)))
    assert explain_links(get(operator_client, f"{HISTORY_URL}?{CHARACTER_VIEW}")) == []


def test_a_chain_view_has_no_link(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"{HISTORY_URL}?variant=7:2")
    assert "Viewing chain 7:2" in html
    assert explain_links(html) == []


# --- The turn inspector -------------------------------------------------------------------------


def test_the_transcript_turn_inspector_links_the_same_query(
    operator_client: Client, engine: FakeEngine
) -> None:
    (expected,) = explain_links(get(operator_client, HISTORY_URL))
    html = get(operator_client, f"/stories/{STORY}/?turn=7")
    assert explain_links(inspector_of(html)) == [expected]
    assert history_links(inspector_of(html)) == []
    assert retrieve_calls(engine) == []


def test_the_transcript_turn_inspector_links_a_do_only_structured_turn(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", HISTORY, 200, json=history_body(do_only_turn(7)))
    html = get(operator_client, f"/stories/{STORY}/?turn=7")
    assert explain_links(inspector_of(html)) == [url_for(do_only_turn(7))]


def test_the_transcript_turn_inspector_links_a_do_only_turn_with_no_beat_in_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", EFFECTIVE, 200, json=structured())
    engine.respond("GET", HISTORY, 200, json=history_body(unseen_do_only_turn(7)))
    html = get(operator_client, f"/stories/{STORY}/?turn=7&{CHARACTER_VIEW}")
    (link,) = explain_links(inspector_of(html))
    assert query_of(link)["q"] == ["Wren sits"]
    assert len(effective_calls(engine)) == 1  # the header's read, shared (#85)


def test_the_transcript_turn_inspector_leaves_it_unlinked_on_a_lite_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", HISTORY, 200, json=history_body(unseen_do_only_turn(7)))
    html = get(operator_client, f"/stories/{STORY}/?turn=7&{CHARACTER_VIEW}")
    assert explain_links(inspector_of(html)) == []


def test_a_logged_transcript_turn_links_in_the_revisions_block(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row()
    html = inspector_of(get(operator_client, f"/stories/{STORY}/?turn=7"))
    revisions = html.split('<dl class="revisions">')[1].split("</dl>")[0]
    assert "<dt>Retrieval</dt>" in revisions
    assert revisions.index("Engine operation") < revisions.index('class="explain-link"')


def test_erased_input_never_leaks_the_logged_request(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row(request={"body": {"say": "secret", "do": "secret"}, "headers": {}})
    items = [
        item(7, "[erased]", redacted=True),
        item(7, "[erased]", channel="do", redacted=True, beat=2),
        reply(7),
    ]
    engine.respond("GET", HISTORY, 200, json=history_body(items))
    history = get(operator_client, HISTORY_URL)
    (link,) = explain_links(history)
    assert "secret" not in link
    assert query_of(link)["q"] == ["[erased]\n[erased]"]
    turn = inspector_of(get(operator_client, f"/stories/{STORY}/?turn=7"))
    assert "secret" not in turn
    assert explain_links(turn) == [link]
    by_operation = get(operator_client, f"/stories/{STORY}/inspector/?operation={OP7}", **HTMX)
    assert "secret" not in by_operation
    assert explain_links(by_operation) == []
    assert history_links(by_operation) == [f"{HISTORY_URL}?before_turn=8#turn-7-1"]
    latest = inspector_of(get(operator_client, f"/stories/{STORY}/"))
    assert "secret" not in latest
    assert history_links(latest) == [f"{HISTORY_URL}?before_turn=8#turn-7-1"]
    assert retrieve_calls(engine) == []


def test_an_inspector_with_an_error_has_no_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    log_row(http_status=503, error_code="model_unavailable", outcome="error")
    html = get(operator_client, f"/stories/{STORY}/inspector/?operation={OP7}", **HTMX)
    assert history_links(html) == []
    assert explain_links(html) == []


def test_a_lite_report_has_no_history_link(operator_client: Client, engine: FakeEngine) -> None:
    log_row(response=respond_json(turn=7, variant=1))
    html = get(operator_client, f"/stories/{STORY}/inspector/?operation={OP7}", **HTMX)
    assert history_links(html) == []


def test_the_just_played_inspector_links_history() -> None:
    body = respond_json(turn=7, variant=2, capabilities=STRUCTURED_CAPS)
    result = RespondResponse.from_json(body)
    assert result is not None
    found = result_inspector(STORY, result, "key", OP7)
    assert found.history_url == history_turn_url(STORY, 7, 2)
    assert found.history_url.endswith("?before_turn=8#turn-7-2")
    assert found.explain_url == ""


def test_a_refused_turn_inspector_has_no_link() -> None:
    exc = EngineError("model_unavailable", "down", {"model_calls": [], "elapsed_ms": 1}, 503)
    found = error_inspector(exc, "key")
    assert found.history_url == found.explain_url == ""


# --- The reconstruction banner ------------------------------------------------------------------


def banner(html: str) -> str:
    found = re.search(r'<p class="notice explain-banner">(.*?)</p>', html, re.S)
    return found.group(1) if found else ""


def ask(client: Client, query: str) -> str:
    return get(client, f"{RETRIEVAL_URL}?{query}")


def test_the_banner_names_the_turn_the_differences_and_the_actor(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_inspect(engine)
    html = ask(operator_client, f"q=x&as_of=6&actor={TOBIN}&explain=7")
    text = banner(html)
    assert BANNER in text
    assert "may have changed since turn 7." in text
    assert "once per rendering character" in text
    assert "This re-run asks at the omniscient viewpoint as Tobin with the input alone." in text
    assert f'href="{HISTORY_URL}?before_turn=8#turn-7-0">Back to turn 7 in history</a>' in text
    form_end = html.index("</form>")
    assert form_end < html.index("explain-banner") < html.index('class="retrieval-header"')
    assert '<input type="hidden" name="explain" value="7"' in html


def test_the_banner_shows_the_variant(operator_client: Client, engine: FakeEngine) -> None:
    answer_inspect(engine)
    text = banner(ask(operator_client, "q=x&as_of=6&explain=7&explain_variant=2"))
    assert "since turn 7 · v2." in text
    assert "#turn-7-2" in text
    assert "as Mara (player)" in text


def test_an_invalid_variant_drops_only_the_variant(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_inspect(engine)
    text = banner(ask(operator_client, "q=x&as_of=6&explain=7&explain_variant=abc"))
    assert BANNER in text
    assert "· v" not in text


@pytest.mark.parametrize(
    "query",
    ["q=x&as_of=6", "q=x&as_of=6&explain=abc", "q=x&as_of=6&explain=0", "q=x&as_of=5&explain=7"],
)
def test_no_banner_otherwise(operator_client: Client, engine: FakeEngine, query: str) -> None:
    answer_inspect(engine)
    html = ask(operator_client, query)
    assert BANNER not in html
    assert 'class="retrieval-header"' in html  # no error either


def test_no_banner_without_a_question(operator_client: Client, engine: FakeEngine) -> None:
    answer_inspect(engine)
    assert BANNER not in ask(operator_client, "as_of=6&explain=7")
    assert retrieve_calls(engine) == []


def test_no_banner_when_the_engine_refuses_the_turn(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_inspect(engine)
    engine.respond(
        "GET",
        RETRIEVE,
        422,
        json=error_json("validation_error", "as_of out of range", field="query.as_of", head=5),
    )
    assert BANNER not in ask(operator_client, "q=x&as_of=6&explain=7")


def test_the_banner_sits_above_a_retrieve_error(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_inspect(engine)
    engine.respond("GET", RETRIEVE, 503, json=error_json("database_unavailable", "down"))
    html = ask(operator_client, "q=x&as_of=6&explain=7")
    assert BANNER in banner(html)
    assert 'class="retrieval-header"' not in html


# --- Calls --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("actor", "sent"), [(MARA, None), (TOBIN, TOBIN)])
def test_following_the_link_makes_exactly_one_retrieve(
    operator_client: Client, engine: FakeEngine, actor: str, sent: str | None
) -> None:
    answer_inspect(engine)
    engine.respond("GET", HISTORY, 200, json=history_body(structured_turn(7, actor=actor)))
    (link,) = explain_links(get(operator_client, HISTORY_URL))
    get(operator_client, f"/stories/{STORY}/?turn=7")
    assert retrieve_calls(engine) == []
    html = get(operator_client, link)
    assert BANNER in banner(html)
    (call,) = retrieve_calls(engine)
    params = parse_qs(call.url.query.decode())
    assert params["q"] == ["say\ndo"]
    assert params["as_of"] == ["6"]
    assert params.get("actor_character_id") == ([sent] if sent else None)
    assert "include_quarantined" not in params
