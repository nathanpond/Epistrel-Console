"""Erase a subject after a dry run with confirmed candidates (#86)."""

import copy
import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core import signing
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import erasure
from console.views.erasure import (
    CANDIDATES_CHANGED,
    CONFIRM_TEXT,
    LEDGER_UNAVAILABLE,
    NO_ANSWER,
    NO_CONFIG_KEY,
    NO_ERASURE_ID,
    NOTHING,
    STORY_CHANGED,
    TOO_MANY_TICKED,
    UNKNOWN_STORY,
    UNKNOWN_SUBJECT,
    residue,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import error, text
from tests.stories.helpers import summary

pytestmark = pytest.mark.django_db

ERASE = "/admin/erasure"
STORIES = "/stories"
STORY_PATH = "/stories/{story_id}"
CHARACTERS = "/catalog/characters"
PERSONAS = "/personas"
NEW = "/erasure/new/"
PREVIEW = "/erasure/preview/"
RUN = "/erasure/run/"
STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"  # the get_story fixture's
HELD_STORY = "01JB2Z8Y5Q3W6K9M1N4P7HELD1"
OTHER_STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2W"
MARA = "01JB2Z8Y5Q3W6K9M1N4P7R0T3A"
TOBIN = "01JB2Z8Y5Q3W6K9M1N4P7R0T3B"
ARI = "01JP0000000000000000PERSA1"
ALDRIC = "01JC0000000000000000ALDR1C"
DRAFTY = "01JC0000000000000000DRAFTY"
OLDIE = "01JC0000000000000000ARCH1V"
ERASURE_ID = "01JE0000000000000000ERAS01"
CAND1 = "a" * 32
CAND2 = "b" * 32
CAND3 = "c" * 32
SNIPPET1 = "Ari said the tide would turn."
SNIPPET2 = "Ari? No, the other one."
SNIPPET3 = "Ari laughed."


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the erasure pages talk to, through the real recording client."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(erasure, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


# --- fixtures -------------------------------------------------------------------------------------


def candidate(candidate_id: str, snippet: str, table: str = "catalog.scenarios") -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "record_table": table,
        "record_id": "01JS0000000000000000SCEN01",
        "field": "world.description",
        "snippet": snippet,
        "rule": "candidate",
    }


def dry_run(**changes: Any) -> dict[str, Any]:
    """A catalog-scope dry run for the persona Ari: 1 persona, 2 media, three candidates (two in
    scenarios, one in a character), one referencing story."""
    body: dict[str, Any] = {
        "config_revision": 7,
        "dry_run": True,
        "subject": {"kind": "persona", "id": ARI},
        "scope": "catalog",
        "story_id": None,
        "records": {"catalog.personas": 1, "catalog.scenarios": 0},
        "prose_segments": 3,
        "media": 2,
        "idempotency_results": 0,
        "regeneration": "redaction",
        "degradation": [],
        "referencing_stories": [{"story_id": STORY, "character_ids": [TOBIN]}],
        "erasure_id": None,
        "operation_id": None,
        "idempotency_key": None,
        "ledger_entry": None,
        "residue": None,
        "candidates_remaining": 0,
        "candidates": [
            candidate(CAND1, SNIPPET1),
            candidate(CAND2, SNIPPET2, "catalog.characters"),
            candidate(CAND3, SNIPPET3),
        ],
    }
    body.update(changes)
    return body


def nothing_run(**changes: Any) -> dict[str, Any]:
    return dry_run(
        records={},
        prose_segments=0,
        media=0,
        candidates=[],
        referencing_stories=[],
        **changes,
    )


def real_run(**changes: Any) -> dict[str, Any]:
    body = dry_run(
        dry_run=False,
        erasure_id=ERASURE_ID,
        operation_id="01JE0000000000000000OPER01",
        idempotency_key="k-run",
        candidates=[],
        candidates_remaining=1,
        residue=0,
        ledger_entry={
            "erasure_id": ERASURE_ID,
            "at": "2026-10-25T10:00:00Z",
            "scope": "catalog",
            "story_id": None,
            "counts": {"records": {"catalog.personas": 1}, "media": 2, "confirmed": [CAND1]},
        },
    )
    body.update(changes)
    return body


def story_run(**changes: Any) -> dict[str, Any]:
    return real_run(
        subject={"kind": "character", "id": TOBIN},
        scope="story",
        story_id=STORY,
        referencing_stories=[],
        records={"characters": 1},
        timeline_revision=12,
        regeneration="model",
        ledger_entry=None,
        **changes,
    )


def sent(request: httpx.Request) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(request.content)
    return body


def erase_calls(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.path == ERASE]


def token_of(html: str) -> str:
    match = re.search(r'name="confirm_token" value="([^"]*)"', html)
    assert match, "no erase payload"
    return match.group(1)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def catalog_item(character_id: str, name: str, status: str) -> dict[str, Any]:
    item: dict[str, Any] = copy.deepcopy(engine_fixture("list_catalog_characters")["items"][0])
    item.update(id=character_id, name=name, status=status, owner_principal="alice")
    if status == "draft":
        item["version"] = None
    return item


def persona(persona_id: str, name: str, owner: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_persona"))
    return {**body, "id": persona_id, "name": name, "owner_principal": owner}


def answer_stories(engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        STORIES,
        200,
        json={
            "items": [
                summary(STORY, "Harbour", "active"),
                summary(HELD_STORY, None, "blocked_by_erasure"),
                summary(OTHER_STORY, None, "archived"),
            ]
        },
    )


def answer_reads(engine: FakeEngine) -> None:
    """Every read the form may make: stories, a story's cast, catalog characters (by status) and
    personas."""
    answer_stories(engine)
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))
    pages = {
        "published": [catalog_item(ALDRIC, "Sir Aldric", "published")],
        "draft": [catalog_item(DRAFTY, "Drafty", "draft"), catalog_item(ALDRIC, "Sir A", "draft")],
        "archived": [catalog_item(OLDIE, "Oldie", "archived")],
    }

    def characters(request: httpx.Request) -> httpx.Response:
        items = pages[request.url.params.get("status", "published")]
        return httpx.Response(200, json={"items": items, "next_cursor": None})

    engine.side_effect("GET", CHARACTERS, characters)
    engine.respond(
        "GET",
        PERSONAS,
        200,
        json={"items": [persona(ARI, "Ari", "bob")], "next_cursor": None},
    )


def preview(
    client: Client,
    engine: FakeEngine,
    answer: dict[str, Any] | None = None,
    **form: str,
) -> str:
    engine.respond("POST", ERASE, 200, json=answer or dry_run())
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))  # story_labels
    data = {"kind": "persona", "subject": ARI, "scope": "catalog", "story": "", **form}
    response = client.post(PREVIEW, data)
    assert response.status_code == 200
    return response.content.decode()


def section(html: str, element_id: str) -> str:
    start = html.index(f'id="{element_id}"')
    return html[start : html.index("</p>", start)] if "<p" in html[start - 10 : start] else ""


def options(html: str, name: str) -> list[tuple[str, str]]:
    match = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert match, f"no {name} select"
    return [
        (value, unescape(label))
        for value, label in re.findall(r'<option value="([^"]*)"[^>]*>([^<]*)</option>', match[1])
    ]


def selected(html: str, name: str) -> str | None:
    match = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert match
    found = re.search(r'<option value="([^"]*)" selected>', match[1])
    return found[1] or None if found else None


def scopes(html: str) -> list[str]:
    return re.findall(r'<input type="radio" name="scope" value="([^"]*)"', html)


def checked_scope(html: str) -> str | None:
    found = re.search(r'<input type="radio" name="scope" value="([^"]*)" checked>', html)
    return found[1] if found else None


# --- the form -------------------------------------------------------------------------------------


def test_the_form_starts_with_the_kind_only(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(NEW).content.decode()
    assert options(html, "kind") == [
        ("", "— choose —"),
        ("character", "Story character"),
        ("catalog_character", "Catalog character"),
        ("persona", "Persona"),
    ]
    assert 'name="subject"' not in html
    assert scopes(html) == []
    assert "confirm_token" not in html  # no way to erase from the form
    assert engine.requests == []


def test_a_story_character_is_offered_the_story_scope_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    html = operator_client.get(NEW, {"kind": "character", "story": STORY}).content.decode()
    assert scopes(html) == ["story"]
    assert checked_scope(html) == "story"
    # Every story but the held one, labelled "<name or (unnamed)> · <status> · <id[:8]>".
    assert options(html, "story")[1:] == [
        (STORY, f"Harbour · active · {STORY[:8]}"),
        (OTHER_STORY, f"(unnamed) · archived · {OTHER_STORY[:8]}"),
    ]
    assert options(html, "subject")[1:] == [
        (MARA, f"Mara (ad-hoc) · {MARA[:8]}"),
        (TOBIN, f"Tobin (ad-hoc) · {TOBIN[:8]}"),
    ]
    assert [r.url.path for r in engine.requests] == [STORIES, f"/stories/{STORY}"]


def test_a_crafted_catalog_scope_for_a_story_character_falls_back_to_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    query = {"kind": "character", "story": STORY, "subject": TOBIN, "scope": "catalog"}
    html = operator_client.get(NEW, query).content.decode()
    assert scopes(html) == ["story"]
    assert checked_scope(html) == "story"
    assert selected(html, "subject") == TOBIN


@pytest.mark.parametrize("kind", ["catalog_character", "persona"])
def test_catalog_entries_are_offered_both_scopes_catalog_first(
    operator_client: Client, engine: FakeEngine, kind: str
) -> None:
    answer_reads(engine)
    html = operator_client.get(NEW, {"kind": kind}).content.decode()
    assert scopes(html) == ["story", "catalog"]
    assert checked_scope(html) == "catalog"
    assert "This story" in html
    assert "The catalog" in html


def test_catalog_characters_of_every_status_merged(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    html = operator_client.get(NEW, {"kind": "catalog_character"}).content.decode()
    assert options(html, "subject")[1:] == [
        (DRAFTY, f"Drafty — alice · draft · {DRAFTY[:8]}"),
        (OLDIE, f"Oldie — alice · archived · {OLDIE[:8]}"),
        (ALDRIC, f"Sir Aldric — alice · published · {ALDRIC[:8]}"),
    ]
    listed = [r.url.params.get("status") for r in engine.requests if r.url.path == CHARACTERS]
    assert listed == ["published", "draft", "archived"]


def test_personas_of_every_owner(operator_client: Client, engine: FakeEngine) -> None:
    answer_reads(engine)
    html = operator_client.get(NEW, {"kind": "persona", "subject": ARI}).content.decode()
    assert options(html, "subject")[1:] == [(ARI, f"Ari — bob · {ARI[:8]}")]
    assert selected(html, "subject") == ARI
    (listed,) = [r for r in engine.requests if r.url.path == PERSONAS]
    assert "owner" not in listed.url.params


def test_a_prefilled_subject_not_listed_is_kept(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    missing = "01JP0000000000000000G0NE01"
    html = operator_client.get(NEW, {"kind": "persona", "subject": missing}).content.decode()
    assert (missing, f"{missing} (not listed)") in options(html, "subject")
    assert selected(html, "subject") == missing


def test_changing_the_kind_clears_the_subject_and_resets_the_scope(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    query = {
        "kind": "character",
        "shown_kind": "persona",
        "story": STORY,
        "shown_story": STORY,
        "subject": ARI,
        "scope": "catalog",
    }
    html = operator_client.get(NEW, query).content.decode()
    assert selected(html, "subject") is None
    assert selected(html, "story") == STORY  # kept: a story character can use it
    assert checked_scope(html) == "story"


def test_changing_the_story_clears_a_story_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    query = {
        "kind": "character",
        "shown_kind": "character",
        "story": STORY,
        "shown_story": OTHER_STORY,
        "subject": TOBIN,
        "scope": "story",
    }
    html = operator_client.get(NEW, query).content.decode()
    assert selected(html, "subject") is None


def test_changing_the_story_keeps_a_persona(operator_client: Client, engine: FakeEngine) -> None:
    answer_reads(engine)
    query = {
        "kind": "persona",
        "shown_kind": "persona",
        "story": STORY,
        "shown_story": "",
        "subject": ARI,
        "scope": "story",
    }
    html = operator_client.get(NEW, query).content.decode()
    assert selected(html, "subject") == ARI
    assert checked_scope(html) == "story"


def test_htmx_swaps_the_subject_section_only(operator_client: Client, engine: FakeEngine) -> None:
    answer_reads(engine)
    response = operator_client.get(NEW, {"kind": "persona"}, headers={"HX-Request": "true"})
    html = response.content.decode()
    assert html.lstrip().startswith('<div id="subject-section">')
    assert "<nav" not in html
    assert 'hx-push-url="true"' in html
    assert 'hx-target="#subject-section"' in html


def test_a_failed_list_degrades_to_a_free_text_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    engine.respond("GET", PERSONAS, 503, json=error("database_unavailable", "down"))
    html = operator_client.get(NEW, {"kind": "persona", "subject": ARI}).content.decode()
    assert '<input type="text" name="subject" id="id_subject" value="' + ARI in html
    assert "Couldn't load the list (503 database_unavailable) — enter the id" in unescape(html)


def test_a_story_without_characters_disables_preview(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    empty = {**engine_fixture("get_story"), "characters": []}
    engine.respond("GET", STORY_PATH, 200, json=empty)
    html = operator_client.get(NEW, {"kind": "character", "story": STORY}).content.decode()
    assert "This story has no characters" in html
    assert re.search(r'<button type="submit" class="preview" disabled', html)


def test_a_failed_story_read_allows_a_typed_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_reads(engine)
    engine.respond("GET", STORY_PATH, 404, json=error("not_found", "no such story"))
    html = operator_client.get(NEW, {"kind": "character", "story": STORY}).content.decode()
    assert '<input type="text" name="subject"' in html
    assert "Couldn't load the list (404 not_found) — enter the id" in unescape(html)
    assert not re.search(r'class="preview" disabled', html)


@pytest.mark.parametrize(
    ("data", "field", "message"),
    [
        ({"kind": "persona", "subject": "", "scope": "catalog"}, "subject", "Choose a subject"),
        ({"kind": "persona", "subject": ARI, "scope": "story"}, "story", "Choose a story"),
        (
            {"kind": "persona", "subject": ARI, "scope": "story", "story": "nope"},
            "story",
            "Not a story id",
        ),
        (
            {"kind": "persona", "subject": "x" * 65, "scope": "catalog"},
            "subject",
            "At most 64 characters",
        ),
    ],
)
def test_console_checks_refuse_with_zero_erasure_calls(
    operator_client: Client, engine: FakeEngine, data: dict[str, str], field: str, message: str
) -> None:
    answer_reads(engine)
    html = operator_client.post(PREVIEW, {"story": "", **data}).content.decode()
    assert erase_calls(engine) == []
    block = html[html.index(f'id="f-{field}"') :]
    assert message in block[: block.index("</p>")]


def test_the_subject_is_trimmed(operator_client: Client, engine: FakeEngine) -> None:
    preview(operator_client, engine, subject=f"  {ARI}\t")
    (call,) = erase_calls(engine)
    assert sent(call)["subject"] == {"kind": "persona", "id": ARI}


# --- Erase… links ---------------------------------------------------------------------------------


def test_erase_link_prefills(operator_client: Client, engine: FakeEngine) -> None:
    from console.erasure import new_url

    url = new_url("character", TOBIN, scope="story", story=STORY)
    assert url is not None
    assert parse_qs(urlsplit(url).query) == {
        "kind": ["character"],
        "subject": [TOBIN],
        "scope": ["story"],
        "story": [STORY],
    }
    answer_reads(engine)
    html = operator_client.get(url).content.decode()
    assert selected(html, "story") == STORY
    assert selected(html, "subject") == TOBIN
    assert checked_scope(html) == "story"


@override_settings(ROOT_URLCONF="tests.urls_bare")
def test_no_erase_link_while_the_form_does_not_exist() -> None:
    from console.erasure import new_url
    from console.templatetags.erasure import erase_url

    assert new_url("persona", ARI, scope="catalog") is None
    assert erase_url("persona", ARI, "catalog") == ""


def test_erase_url_tag_refuses_unknown_kinds_and_scopes() -> None:
    from console.templatetags.erasure import erase_url

    assert erase_url("gadget", ARI, "catalog") == ""
    assert erase_url("persona", ARI, "world") == ""
    assert erase_url("persona", "", "catalog") == ""
    assert erase_url("persona", ARI, "catalog") == (
        f"/erasure/new/?kind=persona&subject={ARI}&scope=catalog"
    )


# --- the preview ----------------------------------------------------------------------------------


def test_preview_sends_a_dry_run_without_a_key(operator_client: Client, engine: FakeEngine) -> None:
    html = preview(operator_client, engine)
    (call,) = erase_calls(engine)
    assert sent(call) == {
        "subject": {"kind": "persona", "id": ARI},
        "scope": {"catalog": True},
        "dry_run": True,
    }
    assert "Idempotency-Key" not in call.headers
    page = text(html)
    assert "dry run, nothing has been erased yet" in page
    assert "<code>catalog.personas</code>: 1" in html
    assert "catalog.scenarios</code>: 0" not in html  # zero rows hidden
    assert "Prose segments 3" in page
    assert "Media purged, retired or rewritten 2" in page
    assert CONFIRM_TEXT in html
    assert ">Erase now</button>" in html


def test_a_story_scope_preview(operator_client: Client, engine: FakeEngine) -> None:
    answer = dry_run(subject={"kind": "character", "id": TOBIN}, scope="story", story_id=STORY)
    answer["referencing_stories"] = []
    html = preview(
        operator_client, engine, answer, kind="character", subject=TOBIN, scope="story", story=STORY
    )
    (call,) = erase_calls(engine)
    assert sent(call)["scope"] == {"story": STORY}
    assert "Images showing the subject (kept)" in html


def test_candidates_are_unticked_grouped_in_first_appearance_order(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))
    html = preview(operator_client, engine)
    boxes = re.findall(r'<input type="checkbox" name="candidate" value="([0-9a-f]+)"([^>]*)>', html)
    assert [b[0] for b in boxes] == [CAND1, CAND3, CAND2]  # scenarios group, then characters
    assert all('form="erase-form"' in attrs and "checked" not in attrs for _, attrs in boxes)
    assert html.index("<h3><code>catalog.scenarios</code></h3>") < html.index(
        "<h3><code>catalog.characters</code></h3>"
    )
    assert SNIPPET1 in unescape(html)
    assert "<code>world.description</code> · <code>01JS0000000000000000SCEN01</code>" in html
    assert 'id="erase-form"' in html
    assert "tick all" not in html.lower()


def test_preview_lists_referencing_stories_with_names(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))
    html = preview(operator_client, engine)
    referencing = html[html.index('<ul class="referencing">') :]
    assert f'<a href="/stories/{STORY}/"><code>{STORY}</code></a>' in referencing
    assert "Erase character" not in html  # only on the result page


def test_a_zero_count_dry_run_disables_erase_now(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview(operator_client, engine, nothing_run())
    assert NOTHING in html
    assert re.search(r'<button type="button" class="danger" disabled[^>]*>Erase now</button>', html)
    assert "confirm_token" not in html
    assert "Records Nothing" in text(html)


# --- the real run ---------------------------------------------------------------------------------


def test_erase_sends_exactly_the_ticked_ids_with_the_preview_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))
    html = preview(operator_client, engine)
    token = token_of(html)
    engine.respond("POST", ERASE, 200, json=real_run())
    response = operator_client.post(RUN, {"confirm_token": token, "candidate": [CAND3]})
    assert response.status_code == 302
    assert response["Location"] == f"/erasure/{ERASURE_ID}/"
    dry, real = erase_calls(engine)
    body = sent(real)
    assert body == {
        "subject": {"kind": "persona", "id": ARI},
        "scope": {"catalog": True},
        "dry_run": False,
        "confirm_candidates": [CAND3],
    }
    assert CAND1 not in body["confirm_candidates"]  # complement: unticked is absent
    key = twostage.unsign("erasure", token).base["key"]
    assert real.headers["Idempotency-Key"] == key
    assert "Idempotency-Key" not in dry.headers
    assert flashes(response) == [
        "Erased — key k-run, operation 01JE0000000000000000OPER01",
    ]


def test_erase_with_nothing_ticked_sends_no_candidates(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.respond("POST", ERASE, 200, json=real_run())
    operator_client.post(RUN, {"confirm_token": token})
    assert "confirm_candidates" not in sent(erase_calls(engine)[1])


def test_a_replayed_erase_says_so(operator_client: Client, engine: FakeEngine) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.respond("POST", ERASE, 200, json=real_run(), headers={"Idempotent-Replay": "true"})
    response = operator_client.post(RUN, {"confirm_token": token})
    assert flashes(response)[0].endswith("(replayed)")


def test_erase_without_a_payload_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(RUN, {"candidate": [CAND1]})
    assert response.status_code == 302
    assert response["Location"] == NEW
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert engine.requests == []


def test_get_on_the_run_url_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    assert operator_client.get(RUN).status_code == 405
    assert engine.requests == []


def test_a_tampered_or_expired_payload_makes_no_call(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.reset()
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    response = operator_client.post(RUN, {"confirm_token": tampered})
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    real_loads = signing.loads

    def aged(*args: Any, **kwargs: Any) -> Any:
        kwargs["max_age"] = -1
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(signing, "loads", aged)
    response = operator_client.post(RUN, {"confirm_token": token})
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert engine.requests == []


def test_another_kinds_payload_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    body = {"subject": {"kind": "persona", "id": ARI}, "scope": {"catalog": True}}
    token = twostage.sign("offboard", ARI, body, base={"offered": [], "key": "k"})
    response = operator_client.post(RUN, {"confirm_token": token})
    assert flashes(response) == [twostage.EXPIRED_TEXT]
    assert engine.requests == []


def test_a_ticked_id_not_offered_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.reset()
    html = operator_client.post(RUN, {"confirm_token": token, "candidate": [CAND1, "d" * 32]})
    assert CANDIDATES_CHANGED in html.content.decode()
    assert engine.requests == []


def test_more_than_1000_ticked_is_refused_above_the_candidates(
    operator_client: Client, engine: FakeEngine
) -> None:
    many = [f"{i:032x}" for i in range(1001)]
    answer = dry_run(
        referencing_stories=[], candidates=[candidate(c, f"Ari {i}.") for i, c in enumerate(many)]
    )
    token = token_of(preview(operator_client, engine, answer))
    engine.reset()
    response = operator_client.post(RUN, {"confirm_token": token, "candidate": many})
    html = response.content.decode()
    assert engine.requests == []
    assert TOO_MANY_TICKED in html
    assert html.index(TOO_MANY_TICKED) < html.index('name="candidate"')
    assert html.count(" checked>") == 1001  # the ticks are kept
    assert token_of(html) == token  # the same payload (and key) is offered again


def test_the_retry_after_no_answer_resends_the_same_key_and_ticks(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.fail("POST", ERASE, httpx.ReadTimeout("slow"))
    html = operator_client.post(RUN, {"confirm_token": token, "candidate": [CAND2]})
    page = html.content.decode()
    assert NO_ANSWER in unescape(page)
    retry = page[page.index('<form method="post" action="/erasure/run/" class="retry">') :]
    retry = retry[: retry.index("</form>")]
    assert f'value="{token}"' in retry
    assert f'name="candidate" value="{CAND2}"' in retry
    engine._failures.clear()
    engine.respond("POST", ERASE, 200, json=real_run())
    operator_client.post(RUN, {"confirm_token": token, "candidate": [CAND2]})
    first, second = erase_calls(engine)[1:]
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]
    assert sent(first) == sent(second)


def test_a_result_without_an_erasure_id_renders_inline(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.respond("POST", ERASE, 200, json=real_run(erasure_id=None, referencing_stories=[]))
    response = operator_client.post(RUN, {"confirm_token": token})
    assert response.status_code == 200
    assert NO_ERASURE_ID in unescape(response.content.decode())


# --- errors ---------------------------------------------------------------------------------------


def test_a_dry_run_404_shows_at_the_story(operator_client: Client, engine: FakeEngine) -> None:
    answer_reads(engine)
    engine.respond("POST", ERASE, 404, json=error("not_found", "no such story"))
    data = {"kind": "persona", "subject": ARI, "scope": "story", "story": STORY}
    html = operator_client.post(PREVIEW, data).content.decode()
    block = html[html.index('id="f-story"') :]
    assert UNKNOWN_STORY in block[: block.index("</p>")]


def test_a_real_run_404_on_the_subject_shows_at_the_subject(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    answer_reads(engine)
    missing = error("not_found", "no persona", subject_kind="persona", subject_id=ARI)
    engine.respond("POST", ERASE, 404, json=missing)
    html = operator_client.post(RUN, {"confirm_token": token}).content.decode()
    block = html[html.index('id="f-subject"') :]
    assert UNKNOWN_SUBJECT in block[: block.index("</p>")]
    assert selected(html, "subject") == ARI


def test_an_unknown_subjects_dry_run_has_nothing_to_erase(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = preview(operator_client, engine, nothing_run())
    assert NOTHING in html


def test_invalid_scope_shows_at_the_scope(operator_client: Client, engine: FakeEngine) -> None:
    answer_reads(engine)
    refusal = error("invalid_erasure_scope", "a story character can't be erased from the catalog")
    engine.respond("POST", ERASE, 422, json=refusal)
    data = {"kind": "character", "subject": TOBIN, "scope": "catalog", "story": STORY}
    html = operator_client.post(PREVIEW, data).content.decode()
    (call,) = erase_calls(engine)
    assert sent(call)["scope"] == {"catalog": True}  # a crafted POST reaches the Engine
    block = html[html.index('id="f-scope"') :]
    assert "can&#x27;t be erased from the catalog" in block[: block.index("</fieldset>")]


@pytest.mark.parametrize(
    ("status", "code", "message", "again"),
    [
        (409, "revision_conflict", STORY_CHANGED, True),
        (422, "unknown_erasure_candidate", CANDIDATES_CHANGED, True),
        (503, "ledger_unavailable", LEDGER_UNAVAILABLE, False),
        (503, "config_key_missing", NO_CONFIG_KEY, False),
    ],
)
def test_real_run_errors(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    message: str,
    again: bool,
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.respond("POST", ERASE, status, json=error(code, "refused"))
    html = operator_client.post(RUN, {"confirm_token": token}).content.decode()
    assert message in unescape(html)
    assert f"{status} {code}" in html
    has_again = 'action="/erasure/preview/" class="preview-again"' in html
    assert has_again is again
    if again:
        engine.respond("POST", ERASE, 200, json=dry_run(referencing_stories=[]))
        response = operator_client.post(PREVIEW, {"confirm_token": token})
        assert response.status_code == 200
        assert sent(erase_calls(engine)[-1])["dry_run"] is True
        assert "Erase now" in response.content.decode()


@pytest.mark.parametrize(
    ("code", "message"),
    [("ledger_unavailable", LEDGER_UNAVAILABLE), ("config_key_missing", NO_CONFIG_KEY)],
)
def test_dry_run_503s_are_worded_on_the_form(
    operator_client: Client, engine: FakeEngine, code: str, message: str
) -> None:
    answer_reads(engine)
    engine.respond("POST", ERASE, 503, json=error(code, "unavailable"))
    data = {"kind": "persona", "subject": ARI, "scope": "catalog", "story": ""}
    html = operator_client.post(PREVIEW, data).content.decode()
    assert message in html
    assert f"503 {code}" in html


def test_an_unlisted_code_shows_the_generic_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    token = token_of(preview(operator_client, engine, dry_run(referencing_stories=[])))
    engine.respond("POST", ERASE, 503, json=error("erasure_pending", "held"))
    html = operator_client.post(RUN, {"confirm_token": token}).content.decode()
    assert "503 erasure_pending" in html
    assert "preview-again" not in html


# --- the result -----------------------------------------------------------------------------------


def run_to_result(client: Client, engine: FakeEngine, answer: dict[str, Any]) -> str:
    token = token_of(preview(client, engine, dry_run(referencing_stories=[])))
    engine.respond("POST", ERASE, 200, json=answer)
    response = client.post(RUN, {"confirm_token": token, "candidate": [CAND1]}, follow=True)
    assert response.status_code == 200
    return response.content.decode()


def test_the_result_page_shows_everything(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))
    html = run_to_result(operator_client, engine, real_run())
    page = text(html)
    assert "Erased — key k-run" in page
    assert '<span class="residue residue-clean">0 — clean</span>' in html
    assert "<code>catalog.personas</code>: 1" in html
    assert "Candidates remaining 1" in page
    assert "Redacted text stands (no model available)" in page
    assert "Idempotency results 0" in page
    ledger = html[html.index('<dl class="ledger">') :]
    assert f"<code>{ERASURE_ID}</code>" in ledger
    assert '<dt>records</dt><dd><dl class="counts"><dt>catalog.personas</dt><dd>1</dd>' in ledger
    assert f"<dt>confirmed</dt><dd>{CAND1}</dd>" in ledger
    # candidates_remaining > 0: "Preview again" with the same subject and scope.
    assert f'href="/erasure/new/?kind=persona&amp;subject={ARI}&amp;scope=catalog"' in html


def test_referencing_stories_link_to_a_story_scope_erasure(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=engine_fixture("get_story"))
    html = run_to_result(operator_client, engine, real_run())
    link = re.search(r'<a class="erase-in-story" href="([^"]+)">([^<]+)</a>', html)
    assert link
    url = unescape(link[1])
    assert parse_qs(urlsplit(url).query) == {
        "kind": ["character"],
        "subject": [TOBIN],
        "story": [STORY],
        "scope": ["story"],
    }
    name = engine_fixture("get_story")["name"]
    assert unescape(link[2]) == f"Erase character {TOBIN[:8]} in {name}"
    answer_reads(engine)
    form = operator_client.get(url).content.decode()
    assert selected(form, "story") == STORY
    assert selected(form, "subject") == TOBIN
    assert checked_scope(form) == "story"


def test_a_story_scope_result(operator_client: Client, engine: FakeEngine) -> None:
    answer = story_run(degradation=["summary_regeneration_capped", "weird"], residue=2)
    html = run_to_result(operator_client, engine, answer)
    page = text(html)
    assert '<span class="residue residue-left">2</span>' in html
    assert "Summaries regenerated by the model" in page
    assert "Summary regeneration hit its cap <code>summary_regeneration_capped</code>" in html
    assert "weird <code>weird</code>" in html
    assert f'<a class="transcript" href="/stories/{STORY}/history/">revision 12</a>' in html
    assert "Images showing the subject (kept)" in html
    assert "Preview again" in html


@pytest.mark.parametrize(
    ("value", "shown", "css"),
    [
        (0, "0 — clean", "residue-clean"),
        (3, "3", "residue-left"),
        (None, "Not scanned", "residue-unscanned"),
    ],
)
def test_residue_colouring(value: int | None, shown: str, css: str) -> None:
    assert residue(value) == erasure.Residue(shown, css)


def test_an_unknown_erasure_id_is_404(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get("/erasure/01JE0000000000000000UNKN01/")
    assert response.status_code == 404
    assert (
        "This Console has no record of erasure 01JE0000000000000000UNKN01 "
        "(the Engine has no read-back)"
    ) in response.content.decode()
    assert engine.requests == []


def test_the_list_shows_real_runs_only(operator_client: Client, engine: FakeEngine) -> None:
    run_to_result(operator_client, engine, story_run())
    html = operator_client.get("/erasure/").content.decode()
    assert html.count(f'href="/erasure/{ERASURE_ID}/"') == 1
    assert EngineOperation.objects.filter(kind="erase").count() == 2  # the dry run, the run


# --- the operation log ----------------------------------------------------------------------------


def test_the_log_holds_no_snippet_text(operator_client: Client, engine: FakeEngine) -> None:
    run_to_result(operator_client, engine, real_run())
    dry, real = EngineOperation.objects.filter(kind="erase").order_by("id")
    for row in (dry, real):
        stored = json.dumps([row.request, row.response])
        for snippet in (SNIPPET1, SNIPPET2, SNIPPET3):
            assert snippet not in stored
    assert dry.request["body"]["dry_run"] is True
    assert real.request["body"]["dry_run"] is False
    response = dry.response
    assert isinstance(response, dict)
    assert [c["snippet"] for c in response["candidates"]] == ["***", "***", "***"]
    assert [c["candidate_id"] for c in response["candidates"]] == [CAND1, CAND2, CAND3]
    assert real.request["body"]["confirm_candidates"] == [CAND1]
    assert (dry.entity_type, dry.entity_id) == ("persona", ARI)
    assert dry.story_id == ""


def test_a_story_scope_row_names_the_story_and_the_story_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer = dry_run(subject={"kind": "character", "id": TOBIN}, scope="story", story_id=STORY)
    answer["referencing_stories"] = []
    preview(
        operator_client, engine, answer, kind="character", subject=TOBIN, scope="story", story=STORY
    )
    (row,) = EngineOperation.objects.filter(kind="erase")
    assert (row.entity_type, row.entity_id, row.story_id) == ("story_character", TOBIN, STORY)


def test_a_catalog_character_row_uses_the_registry_type(
    operator_client: Client, engine: FakeEngine
) -> None:
    preview(operator_client, engine, kind="catalog_character", subject=ALDRIC)
    (row,) = EngineOperation.objects.filter(kind="erase")
    assert (row.entity_type, row.entity_id) == ("character", ALDRIC)
