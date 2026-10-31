"""The Console against the live Engine (``ENGINE_LIVE_URL``, ``ENGINE_LIVE_SERVICE_KEY``), through
its real client and pages.

Run by ci.yml's engine-smoke job against the Engine ``edge`` image; locally, see the README.
"""

import re
import time

import pytest
from django.test import Client, override_settings

from console.engine.client import ENGINE_PROFILES_PAGE, EngineClient, EngineError
from console.engine_api_check import check_engine_api
from console.engine_probe import probe
from console.ids import new_ulid
from console.models import EngineOperation
from tests.plugins.engine_live import live_key, live_url

pytestmark = pytest.mark.engine_live


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
            assert '<td class="status">archived</td>' in row[: row.index("</tr>")]
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
    assigned the seeded ``Structured`` profile v2 (per-character renders): one turn from the play
    view naming both non-players as render characters, sequentially, answers 200 with an
    ``order`` of length 2."""
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
            _structured_profile(client),
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


def _structured_profile(client: EngineClient) -> str:
    """The id of the one profile named exactly ``Structured`` (no hard-coded id)."""
    names: list[str] = []
    found: list[str] = []
    cursor: str | None = None
    while True:
        profiles, cursor = client.list_engine_profiles(cursor=cursor, limit=ENGINE_PROFILES_PAGE)
        names.extend(p.name for p in profiles)
        found.extend(p.id for p in profiles if p.name == "Structured")
        if not cursor:
            break
    assert len(found) == 1, f"expected one profile named 'Structured', saw {names}"
    return found[0]


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
    """#186: on ``Structured`` v2 (partitions on) the three knowledge reads answer empty pages
    for a story with no turn; **Complement:** on v1 ("Structured (M4)", no partitions) each is
    409 ``module_off``."""
    client = EngineClient(url, key, principal="console:smoke")
    profile = _structured_profile(client)
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
