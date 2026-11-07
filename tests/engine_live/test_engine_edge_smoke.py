"""The Console against the live Engine (``ENGINE_LIVE_URL``, ``ENGINE_LIVE_SERVICE_KEY``), through
its real client and pages.

Run by ci.yml's engine-smoke job against the Engine ``edge`` image; locally, see the README.
"""

import re
import time
from typing import Any
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from console.engine.client import ENGINE_PROFILES_PAGE, EngineClient, EngineError
from console.engine.models import CertificationRunRequested, EngineProfile
from console.engine_api_check import check_engine_api
from console.engine_probe import probe
from console.ids import new_ulid
from console.models import EngineOperation
from tests.plugins.engine_live import live_key, live_url

pytestmark = pytest.mark.engine_live

# Engine M7's seeded presets (#293, absorbing #282): ``Structured`` became Full in place (same id,
# version 4; versions 1 to 3 stay pinnable), beside Lite, Standard and Max.
LITE_PROFILE_ID = "00000000000000000000PRF001"
FULL_PROFILE_ID = "00000000000000000000PRF002"
STANDARD_PROFILE_ID = "00000000000000000000PRF003"
MAX_PROFILE_ID = "00000000000000000000PRF004"
PRESET_NAMES = {
    LITE_PROFILE_ID: "Lite",
    STANDARD_PROFILE_ID: "Standard",
    FULL_PROFILE_ID: "Full",
    MAX_PROFILE_ID: "Max",
}


@pytest.fixture
def url() -> str:
    return live_url()


@pytest.fixture
def key() -> str:
    return live_key()


def test_client_health(url: str, key: str) -> None:
    result = EngineClient(url, key).health()
    assert result.http_status == 200
    assert result.body is not None, f"/health did not parse as HealthResponse: {result.raw}"
    assert result.body.status == "ok"
    assert result.body.checks.database == "ok"


def test_probe_says_healthy(url: str, key: str) -> None:
    result = probe(EngineClient(url, key))
    assert result.state == "healthy", result.reason


def test_status_page_and_health(url: str, key: str, operator_client: Client) -> None:
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        page = operator_client.get("/").content.decode()
        health = operator_client.get("/health")
    assert "Healthy" in page
    assert "Engine: Healthy" in page  # the shell's badge
    assert "Unhealthy" not in page
    assert "Unreachable" not in page
    assert health.status_code == 200
    engine = health.json()["engine"]
    assert engine["state"] == "healthy"
    assert engine["checks"]["database"] == "ok"


@pytest.mark.engine_auth
def test_status_page_service_key_accepted(url: str, key: str, operator_client: Client) -> None:
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        page = operator_client.get("/").content.decode()
    assert "Service key: accepted" in page


@pytest.mark.engine_auth
def test_list_stories_live(url: str, key: str) -> None:
    stories = EngineClient(url, key).list_stories()
    assert isinstance(stories, list)


@pytest.mark.engine_auth
def test_wrong_key_is_rejected_on_the_status_page(url: str, operator_client: Client) -> None:
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY="wrong-key-" + "0" * 32):
        page = operator_client.get("/").content.decode()
    assert "Service key: rejected (401)" in page


@pytest.mark.xfail(
    strict=False,
    reason="Engine edge API drift is reported by the daily drift check (#40), not the PR gate",
)
def test_live_engine_api_matches_the_recording(url: str, key: str) -> None:
    assert check_engine_api(EngineClient(url, key)).verdict == "matches"


@pytest.mark.engine_auth
def test_keyed_call_is_accepted(url: str, key: str) -> None:
    # The Engine enforces service auth on every route but the exempt ones (Engine #44).
    response = EngineClient(url, key)._request("GET", "/stories")
    assert response.status_code == 200, response.text


@pytest.mark.engine_auth
def test_story_create_archive_list_delete_live(
    url: str, key: str, operator_client: Client, operator: object
) -> None:
    """#45: create a story in the browser, archive it, see it listed, delete it."""
    ratings = EngineClient(url, key).list_content_ratings()
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        form = operator_client.get("/stories/new/").content.decode()
        signed = re.search(r'name="rating_choices" value="([^"]+)"', form)
        assert signed is not None, form
        response = operator_client.post(
            "/stories/new/",
            {
                "name": "Console smoke harbour",
                "content_rating_id": ratings[0].id,
                "viewpoint": "",
                "idempotency_key": new_ulid(),
                "rating_choices": signed.group(1),
                "characters-TOTAL_FORMS": "2",
                "characters-INITIAL_FORMS": "0",
                "characters-0-name": "Mara",
                "characters-0-definition": "A harbour pilot.",
                "characters-0-true_age": "34",
                "characters-0-is_player": "on",
                "characters-1-name": "Tobin",
                "characters-1-definition": "Her brother.",
                "characters-1-true_age": "41",
            },
        )
        assert response.status_code == 302, response.content.decode()
        story_id = response["Location"].strip("/").rsplit("/", 1)[-1]
        try:
            archived = operator_client.post(
                f"/stories/{story_id}/archive/", {"idempotency_key": new_ulid()}
            )
            assert archived.status_code == 302, archived.content.decode()
            listing = operator_client.get("/stories/").content.decode()
            assert f"<code>{story_id}</code>" in listing  # archived stories are listed
            row = listing[listing.index(f"<code>{story_id}</code>") :]
            assert (
                '<td class="status"><span title="archived">Archived</span></td>'
                in row[: row.index("</tr>")]
            )
            assert "<td>2</td>" in row[: row.index("</tr>")]  # the cast
        finally:
            deleted = operator_client.post(
                f"/stories/{story_id}/delete/", {"idempotency_key": new_ulid()}
            )
        assert deleted.status_code == 302, deleted.content.decode()
        assert f"<code>{story_id}</code>" not in operator_client.get("/stories/").content.decode()


@pytest.mark.engine_auth
def test_story_refusal_is_placed_beside_the_row_live(
    url: str, key: str, operator_client: Client
) -> None:
    """#45 demo step 3: two characters named "Mara" — the Engine's refusal sits on row 2."""
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        form = operator_client.get("/stories/new/").content.decode()
        signed = re.search(r'name="rating_choices" value="([^"]+)"', form)
        # The rating select's own selected option: the nav's "Acting as" select (#64) also has one.
        ratings = form[form.index('name="content_rating_id"') :]
        default = re.search(r'<option value="([^"]+)" selected>', ratings)
        assert signed is not None, form
        assert default is not None, form
        page = operator_client.post(
            "/stories/new/",
            {
                "content_rating_id": default.group(1),
                "idempotency_key": new_ulid(),
                "rating_choices": signed.group(1),
                "characters-TOTAL_FORMS": "2",
                "characters-INITIAL_FORMS": "0",
                "characters-0-name": "Mara",
                "characters-0-definition": "A harbour pilot.",
                "characters-0-true_age": "34",
                "characters-1-name": "mara",
                "characters-1-definition": "Another Mara.",
                "characters-1-true_age": "30",
            },
        ).content.decode()
    second = page[page.index('id="character-1"') :]
    assert "same name" in second[: second.index("</fieldset>")], page
    assert "The Engine rejected the story:" in page


def logged_paths() -> list[str]:
    """Every operation-log row's Engine path (the ``path`` column; ``request`` holds no path)."""
    return [op.path for op in EngineOperation.objects.order_by("pk")]


def embedding_writes() -> list[str]:
    """The logged writes to the embedding purpose's model configuration (#59: never in a smoke)."""
    return [p for p in logged_paths() if "/model-config/embedding" in p]


def _model_row(html: str, purpose: str) -> str:
    match = re.search(rf'<tr id="purpose-{purpose}">(.*?)</tr>', html, re.S)
    assert match is not None, html
    return match.group(1)


@pytest.mark.engine_auth
def test_model_purpose_edit_key_and_drop_live(
    url: str, key: str, operator_client: Client, operator: object
) -> None:
    """#46: edit a purpose, give it a key (never shown again), drop its override."""
    secret = f"sk-console-smoke-{new_ulid()}"
    purpose = "quality_judge"
    blank = dict.fromkeys(("base_url", "temperature", "top_p", "max_tokens", "timeout", "stop"), "")
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        try:
            saved = operator_client.post(
                f"/models/{purpose}/edit/",
                {**blank, "model": "console-smoke", "seed": "7", "idempotency_key": new_ulid()},
            )
            assert saved.status_code == 302, saved.content.decode()
            keyed = operator_client.post(
                f"/models/{purpose}/api-key/", {"api_key": secret, "idempotency_key": new_ulid()}
            )
            assert keyed.status_code == 302, keyed.content.decode()
            listing = operator_client.get("/models/").content.decode()
            row = _model_row(listing, purpose)
            assert "console-smoke" in row
            assert "own" in row
            assert "set (fingerprint" in row
            assert secret not in listing
            # #59: embedding is editable, but the smoke never saves it (no real re-embed).
            embedding = _model_row(listing, "embedding")
            assert 'href="/models/embedding/edit/"' in embedding
            assert "Configured in M4" not in listing
        finally:
            dropped = operator_client.post(
                f"/models/{purpose}/drop/", {"idempotency_key": new_ulid()}
            )
        assert dropped.status_code == 302, dropped.content.decode()
        row = _model_row(operator_client.get("/models/").content.decode(), purpose)
        assert "console-smoke" not in row
        assert "not set" in row
        # The guard reads real rows: this test's own purpose writes are among them (#173).
        assert [p for p in logged_paths() if p.endswith(f"/model-config/{purpose}")]
        assert embedding_writes() == []  # no embedding write


@pytest.mark.engine_auth
def test_policy_create_edit_retire_and_retired_refusal_live(
    url: str, key: str, operator_client: Client, operator: object
) -> None:
    """#47's demo against the real Engine: create a global wordlist policy, edit it, retire it,
    then an edit shows the Engine's retired refusal."""
    slug = f"console-smoke-{new_ulid().lower()}"
    form = {
        "id": slug,
        "scope": "global",
        "name": f"no ferries {slug[-6:]}",
        "applies_to": ["text_output"],
        "rule_type": "wordlist",
        "words": "ferry",
        "on_uncertain": "violation",
        "age_condition": "",
        "message": "Ferries are off-limits",
    }
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        created = operator_client.post(
            "/guardrails/policies/new/", {**form, "idempotency_key": new_ulid()}
        )
        assert created.status_code == 302, created.content.decode()
        assert created["Location"] == f"/guardrails/policies/{slug}/"
        try:
            content = {k: v for k, v in form.items() if k not in ("id", "scope")}
            saved = operator_client.post(
                f"/guardrails/policies/{slug}/edit/",
                {**content, "words": "ferry\nferries", "idempotency_key": new_ulid()},
            )
            assert saved.status_code == 302, saved.content.decode()
            detail = operator_client.get(f"/guardrails/policies/{slug}/").content.decode()
            assert "<li><code>ferries</code></li>" in detail
            assert "<dt>Version</dt><dd>2</dd>" in detail
            listing = operator_client.get("/guardrails/policies/").content.decode()
            assert 'id="policy-minors_no_sexual_content"' in listing
        finally:
            retired = operator_client.post(
                f"/guardrails/policies/{slug}/retire/", {"idempotency_key": new_ulid()}
            )
        assert retired.status_code == 302, retired.content.decode()
        refused = operator_client.post(
            f"/guardrails/policies/{slug}/edit/", {**content, "idempotency_key": new_ulid()}
        )
        assert refused.status_code == 200
        assert "Retired policies cannot be changed" in refused.content.decode()


@pytest.mark.engine_auth
def test_rating_create_policy_edit_cycle_and_default_live(
    url: str, key: str, operator_client: Client
) -> None:
    """#49's demo against the real Engine: a new rating inheriting from Explicit; a
    rating-scoped policy created in #47 lands in its ``policy_ids`` (the second write) and shows
    as its own guardrail; an edit makes version 2 and an unchanged save makes none; Explicit
    inheriting from it is the cycle message; retiring the default is refused."""
    client = EngineClient(url, key, principal="console:smoke")
    ratings = {r.name: r for r in client.list_content_ratings()}
    explicit = ratings["Explicit"].id
    default = client.get_deployment_settings().default_content_rating_id
    name = f"Smoke {new_ulid()[-8:]}"
    base = "/guardrails/ratings/"
    form = {
        "name": name,
        "description": "Console smoke rating",
        "display_order": "",
        "prompt_guidance": "Smoke guidance.",
        "image_guidance": "",
        "inherits_from": [explicit],
    }
    with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
        created = operator_client.post(f"{base}new/", {**form, "idempotency_key": new_ulid()})
        assert created.status_code == 302, created.content.decode()
        rating_id = created["Location"].removeprefix(base).strip("/")
        policy_id = ""
        try:
            policy = operator_client.post(
                "/guardrails/policies/new/",
                {
                    "id": "",
                    "scope": f"rating:{rating_id}",
                    "name": f"{name} policy",
                    "applies_to": ["text_output"],
                    "rule_type": "wordlist",
                    "words": "wyvern",
                    "on_uncertain": "violation",
                    "age_condition": "",
                    "message": "No wyverns",
                    "idempotency_key": new_ulid(),
                },
            )
            assert policy.status_code == 302, policy.content.decode()
            policy_id = policy["Location"].removeprefix("/guardrails/policies/").strip("/")
            assert client.get_content_rating(rating_id).policy_ids == [policy_id]
            detail = operator_client.get(f"{base}{rating_id}/").content.decode()
            assert re.search(rf'<tr class="own"><td><code>{policy_id}</code>', detail)
            assert f"{name} → Explicit" in detail

            edit = {
                **form,
                "display_order": str(client.get_content_rating(rating_id).display_order),
                "policy_ids": [policy_id],
            }
            saved = operator_client.post(
                f"{base}{rating_id}/edit/",
                {**edit, "prompt_guidance": "Edited.", "idempotency_key": new_ulid()},
            )
            assert saved.status_code == 302, saved.content.decode()
            same = operator_client.post(
                f"{base}{rating_id}/edit/",
                {**edit, "prompt_guidance": "Edited.", "idempotency_key": new_ulid()},
            )
            assert same.status_code == 302
            versions = client.list_content_rating_versions(rating_id)
            assert sorted(v.version for v in versions) == [1, 2, 3]  # create, second write, edit

            current = client.get_content_rating(explicit)
            cycle = operator_client.post(
                f"{base}{explicit}/edit/",
                {
                    "name": current.name,
                    "description": current.description,
                    "display_order": str(current.display_order),
                    "prompt_guidance": current.prompt_guidance,
                    "image_guidance": current.image_guidance,
                    "policy_ids": current.policy_ids,
                    "inherits_from": [rating_id],
                    "idempotency_key": new_ulid(),
                },
            )
            assert "That would make Explicit inherit from itself" in cycle.content.decode()

            refused = operator_client.post(f"{base}{default}/retire/", {"name": "default"})
            assert "This is the deployment default" in refused.content.decode()
        finally:
            if policy_id:
                client.retire_policy(policy_id, idempotency_key=new_ulid())
            retired = operator_client.post(f"{base}{rating_id}/retire/", {"name": name})
        assert retired.status_code == 302, retired.content.decode()


@pytest.mark.engine_auth
def test_play_one_turn_live(url: str, key: str, operator_client: Client, operator: object) -> None:
    """#48: play one Lite turn from the play view against the Engine's stub model adapter
    (engine-up.sh / compose.smoke.yml set ``EPISTREL_MODEL_ADAPTER=stub``)."""
    client = EngineClient(url, key, principal="console:smoke")
    ratings = client.list_content_ratings()
    created = client.create_story(
        {
            "name": "Console smoke turn",
            "content_rating_id": ratings[0].id,
            "characters": [
                {
                    "name": "Mara",
                    "definition": "A harbour pilot.",
                    "true_age": 34,
                    "is_player": True,
                },
                {"name": "Tobin", "definition": "Her brother.", "true_age": 41, "is_player": False},
            ],
        },
        idempotency_key=new_ulid(),
    )
    story_id = created.story_id
    assert story_id is not None
    mara, tobin = (c.id for c in created.characters)
    try:
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            page = operator_client.get(f"/stories/{story_id}/").content.decode()
            snapshot = re.search(r'name="snapshot" value="([^"]+)"', page)
            idempotency_key = re.search(r'name="idempotency_key" value="([^"]+)"', page)
            assert snapshot is not None, page
            assert idempotency_key is not None, page
            answer = operator_client.post(
                f"/stories/{story_id}/turn/",
                {
                    "snapshot": snapshot.group(1),
                    "idempotency_key": idempotency_key.group(1),
                    "actor": mara,
                    "say": "Is the ferry late again?",
                    "output_viewpoint": f"character:{tobin}",
                },
                HTTP_HX_REQUEST="true",
            ).content.decode()
        inspector = answer.split('<div id="turn-status"')[0]
        reply = re.search(r'<blockquote class="reply">(.+?)</blockquote>', inspector, re.S)
        assert reply is not None, answer
        assert reply.group(1).strip(), answer
        assert "<h4>Model calls (0)</h4>" not in inspector
        assert re.search(r"<h4>Model calls \(\d+\)</h4>", inspector), inspector
        assert "Is the ferry late again?" in answer.split('<div id="transcript"')[1]
        history = client.get_history(story_id, viewpoint=f"character:{tobin}")
        assert any(item.channel == "message" and item.text for item in history.entries)
    finally:
        client.delete_story(story_id, idempotency_key=new_ulid())


def _delete_when_idle(client: EngineClient, story_id: str, *, wait: float = 60.0) -> None:
    """Delete the story, retrying while the Engine answers 423 ``story_busy`` (a turn on a
    partitioned profile leaves background knowledge jobs running; the delete must wait for
    them), up to ``wait`` seconds."""
    deadline = time.monotonic() + wait
    while True:
        try:
            client.delete_story(story_id, idempotency_key=new_ulid())
            return
        except EngineError as exc:
            if exc.code != "story_busy" or time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


@pytest.mark.engine_auth
def test_two_primary_sequential_turn_live(
    url: str, key: str, operator_client: Client, operator: object
) -> None:
    """#200: as ``test_play_one_turn_live``, on a story with a player and two non-players
    assigned the seeded Full v2 (formerly Structured v2) profile (per-character renders): one
    turn from the play view naming both non-players as render characters, sequentially, answers
    200 with an ``order`` of length 2."""
    client = EngineClient(url, key, principal="console:smoke")
    ratings = client.list_content_ratings()
    created = client.create_story(
        {
            "name": "Console smoke two primaries",
            "content_rating_id": ratings[0].id,
            "characters": [
                {"name": "Wren", "definition": "A courier.", "true_age": 29, "is_player": True},
                {"name": "Marcus", "definition": "A smith.", "true_age": 44, "is_player": False},
                {"name": "Jenni", "definition": "A baker.", "true_age": 37, "is_player": False},
            ],
        },
        idempotency_key=new_ulid(),
    )
    story_id = created.story_id
    assert story_id is not None
    wren, marcus, jenni = (c.id for c in created.characters)
    try:
        client.assign_engine_profile(
            story_id,
            _full_profile(client),
            ignore_warnings=True,
            idempotency_key=new_ulid(),
            version=2,
        )
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            page = operator_client.get(f"/stories/{story_id}/").content.decode()
            snapshot = re.search(r'name="snapshot" value="([^"]+)"', page)
            idempotency_key = re.search(r'name="idempotency_key" value="([^"]+)"', page)
            assert snapshot is not None, page
            assert idempotency_key is not None, page
            assert '<fieldset class="multi-character">' in page  # the section is enabled
            answer = operator_client.post(
                f"/stories/{story_id}/turn/",
                {
                    "snapshot": snapshot.group(1),
                    "idempotency_key": idempotency_key.group(1),
                    "actor": wren,
                    "say": "Who baked this?",
                    "output_viewpoint": f"character:{marcus}",
                    "render_character_ids": [marcus, jenni],
                    "interaction_mode": "sequential",
                    f"order_{marcus}": "1",
                    f"order_{jenni}": "2",
                },
                HTTP_HX_REQUEST="true",
            ).content.decode()
        row = EngineOperation.objects.filter(story_id=story_id, kind="respond").first()
        assert row is not None, answer
        assert row.request["body"]["render_character_ids"] == [marcus, jenni]
        assert row.http_status == 200, answer
        assert isinstance(row.response, dict)
        assert len(row.response["order"]) == 2, row.response
    finally:
        _delete_when_idle(client, story_id)


def _full_profile(client: EngineClient) -> str:
    """The Full preset's id, read by its stable id (#282): it must be named ``Full``, be a
    preset and be at version 4 or later, so a renamed or moved seed fails loudly rather than
    silently testing another profile."""
    profile = client.get_engine_profile(FULL_PROFILE_ID)
    assert profile.name == "Full", f"{FULL_PROFILE_ID} is named {profile.name!r}, not 'Full'"
    assert profile.preset is True, f"{FULL_PROFILE_ID} is not a preset"
    assert profile.current_version >= 4, f"Full is at version {profile.current_version}"
    return FULL_PROFILE_ID


def _every_profile(client: EngineClient) -> list[EngineProfile]:
    """Every listed profile (``include_deleted`` off), through every cursor."""
    found: list[EngineProfile] = []
    cursor: str | None = None
    while True:
        profiles, cursor = client.list_engine_profiles(cursor=cursor, limit=ENGINE_PROFILES_PAGE)
        found.extend(profiles)
        if not cursor:
            return found


@pytest.mark.engine_auth
def test_m7_presets_live(url: str, key: str) -> None:
    """#293: the profile list names Lite, Standard, Full and Max at their four stable ids, each a
    preset, with Full at version 4 or later (other profiles are tolerated). **Complement:** no
    profile is named ``Structured`` any more."""
    client = EngineClient(url, key, principal="console:smoke")
    profiles = _every_profile(client)
    by_id = {p.id: p for p in profiles}
    for profile_id, name in PRESET_NAMES.items():
        assert profile_id in by_id, f"{name} ({profile_id}) is not listed"
        assert by_id[profile_id].name == name
        assert by_id[profile_id].preset is True
    assert by_id[FULL_PROFILE_ID].current_version >= 4
    assert [p.id for p in profiles if p.name == "Structured"] == []


def _knowledge_story(client: EngineClient, profile: str, version: int) -> str:
    """A story with ``test_play_one_turn_live``'s cast (no turn played), on ``profile`` pinned
    to ``version``."""
    ratings = client.list_content_ratings()
    created = client.create_story(
        {
            "name": f"Console smoke knowledge v{version}",
            "content_rating_id": ratings[0].id,
            "characters": [
                {
                    "name": "Mara",
                    "definition": "A harbour pilot.",
                    "true_age": 34,
                    "is_player": True,
                },
                {"name": "Tobin", "definition": "Her brother.", "true_age": 41, "is_player": False},
            ],
        },
        idempotency_key=new_ulid(),
    )
    assert created.story_id is not None
    client.assign_engine_profile(
        created.story_id,
        profile,
        ignore_warnings=True,
        idempotency_key=new_ulid(),
        version=version,
    )
    return created.story_id


@pytest.mark.engine_auth
def test_m5_knowledge_reads_live(url: str, key: str) -> None:
    """#186: on Full v2 (formerly Structured v2; partitions on) the three knowledge reads answer
    empty pages for a story with no turn; **Complement:** on Full v1 (formerly Structured v1,
    "Structured (M4)", no partitions) each is 409 ``module_off``."""
    client = EngineClient(url, key, principal="console:smoke")
    profile = _full_profile(client)
    stories: list[str] = []
    try:
        on = _knowledge_story(client, profile, 2)
        stories.append(on)
        off = _knowledge_story(client, profile, 1)
        stories.append(off)
        [player] = [c.id for c in client.get_story(on).cast if c.is_player]
        beliefs = client.inspect_beliefs(on, player)
        assert beliefs.beliefs == []
        assert beliefs.character == player
        assert client.inspect_divergences(on).items == []
        assert client.inspect_exposures(on, character=player).items == []
        [off_player] = [c.id for c in client.get_story(off).cast if c.is_player]
        for read in (
            lambda: client.inspect_beliefs(off, off_player),
            lambda: client.inspect_divergences(off),
            lambda: client.inspect_exposures(off, character=off_player),
        ):
            with pytest.raises(EngineError) as caught:
                read()
            assert caught.value.code == "module_off"
    finally:
        for story_id in stories:
            _delete_when_idle(client, story_id)


@pytest.mark.engine_auth
def test_m6_world_live(url: str, key: str) -> None:
    """#232: on Full v3 (formerly Structured v3; States and the clock on) a story with no turn
    reads no States, a clock at ``on`` and offset 0, no scheduled events and nothing to
    reconcile, and the provider registry parses; a declared ``gold`` set by +5 answers 15 at an
    inter-turn slot after turn 0, and the clock advances 30 minutes. **Complement:** on Full v2
    (formerly Structured v2; no States, no clock) ``get_states`` and ``advance_clock`` are 409
    ``module_off`` naming their module."""
    client = EngineClient(url, key, principal="console:smoke")
    profile = _full_profile(client)
    stories: list[str] = []
    try:
        on = _knowledge_story(client, profile, 3)
        stories.append(on)
        off = _knowledge_story(client, profile, 2)
        stories.append(off)
        states = client.get_states(on)
        assert states.states == []
        assert states.clock.level == "on"
        assert client.get_clock(on).offset == 0
        assert client.list_scheduled_events(on).events == []
        assert client.get_reconciliation(on).listings == []
        client.list_providers()
        definition = {"name": "gold", "type": "number", "initial_value": 10}
        revision = client.get_story(on).timeline_revision
        declared = client.declare_state(
            on, definition, expected_revision=revision, idempotency_key=new_ulid()
        )
        written = client.set_state(
            on,
            "gold",
            {"delta": 5, "reason": "live smoke"},
            expected_revision=declared.timeline_revision,
            idempotency_key=new_ulid(),
        )
        assert written.new_value == 15
        position = written.slot_position
        assert position is not None
        assert position.turn == 0
        assert position.slot_k >= 1
        advanced = client.advance_clock(
            on,
            reason="live smoke",
            minutes=30,
            expected_revision=written.timeline_revision,
            idempotency_key=new_ulid(),
        )
        assert advanced.after.offset == 30
        for write, module in (
            (lambda: client.get_states(off), "states"),
            (
                lambda: client.advance_clock(
                    off,
                    reason="live smoke",
                    minutes=30,
                    expected_revision=None,
                    idempotency_key=new_ulid(),
                ),
                "clock",
            ),
        ):
            with pytest.raises(EngineError) as caught:
                write()
            assert caught.value.code == "module_off"
            assert caught.value.details.get("module") == module
    finally:
        for story_id in stories:
            _delete_when_idle(client, story_id)


PRESET_IDS = set(PRESET_NAMES)
GZIP_MAGIC = b"\x1f\x8b"


def _export_ready(client: EngineClient, story_id: str, *, wait: float = 60.0) -> str:
    """Export the story and poll its job once a second until ``ready`` (E24: 60 s cap); the
    job id. A story the edge can't export skips the case with the code (E24's precondition)."""
    try:
        started = client.export_story(story_id, idempotency_key=uuid4().hex)
    except EngineError as exc:
        if exc.http_status in {409, 422}:
            pytest.skip(f"the edge refused the export: {exc.code}")
        raise
    deadline = time.monotonic() + wait
    while True:
        job = client.export_status(started.job_id)
        if job.status == "ready":
            return started.job_id
        assert job.status in {"queued", "running"}, (job.status, job.error)
        assert time.monotonic() < deadline, f"export still {job.status} after {wait:.0f} s"
        time.sleep(1.0)


@pytest.mark.engine_auth
def test_m7_portability_live(url: str, key: str) -> None:
    """#292: on a Full v4 story with no turn, the rewrite, amendment, outbox and prune-run lists
    answer empty, the operations list parses, there is no backfill, a pruning answers its
    impact unapplied, an export becomes ``ready`` and its bundle starts with the gzip magic,
    the certification and eval lists parse and the preset filter names the four presets.
    **Complement:** an unknown State's definition is 404, and the story is deleted in a
    ``finally``."""
    client = EngineClient(url, key, principal="console:smoke")
    story = _knowledge_story(client, FULL_PROFILE_ID, 4)
    try:
        assert client.list_rewrites(story).items == []
        assert client.list_amendments(story).items == []
        assert client.list_outbox(story).rows == []
        assert client.list_prune_runs(story).prune_runs == []
        assert client.list_operations(story).story_id == story
        with pytest.raises(EngineError) as missing:
            client.get_backfill(story)
        assert missing.value.code == "backfill_not_found"
        pruned = client.prune_story(
            story, ignore_warnings=False, expected_revision=None, idempotency_key=uuid4().hex
        )
        assert pruned.applied is False
        job_id = _export_ready(client, story)
        with client.open_export_bundle(job_id) as bundle:
            assert bundle.status == 200
            head = next(bundle.iter_bytes(2))
        assert head[:2] == GZIP_MAGIC
        client.list_certifications()
        client.list_eval_runs()
        presets, _ = client.list_engine_profiles(preset=True)
        assert {p.id for p in presets} == PRESET_IDS
        with pytest.raises(EngineError) as unknown:
            client.get_state_definition(story, "nope")
        assert unknown.value.http_status == 404
    finally:
        _delete_when_idle(client, story)


BACKFILL_ENDED = frozenset({"completed", "cancelled", "failed"})


@pytest.mark.engine_auth
@pytest.mark.xfail(
    strict=True,
    reason=(
        "nathanpond/Epistrel#783: GET /stories/{id} answers 500 while a backfill pauses the "
        "story (StoryPauseProgressBody requires {done, total})"
    ),
)
def test_backfill_pause_story_read_live(url: str, key: str) -> None:
    """#295 (E10's suspected Engine bug): on a Full story pinned below v4 with one played turn,
    assigning v4 with ``upgrade_mode: backfill`` starts a backfill; while it runs, the story read
    answers 200 with ``pause.cause == "backfill"``. Skips when the edge lacks the presets, starts
    no backfill, or the backfill ends before any read sees the pause."""
    client = EngineClient(url, key, principal="console:smoke")
    profile = _full_profile(client)
    # Full v3 -> v4 gains no data-producing capability on the edge, so nothing is backfilled;
    # v1 -> v4 gains several (partitions, States, the clock), which starts one.
    story = _knowledge_story(client, profile, 1)
    try:
        [player] = [c.id for c in client.get_story(story).cast if c.is_player]
        client.respond(
            story,
            {"actor_character_id": player, "player_input": {"say": "Is the ferry late?"}},
            idempotency_key=new_ulid(),
            prefer_async=False,
        )
        written = client.assign_engine_profile(
            story,
            profile,
            ignore_warnings=True,
            idempotency_key=new_ulid(),
            version=4,
            upgrade_mode="backfill",
        )
        if written.backfill is None:
            pytest.skip("assigning Full v4 started no backfill on this edge")
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            read = client.get_story(story)  # the 500 (#783) raises EngineError here
            if read.pause is not None:
                assert read.pause.cause == "backfill"
                return
            try:
                status = client.get_backfill(story).status
            except EngineError as exc:
                status = exc.code
            if status in BACKFILL_ENDED or status == "backfill_not_found":
                pytest.skip(f"the backfill ended ({status}) before a read saw the pause")
            time.sleep(0.25)  # a stub backfill runs for seconds: once a second could miss it
        pytest.fail("the backfill neither paused the story nor ended within 120 s")
    finally:
        _delete_when_idle(client, story, wait=120.0)


def _certification_estimate(client: EngineClient) -> CertificationRunRequested | None:
    """An unconfirmed request on Full: the estimate, or ``None`` for the edge's 503
    ``certification_unavailable`` (no eval harness), which passes as an answer."""
    refused: EngineError | None = None
    try:
        return client.request_certification_run_profile(
            FULL_PROFILE_ID, confirm=False, idempotency_key=new_ulid()
        )
    except EngineError as exc:
        refused = exc
    assert (refused.http_status, refused.code) == (503, "certification_unavailable")
    return None


@pytest.mark.engine_auth
@pytest.mark.xfail(
    strict=True,
    reason="#337: the edge image ships no eval sets, so every estimate is 0 model calls",
)
def test_certification_estimate_live(url: str, key: str) -> None:
    """#314: an unconfirmed certification request on Full answers its estimate (model calls,
    the fingerprint and the suites) with no run, or 503 ``certification_unavailable``; both
    pass. The complement is :func:`test_certification_estimate_queues_nothing_live`."""
    client = EngineClient(url, key, principal="console:smoke")
    answer = _certification_estimate(client)
    if answer is None:
        return
    assert answer.fingerprint
    assert answer.suites
    assert answer.run_id is None
    assert answer.estimate.model_calls > 0


@pytest.mark.engine_auth
def test_certification_estimate_queues_nothing_live(url: str, key: str) -> None:
    """#314's **complement:** the estimate creates no run (no ``run_id`` or ``job_id``), and the
    fingerprint's certifications are the same before and after it (a 503 passes, no skip)."""
    client = EngineClient(url, key, principal="console:smoke")
    first = _certification_estimate(client)
    if first is None:
        return
    assert first.run_id is None
    assert first.job_id is None
    before = [c.id for c in client.list_certifications(fingerprint=first.fingerprint).items]
    again = _certification_estimate(client)
    assert again is not None
    assert again.run_id is None
    assert again.job_id is None
    after = [c.id for c in client.list_certifications(fingerprint=first.fingerprint).items]
    assert after == before


@pytest.mark.engine_auth
def test_export_through_the_console_live(url: str, key: str, operator_client: Client) -> None:
    """#319: export the smoke's story from the Console's Export page, poll the job to ``ready``
    (E24: once a second, 60 s cap), then download it through the Console: the first two bytes
    are the gzip magic and the size is the job's. **Complement:** the story is deleted in a
    ``finally``."""
    client = EngineClient(url, key, principal="console:smoke")
    story = _knowledge_story(client, FULL_PROFILE_ID, 4)
    try:
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            started = operator_client.post(
                f"/stories/{story}/export/", {"idempotency_key": new_ulid()}
            )
            assert started.status_code == 303, started.content.decode()
            match = re.fullmatch(rf"/stories/{story}/export/([0-9A-Z]{{26}})/", started["Location"])
            assert match is not None, started["Location"]
            job_id = match.group(1)
            deadline = time.monotonic() + 60.0
            while True:
                job = client.export_status(job_id)
                if job.status == "ready":
                    break
                assert job.status in {"queued", "running"}, (job.status, job.error)
                assert time.monotonic() < deadline, f"export still {job.status} after 60 s"
                time.sleep(1.0)
            page = operator_client.get(started["Location"]).content.decode()
            assert "Ready to download" in page
            download = operator_client.get(f"{started['Location']}bundle/")
            assert download.status_code == 200
            body = b"".join(download.streaming_content)  # type: ignore[attr-defined]
        assert body[:2] == GZIP_MAGIC
        assert len(body) == job.size
        assert url not in download["Content-Disposition"]
    finally:
        _delete_when_idle(client, story)


IMPORT_ENDED = frozenset({"ready", "refused", "failed"})
IMPORT_STARTED = re.compile(r"Import started: job <code>([0-9A-Z]{26})</code>")


@pytest.mark.engine_auth
def test_import_through_the_console_live(
    url: str, key: str, operator_client: Client, settings: Any, tmp_path: Any
) -> None:
    """#320 (E24): export the smoke's story (#319's helper), then import its bundle through the
    Engine panel's Import page with "Forward only"; when the Engine warns, post "Import anyway".
    The Engine answers 202, a 303 to #321's job page (before #321 the page showed "Import
    started: job …"); the job is polled through ``import_status`` once a second (120 s cap) until
    it ends. Then (#321) the Console's job page is polled every 2 s (120 s cap) until it reads
    "Imported", and its story link opens a story that is ``active`` or ``paused``.
    **Complement:** both stories are deleted in a ``finally``."""
    settings.DATA_DIR = tmp_path
    client = EngineClient(url, key, principal="console:smoke")
    story = _knowledge_story(client, FULL_PROFILE_ID, 4)
    created: list[str] = []
    try:
        job_id = _export_ready(client, story)
        with client.open_export_bundle(job_id) as bundle:
            assert bundle.status == 200
            content = b"".join(bundle.iter_bytes())
        upload = SimpleUploadedFile("smoke.epistrel.tgz", content, content_type="application/gzip")
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            page = operator_client.post(
                "/engine/import/",
                {"file": upload, "upgrade_mode": "forward_only", "idempotency_key": new_ulid()},
            )
            assert page.status_code in (200, 303), page.content.decode()
            html = page.content.decode()
            if "confirm_token" in html:  # the impact warned: confirm it
                token = re.search(r'name="confirm_token" value="([^"]+)"', html)
                assert token is not None
                page = operator_client.post(
                    "/engine/import/",
                    {"confirm_token": token.group(1), "idempotency_key": new_ulid()},
                )
                html = page.content.decode()
            if page.status_code == 303:  # #321 registered the job page
                found = re.search(r"/engine/import/([0-9A-Z]{26})/", page["Location"])
            else:
                found = IMPORT_STARTED.search(html)
            assert found is not None, html
        import_job = found.group(1)
        deadline = time.monotonic() + 120.0
        while True:
            job = client.import_status(import_job)
            if job.story_id:
                created.append(job.story_id)
            if job.status in IMPORT_ENDED:
                break
            assert time.monotonic() < deadline, f"import still {job.status} after 120 s"
            time.sleep(1.0)
        assert job.status == "ready", (job.status, job.error)
        assert job.story_id is not None
        assert job.story_id != story  # always a new story
        # #321 (E24): the Console's job page, polled every 2 s (120 s cap), reads "Imported"
        # and its story link opens a story that is active or paused.
        job_page = f"/engine/import/{import_job}/"
        deadline = time.monotonic() + 120.0
        with override_settings(ENGINE_URL=url, ENGINE_SERVICE_KEY=key):
            while True:
                shown = operator_client.get(f"{job_page}?partial=1").content.decode()
                if "Imported</strong>" in shown:
                    break
                assert "hx-trigger" in shown, shown  # still polling, never ended otherwise
                assert time.monotonic() < deadline, "the job page isn't Imported after 120 s"
                time.sleep(2.0)
            full = operator_client.get(job_page)
            assert full.status_code == 200
            html = full.content.decode()
            link = re.search(r'<a class="import-story" href="(/stories/([0-9A-Z]{26})/)">', html)
            assert link is not None, html
            assert link.group(2) == job.story_id
            assert "Errors (" in html
            opened = operator_client.get(link.group(1))
            assert opened.status_code == 200
        assert client.get_story(job.story_id).status in {"active", "paused"}
    finally:
        for made in dict.fromkeys(created):
            _delete_when_idle(client, made)
        _delete_when_idle(client, story)
