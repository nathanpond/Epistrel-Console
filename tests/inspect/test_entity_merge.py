"""#306: Merge entities (``inspect:entity-merge``, ``console/views/inspect_merge.py`` and
``console/forms/merge.py``), against the schema-validating fake Engine over #292's
``m7/story.json`` cast (Mara the player, Alice, Bob) and ``full_v4()``. The scene at the head is
an inline body in #58's shape (E16): the tavern (a place), a hooded stranger (a person) and an
unnamed object. The picker, the request body, the local refusals (no merge call, the posted key
kept), the outcomes, the Engine's refusals with D8's key rule, the Canon gate and the links."""

import copy
import json
import logging
import re
from collections.abc import Iterator
from html import unescape
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EXPECTED_REVISION_HEADER, EngineClient
from console.engine.models import SceneBody
from console.engine_access import engine_client_for
from console.forms.merge import (
    ENTITY_ID_INVALID,
    MERGE_TOO_MANY,
    SURVIVOR_HELP,
    SURVIVOR_NOT_CHOSEN,
    Candidate,
    merge_candidates,
)
from console.rewrites import (
    MERGE_INVALID_TEXT,
    NOT_APPLIED_PREFIX,
    OMNISCIENT_LINE,
    OMNISCIENT_REQUIRED_TEXT,
    SESSION_LIMIT_TEXT,
)
from console.upgrades import BACKFILL_PAUSED_TEXT
from console.views import inspect as views
from console.views.inspect_merge import MERGE_UNREADABLE, SCENE_UNAVAILABLE, entity_merge_url
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.inspect.helpers import FIXTURES, full_v4, lite
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

STORY = "01JM7ST0000000000000000001"
MARA, ALICE, BOB = (f"01JM7CH000000000000000000{n}" for n in (1, 2, 3))
STRANGER = "01JM7EN0000000000000000001"
THING = "01JM7EN0000000000000000004"
TAVERN = "01JM7EN0000000000000000005"
ELSEWHERE = "01JM7EN0000000000000000009"  # in neither the scene nor the cast
SESSION = "01JM7RW0000000000000000012"  # m7/merge_session.json
ANSWER_KEY = "01JM7KY0000000000000000001"  # its idempotency_key
KEY = "01JM7KY0000000000000000009"
URL = f"/stories/{STORY}/inspect/canon/merge/"
SESSION_URL = f"/stories/{STORY}/rewrites/{SESSION}/"
CONFIG_PATH = f"/stories/{STORY}/effective-config"
STORY_PATH = f"/stories/{STORY}"
SCENE_PATH = f"/stories/{STORY}/scene"
MERGE_PATH = f"/stories/{STORY}/entities/merge"
SCENE = "/stories/{story_id}/scene"
MERGE = "/stories/{story_id}/entities/merge"
TICK = re.compile(r'<input type="checkbox" name="entity_ids" value="([^"]+)"[^>]*>')
TICKED = re.compile(r'<input type="checkbox" name="entity_ids" value="([^"]+)"[^>]*checked>')
RADIO_CHECKED = re.compile(r'<input type="radio" name="survivor" value="([^"]+)"[^>]*checked>')
KEY_INPUT = re.compile(r'name="idempotency_key" value="([^"]+)"')


def scene_body(*present: dict[str, str]) -> dict[str, Any]:
    """#58's ``SceneBody`` at the head: the tavern, the stranger and an unnamed object (or
    ``present`` instead)."""
    return {
        "turn": 3,
        "variant": 0,
        "viewpoint": "omniscient",
        "location": {"entity_id": TAVERN, "kind": "place", "name": "The tavern"},
        "present": list(present)
        or [
            {"entity_id": STRANGER, "kind": "person", "name": "hooded stranger"},
            {"entity_id": THING, "kind": "object", "name": ""},
        ],
    }


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def merged(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("merge_session")
    body.update(changes)
    return body


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(views, "engine_client_for", client_with_fake)
    fake.respond("GET", "/stories/{story_id}", 200, json=m7("story"))
    fake.respond("GET", "/stories/{story_id}/effective-config", 200, json=full_v4())
    fake.respond("GET", SCENE, 200, json=scene_body())
    fake.respond("POST", MERGE, 201, json=merged())
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def get(client: Client, query: str = "") -> str:
    response = client.get(f"{URL}{query}")
    assert response.status_code == 200
    return unescape(response.content.decode())


def post(client: Client, **data: Any) -> Any:
    data.setdefault("idempotency_key", KEY)
    data.setdefault("resolution", "synchronous")
    return client.post(URL, data)


def paths(fake: FakeEngine) -> list[str]:
    return [f"{r.method} {r.url.path}" for r in fake.requests]


def merges(fake: FakeEngine) -> list[httpx.Request]:
    return [r for r in fake.requests if r.method == "POST" and r.url.path == MERGE_PATH]


def scenes(fake: FakeEngine) -> list[httpx.Request]:
    return [r for r in fake.requests if r.url.path == SCENE_PATH]


def sent(fake: FakeEngine) -> dict[str, Any]:
    (request,) = merges(fake)
    body: dict[str, Any] = json.loads(request.content)
    return body


def key_in(html: str) -> str:
    keys = KEY_INPUT.findall(html)
    assert keys, "no idempotency key on the page"
    return str(keys[0])


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def labels(html: str) -> list[str]:
    return re.findall(r'<label for="merge-[0-9A-Z]+">([^<]*)</label>', html)


def picker(html: str) -> str:
    return html.split('<fieldset class="merge-entities">')[1].split("</fieldset>")[0]


def other(html: str) -> str:
    return html.split('<fieldset class="merge-other">')[1].split("</fieldset>")[0]


# --- the picker ----------------------------------------------------------------------------------


def test_candidates_order_and_labels(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"?entity={ELSEWHERE}")
    assert TICK.findall(html) == [ELSEWHERE, TAVERN, STRANGER, THING, MARA, ALICE, BOB]
    assert labels(html) == [
        "…00000009 (not in the scene)",
        "The tavern (Place)",
        "hooded stranger (Person)",
        "(unnamed) (Object)",
        "Mara (player) (character)",
        "Alice (character)",
        "Bob (character)",
    ]
    # Each row's id through engine_id: the short id, the full id in ``title``.
    assert f'<code title="{TAVERN}">…00000005</code>' in html
    assert 'class="candidate url"' in html
    assert 'class="candidate scene"' in html
    assert 'class="candidate cast"' in html


def test_an_id_in_the_scene_and_the_cast_appears_once(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        SCENE,
        200,
        json=scene_body(
            {"entity_id": MARA, "kind": "person", "name": "Mara"},
            {"entity_id": STRANGER, "kind": "person", "name": "hooded stranger"},
        ),
    )
    html = get(operator_client)
    assert TICK.findall(html) == [TAVERN, MARA, STRANGER, ALICE, BOB]
    assert labels(html)[1] == "Mara (Person)"


def test_merge_candidates_minor_and_dedupe() -> None:
    scene = SceneBody.from_json(scene_body())
    assert scene is not None
    found = merge_candidates({MARA: "Mara (player)", BOB: "Bob (minor)"}, scene, [BOB, BOB])
    assert found[0] == Candidate(BOB, "Bob (minor) (character)", "url")
    assert [c.id for c in found] == [BOB, TAVERN, STRANGER, THING, MARA]
    # Without the scene a URL id outside the cast reads "(from the link)".
    assert merge_candidates(None, None, [ELSEWHERE]) == [
        Candidate(ELSEWHERE, "…00000009 (from the link)", "url")
    ]


def test_url_entity_preticked(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"?entity=+{STRANGER.lower()}+")
    assert TICKED.findall(html) == [STRANGER]
    assert RADIO_CHECKED.findall(html) == [STRANGER]
    # The URL id comes first, labelled from the scene.
    assert TICK.findall(html)[0] == STRANGER
    assert labels(html)[0] == "hooded stranger (Person)"


def test_nothing_ticked_preselects_no_survivor(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert TICKED.findall(html) == []
    assert RADIO_CHECKED.findall(html) == []
    assert SURVIVOR_HELP in html


def test_bad_url_id_dropped_with_notice(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client, f"?entity=not-an-id-xyz&entity=also-bad&entity={BOB}")
    assert html.count(ENTITY_ID_INVALID) == 1
    assert "not-an-id-xyz" not in html.split("<h2>Merge entities</h2>")[1]  # never echoed
    assert TICKED.findall(html) == [BOB]
    # Complement: it appears in no Engine request.
    assert not any(
        "not-an-id-xyz" in str(r.url) or b"not-an-id-xyz" in r.content for r in engine.requests
    )


def test_scene_read_failure_offers_cast(
    operator_client: Client, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    engine.respond("GET", SCENE, 503, json=error_json("database_unavailable"))
    with caplog.at_level(logging.WARNING, logger="console.engine"):
        html = get(operator_client, f"?entity={ELSEWHERE}")
    assert SCENE_UNAVAILABLE in html
    assert html.index(SCENE_UNAVAILABLE) < html.index('<table class="merge-picker">')
    assert TICK.findall(html) == [ELSEWHERE, MARA, ALICE, BOB]
    assert labels(html)[0] == "…00000009 (from the link)"
    assert any("database_unavailable" in r.getMessage() for r in caplog.records)


def test_no_candidates_at_all(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", SCENE, 503, json=error_json("database_unavailable"))
    engine.respond("GET", "/stories/{story_id}", 503, json=error_json("database_unavailable"))
    html = get(operator_client)
    assert "No entities to offer — type their ids below" in html
    assert TICK.findall(html) == []


def test_get_reads(operator_client: Client, engine: FakeEngine) -> None:
    get(operator_client, f"?entity={BOB}")
    assert paths(engine) == [f"GET {CONFIG_PATH}", f"GET {STORY_PATH}", f"GET {SCENE_PATH}"]
    (scene,) = scenes(engine)
    assert list(scene.url.params.multi_items()) == [("viewpoint", "omniscient")]  # no as_of


def test_the_page_title_and_text(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert "<title>Inspect · Merge entities · " in html
    assert "<h2>Merge entities</h2>" in html
    assert "Reveal that these entities are one referent" in html
    assert '<button type="submit">Plan the merge</button>' in html
    assert (
        f'<a class="active" aria-current="page" href="/stories/{STORY}/inspect/canon/">Canon</a>'
        in html
    )


def test_no_viewpoint_picker(operator_client: Client, engine: FakeEngine) -> None:
    html = get(operator_client)
    assert OMNISCIENT_LINE in html
    assert 'name="viewpoint"' not in html


# --- the body ------------------------------------------------------------------------------------


def test_body_survivor_first(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert response.status_code == 303
    assert sent(engine) == {
        "entity_ids": [BOB, STRANGER],
        "preview": "preview_first",
        "resolution_mode": "synchronous",
    }
    (request,) = merges(engine)
    assert list(request.url.params.multi_items()) == [("viewpoint", "omniscient")]
    assert request.headers["Idempotency-Key"] == KEY
    # Complement: no visibility, no identity proposal, no expected revision.
    body = sent(engine)
    assert "visibility" not in body
    assert "identity_proposal_id" not in body
    assert EXPECTED_REVISION_HEADER not in request.headers


def test_typed_ids_join_the_set(operator_client: Client, engine: FakeEngine) -> None:
    typed = f"\n  {BOB.lower()} \n{STRANGER}\n{ELSEWHERE}\n"
    post(operator_client, entity_ids=[STRANGER], other_ids=typed, survivor=STRANGER)
    assert sent(engine)["entity_ids"] == [STRANGER, BOB, ELSEWHERE]


def test_a_typed_survivor(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, entity_ids=[STRANGER], other_ids=ELSEWHERE, survivor=ELSEWHERE)
    assert sent(engine)["entity_ids"] == [ELSEWHERE, STRANGER]


def test_modes_threshold_and_apply_directly(operator_client: Client, engine: FakeEngine) -> None:
    post(
        operator_client,
        entity_ids=[STRANGER, BOB],
        survivor=STRANGER,
        resolution="threshold_high",
        apply_directly="on",
    )
    body = sent(engine)
    assert (body["preview"], body["resolution_mode"]) == ("apply_directly", {"threshold": "high"})


def local(operator_client: Client, engine: FakeEngine, **data: Any) -> str:
    response = post(operator_client, **data)
    assert response.status_code == 200
    html = unescape(response.content.decode())
    assert merges(engine) == []
    assert key_in(html) == KEY
    return html


def test_too_few_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = local(operator_client, engine, entity_ids=[STRANGER], survivor=STRANGER)
    assert MERGE_INVALID_TEXT["too_few"] in picker(html)
    assert TICKED.findall(html) == [STRANGER]


def test_too_many_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    typed = "\n".join(f"01JM7EN00000000000000000{n:02d}" for n in range(10, 20))
    html = local(operator_client, engine, entity_ids=[STRANGER], other_ids=typed, survivor=STRANGER)
    assert MERGE_TOO_MANY in picker(html)
    assert typed in other(html)


def test_bad_typed_line_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = local(
        operator_client,
        engine,
        entity_ids=[STRANGER, BOB],
        other_ids=f"{ALICE}\nnope\n\nbad too",
        survivor=BOB,
    )
    errors = other(html)
    assert f"Line 2: {ENTITY_ID_INVALID}" in errors
    assert f"Line 4: {ENTITY_ID_INVALID}" in errors
    assert "Line 1" not in errors
    # A typed id the cast already offers adds no "(typed)" radio of its own.
    assert f'<span title="{ALICE}">' not in html
    assert RADIO_CHECKED.findall(html) == [BOB]


def test_survivor_not_chosen_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = local(operator_client, engine, entity_ids=[STRANGER, BOB], survivor=ALICE)
    assert SURVIVOR_NOT_CHOSEN in picker(html)


def test_no_survivor_then_typed_radios(operator_client: Client, engine: FakeEngine) -> None:
    html = local(operator_client, engine, entity_ids=[STRANGER], other_ids=ELSEWHERE)
    assert SURVIVOR_NOT_CHOSEN in picker(html)
    assert f'<span title="{ELSEWHERE}">…00000009 (typed)</span>' in html
    assert f'name="survivor" value="{ELSEWHERE}"' in html


def test_a_bad_posted_tick_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    html = local(
        operator_client, engine, entity_ids=[STRANGER, BOB, "not-an-id"], survivor=STRANGER
    )
    assert ENTITY_ID_INVALID in picker(html)
    assert MERGE_INVALID_TEXT["too_few"] not in html  # the bounds count valid ids only


def test_post_reads_no_scene_before_write(operator_client: Client, engine: FakeEngine) -> None:
    post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert paths(engine) == [f"GET {CONFIG_PATH}", f"GET {STORY_PATH}", f"POST {MERGE_PATH}"]


# --- outcomes ------------------------------------------------------------------------------------


def session_code() -> str:
    return f'<code title="{SESSION}">…00000012</code>'


def test_success_redirects_to_session(operator_client: Client, engine: FakeEngine) -> None:
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert response.status_code == 303
    assert response["Location"] == SESSION_URL
    assert flashes(response) == [
        f"Merge planned as rewrite session {session_code()}"
        f" — idempotency key <code>{ANSWER_KEY}</code>"
    ]


def question(qid: str) -> dict[str, Any]:
    body: dict[str, Any] = m7("rewrite_open")["questions"][0]
    body["id"] = qid
    return body


@pytest.mark.parametrize(
    ("changes", "apply_directly", "text"),
    [
        pytest.param(
            {"state": "awaiting_answers", "questions": [question("q1"), question("q2")]},
            False,
            "Merge planned as rewrite session {code} — 2 questions to answer",
            id="questions",
        ),
        pytest.param(
            {
                "state": "applied",
                "apply": {
                    "amendment_id": "01JM7AM0000000000000000003",
                    "report": {"applied": []},
                    "timeline_revision": 21,
                },
            },
            True,
            "Merge applied — rewrite session {code}",
            id="applied",
        ),
        pytest.param(
            {},
            True,
            NOT_APPLIED_PREFIX + "Merge planned as rewrite session {code}",
            id="not-applied",
        ),
        pytest.param(
            {"state": "planning"},
            False,
            "Merge opened — the session is now Planning",
            id="other-state",
        ),
    ],
)
def test_the_flash_follows_the_session_state(
    operator_client: Client,
    engine: FakeEngine,
    changes: dict[str, Any],
    apply_directly: bool,
    text: str,
) -> None:
    from tests.rewrites.helpers import answer_raw

    answer_raw(engine, "POST", MERGE, merged(**changes), 201)
    extra = {"apply_directly": "on"} if apply_directly else {}
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB, **extra)
    assert response.status_code == 303
    (flash,) = flashes(response)
    assert flash == text.format(code=session_code()) + (
        f" — idempotency key <code>{ANSWER_KEY}</code>"
    )


def test_unreadable_201_redirects_to_rewrites(operator_client: Client, engine: FakeEngine) -> None:
    from tests.rewrites.helpers import answer_raw

    answer_raw(engine, "POST", MERGE, {"not": "a session"}, 201)
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert response.status_code == 303
    assert response["Location"] == f"/stories/{STORY}/rewrites/"
    assert [unescape(f) for f in flashes(response)] == [
        f"{MERGE_UNREADABLE} — idempotency key <code>{KEY}</code>"
    ]


# --- refusals ------------------------------------------------------------------------------------

REFUSALS = [
    *(
        pytest.param(
            422, error_json("merge_invalid", reason=reason), text, True, id=f"merge-{reason}"
        )
        for reason, text in MERGE_INVALID_TEXT.items()
    ),
    pytest.param(
        409,
        error_json("rewrite_session_limit", limit=3),
        SESSION_LIMIT_TEXT.format(limit="3") + f' <a href="/stories/{STORY}/rewrites/">Rewrite</a>',
        False,
        id="limit",
    ),
    pytest.param(
        409, error_json("story_paused", cause="backfill"), BACKFILL_PAUSED_TEXT, False, id="paused"
    ),
    pytest.param(
        409,
        error_json("module_off", module="lite_rewrites"),
        "History rewrites need a planner for this story's pipeline (<code>lite_rewrites</code> "
        f'is off) <a href="/stories/{STORY}/config/">Config</a>',
        False,
        id="module-off",
    ),
    pytest.param(423, error_json("story_busy"), "Story busy", False, id="busy"),
    pytest.param(
        409,
        error_json("story_archived"),
        "This story is archived — unarchive it to merge entities.",
        False,
        id="archived",
    ),
    pytest.param(
        403, error_json("omniscient_required"), OMNISCIENT_REQUIRED_TEXT, False, id="omniscient"
    ),
]


@pytest.mark.parametrize(("status", "body", "text", "on_picker"), REFUSALS)
def test_refusals_keep_the_inputs_with_a_fresh_key(
    operator_client: Client,
    engine: FakeEngine,
    caplog: pytest.LogCaptureFixture,
    status: int,
    body: dict[str, Any],
    text: str,
    on_picker: bool,
) -> None:
    engine.respond("POST", MERGE, status, json=body)
    with caplog.at_level(logging.WARNING, logger="console"):
        response = post(
            operator_client,
            entity_ids=[STRANGER, BOB],
            other_ids=ELSEWHERE,
            survivor=BOB,
            resolution="best_effort",
            apply_directly="on",
        )
    assert response.status_code == 200
    html = unescape(response.content.decode())
    if on_picker:
        assert text in picker(html)
        assert "rewrite-refusal" not in html
    else:
        refusal = html.split('<p class="errorlist rewrite-refusal" role="alert">')[1]
        refusal = refusal.split("</p>")[0]
        assert refusal.startswith(text) if "<a" in text else text in refusal
    # The inputs kept: ticks, typed ids, survivor and modes.
    assert set(TICKED.findall(html)) == {STRANGER, BOB}
    assert ELSEWHERE in other(html)
    assert RADIO_CHECKED.findall(html) == [BOB]
    assert '<option value="best_effort" selected>' in html
    assert 'name="apply_directly" id="id_apply_directly" checked' in html
    assert key_in(html) != KEY
    assert len(merges(engine)) == 1
    # After the refusal the page re-reads the scene once.
    assert paths(engine) == [
        f"GET {CONFIG_PATH}",
        f"GET {STORY_PATH}",
        f"POST {MERGE_PATH}",
        f"GET {SCENE_PATH}",
    ]
    if body["error"]["code"] == "omniscient_required":
        assert any("omniscient_required" in r.getMessage() for r in caplog.records)


def test_transport_error_keeps_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("POST", MERGE, httpx.ReadTimeout("slow"))
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'class="engine-error' in html
    assert key_in(html) == KEY
    assert set(TICKED.findall(html)) == {STRANGER, BOB}


def test_unreadable_refusal_keeps_posted_key(operator_client: Client, engine: FakeEngine) -> None:
    from tests.rewrites.helpers import answer_raw

    answer_raw(engine, "POST", MERGE, {"not": "an envelope"}, 503)
    html = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB).content.decode()
    assert 'class="engine-error' in html
    assert key_in(html) == KEY


def test_idempotency_conflict_is_the_panel_with_a_fresh_key(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", MERGE, 409, json=error_json("idempotency_conflict", "reused key"))
    html = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB).content.decode()
    assert 'class="engine-error' in html
    assert "rewrite-refusal" not in html
    assert key_in(html) != KEY


def test_importing_on_the_write(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "POST",
        MERGE,
        409,
        json=error_json("story_importing", stage="media", job_id="01JM7JB0000000000000000007"),
    )
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert "This story is being imported" in unescape(response.content.decode())


# --- the gate and the links ----------------------------------------------------------------------


def test_greyed_canon_tab_zero_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/stories/{story_id}/effective-config", 200, json=lite())
    get(operator_client, f"?entity={BOB}")
    response = post(operator_client, entity_ids=[STRANGER, BOB], survivor=BOB)
    assert response.status_code == 200
    assert scenes(engine) == []
    assert merges(engine) == []
    assert 'class="entity-merge-form"' not in response.content.decode()


def test_canon_tab_links_merge(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/admin/stories/{story_id}/facts",
        200,
        json=json.loads((FIXTURES / "facts_list.json").read_text()),
    )
    engine.respond(
        "GET",
        "/stories/{story_id}/predicates",
        200,
        json=json.loads((FIXTURES / "predicates_list.json").read_text()),
    )
    response = operator_client.get(f"/stories/{STORY}/inspect/canon/")
    html = response.content.decode()
    actions = html.split('<p class="canon-actions">')[1].split("</p>")[0]
    assert actions.index("New fact") < actions.index(
        f'<a class="button merge-entities" href="{URL}">Merge entities…</a>'
    )


def test_entity_page_links_merge_with_entity(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        "/stories/{story_id}/entities/{entity_id}/profile",
        200,
        json=json.loads((FIXTURES / "entity_profile_omniscient.json").read_text()),
    )
    html = operator_client.get(f"/stories/{STORY}/inspect/entities/{STRANGER}/").content.decode()
    assert (
        '<p class="entity-action merge-entity"><a class="merge-entity" '
        f'href="{URL}?entity={STRANGER}">Merge with another entity…</a></p>'
    ) in html
    assert entity_merge_url(STORY, STRANGER) == f"{URL}?entity={STRANGER}"


def test_entity_page_without_a_profile_has_no_merge_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond(
        "GET",
        "/stories/{story_id}/entities/{entity_id}/profile",
        404,
        json=error_json("not_found"),
    )
    html = operator_client.get(f"/stories/{STORY}/inspect/entities/{STRANGER}/").content.decode()
    assert "merge-entity" not in html
