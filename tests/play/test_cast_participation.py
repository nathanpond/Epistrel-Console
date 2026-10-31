"""#61 (C8): the cast panel's participation roles and weights, their role form and weight button,
and the add-character form's participation role."""

import json
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils.http import url_has_allowed_host_and_scheme

from console.cast_panel import Shown
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.timeline import reload_message
from console.views import play, story_characters
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import EFFECTIVE, answer_effective_config
from tests.inspect.helpers import structured_v2
from tests.play.helpers import MARA, STORY, TOBIN, answer_page, error_json, story_json

JENNI = "01JB2Z8Y5Q3W6K9M1N4P7R0T3C"
STORY_PATH = "/stories/{story_id}"
PARTICIPATION = "/stories/{story_id}/characters/{character_id}/participation"
WEIGHT = "/stories/{story_id}/characters/{character_id}/weight"
ADD = "/stories/{story_id}/characters"
PLAY_URL = f"/stories/{STORY}/"
ROLE_URL = f"/stories/{STORY}/characters/{TOBIN}/participation/"
WEIGHT_URL = f"/stories/{STORY}/characters/{TOBIN}/weight/"
NEW_URL = f"/stories/{STORY}/characters/new/"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KY7"
OMNISCIENT = {"viewpoint": "omniscient"}
MODELED_AT = {"turn": 3, "variant": 0, "slot_k": 0, "beat": 32767, "step": 0}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine behind the play view and the story-character views."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (play, story_characters):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


# --- the story as the fake serves it ----------------------------------------------------------


def _participation(role: str = "reactive", fixed: bool = False) -> dict[str, Any]:
    return {"role": role, "fixed": fixed}


def play_story(**changes: Any) -> dict[str, Any]:
    """The play read: Mara (the player), Tobin and Jenni, both reactive."""
    story = story_json(**changes)
    story["characters"] = [
        {"id": MARA, "name": "Mara", "is_player": True, "participation": None},
        {"id": TOBIN, "name": "Tobin", "is_player": False, "participation": _participation()},
        {"id": JENNI, "name": "Jenni", "is_player": False, "participation": _participation()},
    ]
    return story


def omniscient_story(**rows: dict[str, Any]) -> dict[str, Any]:
    """The omniscient read (#186's shapes): ``rows`` maps a name to changes on its row."""
    story = play_story()
    for character in story["characters"]:
        character.update(
            true_age=30,
            promoted_to=[],
            role_id=None,
            source=None,
            weight="major",
            modeled_at=None,
        )
        character.update(rows.get(character["name"], {}))
    return story


def serve_page(
    engine: FakeEngine,
    omniscient: dict[str, Any] | None = None,
    *,
    story: dict[str, Any] | None = None,
    renders: str | None = "per_character",
) -> None:
    answer_page(engine, story=story or play_story())
    engine.respond("GET", STORY_PATH, 200, json=omniscient or omniscient_story(), query=OMNISCIENT)
    if renders is not None:
        engine.respond("GET", EFFECTIVE, 200, json=structured_v2(multi_character__renders=renders))


def page(client: Client) -> str:
    response = client.get(PLAY_URL)
    assert response.status_code == 200
    return response.content.decode()


def row(html: str, character_id: str) -> str:
    match = re.search(rf'<li id="cast-{character_id}">(.*?)</li>', html, flags=re.S)
    assert match, character_id
    return match.group(1)


def flashes(response: Any) -> list[tuple[str, str]]:
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def writes(engine: FakeEngine, method: str) -> list[Any]:
    return [r for r in engine.requests if r.method == method]


def shown(character_id: str = TOBIN, role: str | None = "reactive", fixed: bool = False) -> str:
    names = {TOBIN: "Tobin", JENNI: "Jenni"}
    return Shown(STORY, character_id, names.get(character_id, "?"), role, fixed).signed()


def role_post(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"idempotency_key": KEY, "shown": shown(), "role": "primary"}
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def participation_set(role: str = "primary", fixed: bool = True) -> dict[str, Any]:
    return {
        "timeline_revision": 1,
        "config_revision": 4,
        "character": {
            "id": TOBIN,
            "name": "Tobin",
            "is_player": False,
            "role_id": None,
            "source": None,
            "participation": _participation(role, fixed),
        },
        "idempotency_key": KEY,
    }


def character_weighted(weight: str = "minor") -> dict[str, Any]:
    character = omniscient_story()["characters"][1]
    character.update(weight=weight)
    return {"timeline_revision": 2, "character": character, "idempotency_key": KEY}


# --- the rows ----------------------------------------------------------------------------------


def test_cast_rows_show_role_and_weight_from_the_omniscient_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(
        engine,
        omniscient_story(
            Tobin={"participation": _participation("primary", True)},
            Jenni={"weight": "minor", "modeled_at": MODELED_AT},
        ),
    )
    html = page(operator_client)
    tobin, jenni = row(html, TOBIN), row(html, JENNI)
    assert tobin.startswith(
        'Tobin · <span class="cast-role">Primary (fixed)</span> · <span class="cast-weight" '
        'title="Major: full partition and hot-memory budget">Major</span>\n'
    )
    assert jenni.startswith(
        'Jenni · <span class="cast-role">Reactive</span> · <span class="cast-weight" title="Minor: '
        'sparse partition, scaled hot-memory budget, rendered only when named">Minor</span> · '
        f'minor since <a class="minor-since" href="/stories/{STORY}/history/?before_turn=4'
        '#turn-3-0">turn 3 · after the turn</a>'
    )
    # The role form, then the weight button, before #72's Promote and Erase.
    assert tobin.index("Set role") < tobin.index("Make minor") < tobin.index("Promote to catalog")
    assert tobin.index("Promote to catalog") < tobin.index("Erase…")
    assert 'title="Minor: sparse partition' in tobin  # Make minor's title is the target's
    assert ">Make major</button>" in jenni
    assert '<option value="primary" selected>Primary</option>' in tobin
    assert 'id="cast-' + TOBIN + '-fixed" checked' in tobin
    assert '<option value="reactive" selected>Reactive</option>' in jenni
    assert 'title="The engine never escalates or lowers a fixed role"' in tobin


def test_a_major_with_modeled_at_and_a_missing_weight(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = omniscient_story(Tobin={"modeled_at": MODELED_AT})
    del body["characters"][2]["weight"]  # a missing weight is the Engine's default, Major
    serve_page(engine, body)
    html = page(operator_client)
    assert "minor since" not in row(html, TOBIN)
    assert ">Major</span>" in row(html, JENNI)
    assert ">Make minor</button>" in row(html, JENNI)


def test_failed_omniscient_read_shows_roles_and_no_weight_buttons(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    story = play_story()
    story["characters"][1]["participation"] = _participation("silent", True)
    answer_page(engine, story=story)
    engine.respond(
        "GET", STORY_PATH, 503, json=error_json("database_unavailable"), query=OMNISCIENT
    )
    html = page(operator_client)
    tobin = row(html, TOBIN)
    assert '<span class="cast-role">Silent (fixed)</span>' in tobin
    assert '<span class="cast-weight">—</span>' in tobin
    assert '<option value="silent" selected>Silent</option>' in tobin
    assert (
        '<p class="muted promotion-links-note">Weights and promotion links unavailable: '
        "503 database_unavailable</p>"
    ) in html
    assert "Promotion links unavailable" not in html
    # Complement: no weight form renders; the role form's shown is signed from the play read.
    assert "/weight/" not in html
    assert 'class="cast-weight-form' not in html
    signed = re.search(r'name="shown" value="([^"]+)"', tobin)
    assert signed
    found = Shown.unsigned(signed.group(1), STORY, TOBIN)
    assert found == Shown(STORY, TOBIN, "Tobin", "silent", True)


def test_a_row_without_a_role_reads_a_dash_and_preselects_reactive(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine, omniscient_story(Tobin={"participation": None}))
    tobin = row(page(operator_client), TOBIN)
    assert '<span class="cast-role">—</span>' in tobin
    assert '<option value="reactive" selected>Reactive</option>' in tobin


def test_player_row_has_no_role_or_weight_controls(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    mara = row(page(operator_client), MARA)
    assert mara.startswith("Mara (player) ")
    # Complement: none of the controls a non-player row has.
    for absent in ("cast-role", "cast-weight", "<form", "Set role", "Make ", "/participation/"):
        assert absent not in mara


def test_row_ids_are_unique_across_rows(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine)
    html = page(operator_client)
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    assert f'id="cast-{TOBIN}-role"' in html
    assert f'id="cast-{JENNI}-role"' in html
    keys = re.findall(r'name="idempotency_key" value="([^"]+)"', html)
    assert len(keys) == len(set(keys))


# --- the role form -----------------------------------------------------------------------------


def test_set_role_patches_participation_and_flashes_config_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PATCH", PARTICIPATION, 200, json=participation_set())
    response = operator_client.post(ROLE_URL, role_post(fixed="on"))
    assert response.status_code == 302
    assert response["Location"] == f"{PLAY_URL}#cast-{TOBIN}"
    (patch,) = writes(engine, "PATCH")
    assert patch.url.path == f"/stories/{STORY}/characters/{TOBIN}/participation"
    assert patch.read() == b'{"role":"primary","fixed":true}'
    assert patch.headers["Idempotency-Key"] == KEY
    assert flashes(response) == [
        ("success", "Set Tobin's role to Primary (fixed) — config revision 4")
    ]
    (logged,) = EngineOperation.objects.filter(kind="set_participation")
    assert (logged.entity_type, logged.entity_id, logged.idempotency_key) == ("story", STORY, KEY)


def test_same_role_makes_no_engine_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(ROLE_URL, role_post(role="reactive"))
    assert response["Location"] == f"{PLAY_URL}#cast-{TOBIN}"
    assert flashes(response) == [("info", "Tobin already has that role")]
    assert writes(engine, "PATCH") == []  # complement: zero PATCH calls


@pytest.mark.parametrize(
    "signed",
    [
        "not-a-signature",
        "",
        None,
        Shown(STORY, TOBIN, "Tobin", None, False).signed(),  # a shown "—" always calls
        Shown(STORY, TOBIN, "Tobin", "background", False).signed(),  # so does an unknown role
    ],
    # Fixed ids: a signed value is time-stamped, so xdist workers would collect different ids.
    ids=["garbage", "empty", "absent", "shown-dash", "shown-unknown-role"],
)
def test_bad_signature_calls_the_engine(
    operator_client: Client, engine: FakeEngine, signed: str | None
) -> None:
    engine.respond("PATCH", PARTICIPATION, 200, json=participation_set("reactive", False))
    data = role_post(role="reactive", shown=signed)
    response = operator_client.post(ROLE_URL, data)
    assert len(writes(engine, "PATCH")) == 1
    assert flashes(response) == [("success", "Set Tobin's role to Reactive — config revision 4")]


def test_shown_for_another_character_calls_the_engine(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PATCH", PARTICIPATION, 404, json=error_json("not_found"))
    other_story = Shown("01JB2Z8Y5Q3W6K9M1N4P7R0T9Z", TOBIN, "Tobin", "reactive", False)
    for signed in (shown(JENNI), other_story.signed()):
        response = operator_client.post(ROLE_URL, role_post(role="reactive", shown=signed))
        # Without a usable shown the flash would name the short id; 404 has its own words.
        assert flashes(response)[-1] == ("error", "That character isn't in this story's cast")
    assert len(writes(engine, "PATCH")) == 2


def test_without_a_usable_shown_flashes_name_the_short_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("PATCH", PARTICIPATION, 503, json=error_json("database_unavailable"))
    response = operator_client.post(ROLE_URL, role_post(shown="forged"))
    assert flashes(response) == [
        ("error", f"Couldn't change …{TOBIN[-8:]}: 503 database_unavailable")
    ]


@pytest.mark.parametrize("role", [None, "", "background", "PRIMARY"])
def test_invalid_role_post_flashes_and_makes_no_call(
    operator_client: Client, engine: FakeEngine, role: str | None
) -> None:
    response = operator_client.post(ROLE_URL, role_post(role=role, fixed="on"))
    assert response["Location"] == f"{PLAY_URL}#cast-{TOBIN}"
    assert flashes(response) == [("error", "Choose a role.")]
    assert writes(engine, "PATCH") == []


# --- the weight button -------------------------------------------------------------------------


def test_make_minor_posts_weight_and_flashes_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine)
    tobin = row(page(operator_client), TOBIN)
    form = re.search(
        r'<form method="post" action="([^"]+)" class="cast-weight-form.*?</form>', tobin
    )
    assert form
    assert form.group(1) == WEIGHT_URL
    key = re.search(r'name="idempotency_key" value="([^"]+)"', form.group(0))
    signed = re.search(r'name="shown" value="([^"]+)"', form.group(0))
    assert key
    assert signed
    assert 'name="weight" value="minor"' in form.group(0)
    engine.respond("POST", WEIGHT, 200, json=character_weighted("minor"))
    data = {"idempotency_key": key.group(1), "shown": signed.group(1), "weight": "minor"}
    response = operator_client.post(WEIGHT_URL, data)
    assert response["Location"] == f"{PLAY_URL}#cast-{TOBIN}"
    (post,) = writes(engine, "POST")
    assert post.read() == b'{"weight":"minor"}'
    assert post.headers["Idempotency-Key"] == key.group(1)
    assert flashes(response) == [("success", "Tobin is now Minor — timeline revision 2")]
    (logged,) = EngineOperation.objects.filter(kind="set_weight")
    assert (logged.entity_type, logged.entity_id) == ("story", STORY)


def test_the_weight_write_always_calls(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", WEIGHT, 200, json=character_weighted("major"))
    data = {"idempotency_key": KEY, "shown": shown(), "weight": "major"}
    response = operator_client.post(WEIGHT_URL, data)
    assert len(writes(engine, "POST")) == 1
    assert flashes(response) == [("success", "Tobin is now Major — timeline revision 2")]


def test_an_invalid_weight_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.post(WEIGHT_URL, {"idempotency_key": KEY, "weight": "huge"})
    assert flashes(response) == [("error", "Choose a weight.")]
    assert writes(engine, "POST") == []


@pytest.mark.parametrize("path", ["weight", "participation"])
def test_hostile_ids_never_redirect_off_site(
    operator_client: Client, engine: FakeEngine, path: str
) -> None:
    """The return address is built from the URL's ids, so an off-site-looking id stays local."""
    url = f"/stories/%5C%5Cevil.example/characters/@evil.example/{path}/"
    response = operator_client.post(url, {"idempotency_key": KEY, "weight": "huge"})
    location = response["Location"]
    assert location.startswith("/stories/"), location
    assert url_has_allowed_host_and_scheme(location, allowed_hosts={"testserver"})


def test_an_off_site_return_address_falls_back_to_the_story_list(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(story_characters, "cast_row_url", lambda *_: "https://evil.example/")
    response = operator_client.post(WEIGHT_URL, {"idempotency_key": KEY, "weight": "huge"})
    assert response["Location"] == "/stories/"


# --- gating and refusals -----------------------------------------------------------------------


def test_archived_story_disables_both_controls(operator_client: Client, engine: FakeEngine) -> None:
    story = play_story(status="archived", archived_at="2026-10-24T22:00:00Z")
    serve_page(engine, story=story)
    tobin = row(page(operator_client), TOBIN)
    title = 'title="Archived — unarchive to change roles and weights"'
    assert re.search(r"<select[^>]*disabled", tobin)
    assert re.search(r'<input type="checkbox"[^>]*disabled', tobin)
    assert f'class="set-role" disabled {title}>Set role</button>' in tobin
    assert f'class="set-weight" {title} disabled>Make minor</button>' in tobin


def test_a_paused_story_keeps_the_controls_enabled(
    operator_client: Client, engine: FakeEngine
) -> None:
    serve_page(engine, story=play_story(status="paused"))
    tobin = row(page(operator_client), TOBIN)
    assert "disabled" not in tobin


BUSY = error_json("story_busy", "a turn is in flight", reason="in_flight")


@pytest.mark.parametrize(
    ("status", "body", "flash"),
    [
        (423, BUSY, "Story busy — another turn is running"),
        (
            409,
            error_json("story_archived"),
            "This story is archived — unarchive it to change roles and weights.",
        ),
        (409, error_json("story_paused"), "The story is paused for a check"),
        (
            409,
            error_json("revision_conflict", current_revision=9),
            None,  # the shared reload wording
        ),
        (422, error_json("validation_error", field="character_id", reason="is_player"), None),
        (404, error_json("not_found"), "That character isn't in this story's cast"),
        (
            503,
            error_json("database_unavailable"),
            "Couldn't change Tobin: 503 database_unavailable",
        ),
    ],
)
@pytest.mark.parametrize("write", ["role", "weight"])
def test_refusals_flash_c5_wording(
    operator_client: Client,
    engine: FakeEngine,
    caplog: pytest.LogCaptureFixture,
    write: str,
    status: int,
    body: dict[str, Any],
    flash: str | None,
) -> None:
    code = body["error"]["code"]
    if code == "revision_conflict":
        flash = reload_message(9)
    if code == "validation_error":
        flash = "The player's character has no role."
    if write == "role":
        engine.respond("PATCH", PARTICIPATION, status, json=body)
        response = operator_client.post(ROLE_URL, role_post())
    else:
        engine.respond("POST", WEIGHT, status, json=body)
        data = {"idempotency_key": KEY, "shown": shown(), "weight": "minor"}
        response = operator_client.post(WEIGHT_URL, data)
    assert response.status_code == 302
    assert response["Location"] == f"{PLAY_URL}#cast-{TOBIN}"
    assert flashes(response) == [("error", flash)]
    assert f"engine error {status} {code}" in caplog.text


def test_an_unreachable_engine_flashes_couldnt_change(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("PATCH", PARTICIPATION, httpx.ConnectError("refused"))
    response = operator_client.post(ROLE_URL, role_post())
    ((level, text),) = flashes(response)
    assert level == "error"
    assert text.startswith("Couldn't change Tobin: unreachable (")


def test_inert_note_under_combined_renders(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine, renders="combined")
    html = page(operator_client)
    assert (
        '<p class="muted roles-note">Roles take effect only when the story\'s profile renders '
        "characters one by one (this story's renders <code>combined</code>)</p>"
    ) in html
    # The role form isn't gated by it.
    assert "disabled" not in row(html, TOBIN)


def test_note_when_config_unread(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine, renders=None)
    engine.respond("GET", EFFECTIVE, 503, json=error_json("database_unavailable"))
    html = page(operator_client)
    assert (
        "Roles take effect only when the story's profile renders characters one by one "
        "(couldn't read this story's configuration)"
    ) in html


def test_no_note_under_per_character(operator_client: Client, engine: FakeEngine) -> None:
    serve_page(engine, renders="per_character")
    assert "roles-note" not in page(operator_client)  # complement


def test_no_note_without_a_non_player(operator_client: Client, engine: FakeEngine) -> None:
    story = play_story()
    story["characters"] = story["characters"][:1]
    omniscient = omniscient_story()
    omniscient["characters"] = omniscient["characters"][:1]
    serve_page(engine, omniscient, story=story, renders="combined")
    assert "roles-note" not in page(operator_client)


@pytest.mark.parametrize("url", [ROLE_URL, WEIGHT_URL])
def test_get_on_write_urls_is_405(operator_client: Client, engine: FakeEngine, url: str) -> None:
    assert operator_client.get(url).status_code == 405
    assert engine.requests == []


# --- add character -----------------------------------------------------------------------------


def add_form(client: Client, engine: FakeEngine) -> str:
    engine.respond("GET", STORY_PATH, 200, json=omniscient_story())
    response = client.get(NEW_URL)
    assert response.status_code == 200
    return response.content.decode()


def add_data(html: str, **changes: Any) -> dict[str, Any]:
    snapshot = re.search(r'name="snapshot" value="([^"]*)"', html)
    assert snapshot
    data: dict[str, Any] = {
        "snapshot": snapshot.group(1),
        "idempotency_key": KEY,
        "expected_revision": "1",
        "name": "Dale",
        "definition": "A delivery driver in a hurry.",
        "true_age": "52",
        "safe_fallback_text": "",
        "deflection_style": "",
        "participation_role": "",
        "images-TOTAL_FORMS": "1",
        "images-INITIAL_FORMS": "0",
        "images-MIN_NUM_FORMS": "0",
        "images-MAX_NUM_FORMS": "21",
        "images-0-media_id": "",
        "images-0-ORDER": "1",
    }
    data.update(changes)
    return data


def added() -> dict[str, Any]:
    return {
        "id": "01JB2Z8Y5Q3W6K9M1N4P7R0T3D",
        "name": "Dale",
        "ordinal": 3,
        "timeline_revision": 2,
        "idempotency_key": KEY,
    }


def test_the_add_form_offers_the_participation_role(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = add_form(operator_client, engine)
    assert '<option value="" selected>Engine default (reactive)</option>' in html
    assert html.index('id="f-deflection_style"') < html.index('id="f-participation_role"')
    assert html.index('id="f-participation_role"') < html.index('id="f-participation_fixed"')
    assert "The engine never escalates or lowers a fixed role" in html


@pytest.mark.parametrize(
    ("changes", "sent", "suffix"),
    [
        ({}, None, ""),
        ({"participation_role": "silent"}, {"role": "silent", "fixed": False}, " — role Silent"),
        (
            {"participation_role": "primary", "participation_fixed": "on"},
            {"role": "primary", "fixed": True},
            " — role Primary (fixed)",
        ),
    ],
)
def test_add_character_sends_participation_only_when_chosen(
    operator_client: Client,
    engine: FakeEngine,
    changes: dict[str, str],
    sent: dict[str, Any] | None,
    suffix: str,
) -> None:
    html = add_form(operator_client, engine)
    engine.respond("POST", ADD, 201, json=added())
    response = operator_client.post(NEW_URL, add_data(html, **changes))
    assert response.status_code == 302
    (post,) = writes(engine, "POST")
    body = json.loads(post.read())
    assert body.get("participation") == sent
    assert ("participation" in body) is (sent is not None)
    assert flashes(response) == [
        ("success", f"Added Dale (cast #3) — timeline revision 2 — key {KEY}{suffix}")
    ]


def test_fixed_without_role_is_a_local_error(operator_client: Client, engine: FakeEngine) -> None:
    html = add_form(operator_client, engine)
    response = operator_client.post(NEW_URL, add_data(html, participation_fixed="on"))
    assert response.status_code == 200
    page_html = response.content.decode()
    row_html = re.search(r'id="f-participation_role-row">(.*?)</p>', page_html, flags=re.S)
    assert row_html
    assert "Choose a role to fix it." in row_html.group(1)
    assert writes(engine, "POST") == []


@pytest.mark.parametrize("path", ["participation", "participation.role", "participation.fixed"])
def test_a_participation_422_lands_on_the_role(
    operator_client: Client, engine: FakeEngine, path: str
) -> None:
    html = add_form(operator_client, engine)
    refused = error_json(
        "validation_error",
        "bad participation",
        errors=[{"field": f"body.{path}", "message": "Not a role"}],
    )
    engine.respond("POST", ADD, 422, json=refused)
    response = operator_client.post(NEW_URL, add_data(html, participation_role="primary"))
    page_html = response.content.decode()
    row_html = re.search(r'id="f-participation_role-row">(.*?)</p>', page_html, flags=re.S)
    assert row_html
    assert "Not a role" in row_html.group(1)
