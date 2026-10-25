"""Start a story from a scenario and see what was applied (#75)."""

import copy
import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console import twostage
from console.creation_report import CREATION_KIND, creation_row, has_creation_report
from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.forms.story_from_scenario import age_text
from console.models import EngineOperation
from console.views import catalog_scenarios, play, stories
from console.views import story_from_scenario as views
from tests.catalog.helpers import CHAR, CHAR2, card
from tests.catalog.scenario_helpers import answer_profiles, fixture
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.play.helpers import answer_page, story_json
from tests.ratings.helpers import TEEN, answer_ratings, calls, error, text

pytestmark = pytest.mark.django_db

SCN = "01JC000000000000000000SCN2"
STORY = "01JS000000000000000000STR1"
PERSONA = "01JP0000000000000000PERSA1"
PERSONA2 = "01JP0000000000000000PERSA2"
URL = "/stories/new/from-scenario/"
REPORT_URL = f"/stories/{STORY}/creation-report/"
S_ITEM = "/catalog/scenarios/{scenario_id}"
S_LIST = "/catalog/scenarios"
S_VERSIONS = "/catalog/scenarios/{scenario_id}/versions"
C_ITEM = "/catalog/characters/{character_id}"
C_LIST = "/catalog/characters"
PERSONAS = "/personas"
PROFILE = "/engine-profiles/{profile_id}"
STORIES = "/stories"
STORY_ITEM = "/stories/{story_id}"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"
RATING_VERSION = "01JRV00000000000000000TEEN"
POLICIES = [
    {
        "name": "No swearing",
        "rule": {"type": "wordlist", "words": ["darn"]},
        "message": "Keep it clean.",
        "applies_to": ["text_output"],
        "age_condition": "minor_involved_or_present",
    }
]
OVERRIDABLE = [
    "story_settings_defaults.interaction_mode",
    "story_settings_defaults.allow_new_arrivals",
    "story_settings_defaults.audit_retention_days",
    "story_settings_defaults.policies",
    "cast.max_characters_at_start",
]


# --- fixtures ----------------------------------------------------------------------------------


def role(role_id: str, kind: str = "npc", fill: str = "open", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": role_id,
        "public_label": role_id.capitalize(),
        "setup_description": f"The {role_id}.",
        "kind": kind,
        "fill": fill,
        "required": True,
    }
    return {**body, **changes}


def scenario(**changes: Any) -> dict[str, Any]:
    """A published scenario (v2) with one role of every kind: ``guard`` locked (Aldric),
    ``traveller`` open (eligibility set), ``elder`` default (Bram), ``extra`` optional open,
    ``hero`` the player, ``sidekick`` an optional player."""
    body = fixture("detail")
    body["cast"] = {
        "max_characters_at_start": 6,
        "roles": [
            role("guard", fill="locked", character={"catalog_id": CHAR, "version": 2}),
            role(
                "traveller",
                eligibility={
                    "labels": ["human"],
                    "content_ratings": [TEEN],
                    "age_range": [18, None],
                },
                setup_description="x" * 250,
            ),
            role("elder", fill="default", character={"catalog_id": CHAR2, "version": None}),
            role("extra", required=False),
            role("hero", kind="player"),
            role("sidekick", kind="player", required=False),
        ],
    }
    body["creation_rules"] = {"overridable": OVERRIDABLE, "bounds": {"audit_retention_days": 90}}
    body["story_settings_defaults"]["policies"] = copy.deepcopy(POLICIES)
    body["story_settings_defaults"]["default_output_viewpoint"] = None
    body.update(changes)
    return body


def persona(persona_id: str = PERSONA, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_persona"))
    body["id"] = persona_id
    body["owner_principal"] = "console:nate"
    return {**body, **changes}


def location(**changes: Any) -> dict[str, Any]:
    return {"entry": SCN, "version": 2, "field": None, "role": None, "seed_id": None, **changes}


def entry(code: str, outcome: str = "kept", **loc: Any) -> dict[str, Any]:
    return {
        "code": code,
        "location": location(**loc),
        "outcome": outcome,
        "rule": f"rule of {code}",
        "source": None,
        "text": None,
    }


def report(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "story_id": STORY,
        "counts": {
            "errors": 0,
            "conflicts": 0,
            "warnings": 3,
            "deferred": {
                "by_class": {"knowledge_seed": 1},
                "by_capability": {"structured_memory": 1},
            },
            "inactive": {"by_class": {"configuration": 1}, "by_capability": {"multi_player": 1}},
            "materialized": None,
        },
        "cast": [
            {
                "kind": "catalog",
                "id": CHAR,
                "version": 2,
                "name": "Aldric",
                "public_description": None,
                "images": {"primary": None, "gallery": []},
                "labels": [],
                "content_rating": None,
                "listing_status": "public",
                "status": "published",
                "character_id": "01JCHR000000000000000GUARD",
                "role_id": "guard",
                "source": {"catalog_id": CHAR, "version": 2},
            },
            {
                "kind": "ad_hoc",
                "character_id": "01JCHR0000000000000TRAVLR",
                "role_id": "traveller",
                "name": "Wren",
            },
            {
                "kind": "player",
                "character_id": "01JCHR00000000000000HER0",
                "role_id": "hero",
                "name": "Ari",
            },
        ],
        "errors": [],
        "warnings": [
            entry("profile_unavailable", "ignored", field="recommendation.engine_profile_id"),
            entry("rating_looser", role="guard"),
            entry("personality_in_role", role="guard"),
        ],
        "deferred": [
            {
                "field_ref": "seed:01JC0000000000000000SEED01",
                "field_class": "knowledge_seed",
                "location": location(seed_id="01JC0000000000000000SEED01"),
                "capability_needed": "structured_memory",
                "payload": {"text": "She hid the deed in the barn."},
            }
        ],
        "inactive": [
            {
                "name": "turn_concurrency",
                "value": "queue",
                "capability": "multi_player",
                "field_class": "configuration",
                "location": location(field="story_settings_defaults.turn_concurrency"),
            }
        ],
        "materialized": None,
        "unavailable": ["fact_consistency"],
    }
    return {**body, **changes}


def impact(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "affected_stories": [],
        "affected_count": 0,
        "recommendations_unmet": [],
        "guarantees_lost": [],
        "warnings": [],
        "capabilities_after": ["memory"],
        "quality_status_after": None,
        "fingerprint_before": None,
        "fingerprint_after": "fp-after",
    }
    return {**body, **changes}


def created(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "applied": True,
        "story_id": STORY,
        "timeline_revision": 2,
        "idempotency_key": KEY,
        "characters": [{"id": "01JCHR000000000000000GUARD", "name": "Aldric", "role_id": "guard"}],
        "content_rating": {"id": TEEN, "version_id": RATING_VERSION, "name": "Teen"},
        "scenario": {"id": SCN, "version": 2},
        "engine_profile": {"id": "lite-default", "version": 1},
        "report": report(),
        "impact": impact(),
    }
    return {**body, **changes}


def held() -> dict[str, Any]:
    unmet = {"capability": "structured_memory", "recommended_by": SCN}
    return {
        "applied": False,
        "story_id": None,
        "timeline_revision": 0,
        "idempotency_key": KEY,
        "characters": [],
        "content_rating": None,
        "report": report(story_id=None, cast=[]),
        "impact": impact(
            recommendations_unmet=[unmet], warnings=["the profile lacks structured_memory"]
        ),
    }


def characters_by_id(request: httpx.Request) -> httpx.Response:
    names = {CHAR: "Aldric", CHAR2: "Bram"}
    character_id = request.url.path.rsplit("/", 1)[-1]
    return httpx.Response(200, json=card(id=character_id, name=names.get(character_id)))


def answer_reads(engine: FakeEngine, body: dict[str, Any] | None = None) -> None:
    """Step 2's reads: the scenario, ratings, profiles, published characters, personas and the
    locked/default roles' cards."""
    engine.respond("GET", S_ITEM, 200, json=body if body is not None else scenario())
    answer_ratings(engine)
    answer_profiles(engine)
    engine.respond("GET", C_LIST, 200, json=engine_fixture("list_catalog_characters"))
    engine.respond(
        "GET", PERSONAS, 200, json={"items": [persona(), persona(PERSONA2)], "next_cursor": None}
    )
    engine.side_effect("GET", C_ITEM, characters_by_id)
    engine.respond("GET", PROFILE, 200, json=profile_body())


def profile_body() -> dict[str, Any]:
    return {
        "id": "lite-default",
        "name": "Lite default",
        "owner_principal": "system",
        "preset": True,
        "version": 1,
        "current_version": 1,
        "settings": {},
        "created_at": "2026-10-01T09:00:00.000000Z",
        "updated_at": "2026-10-01T09:00:00.000000Z",
        "deleted_at": None,
    }


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (views, play, stories, catalog_scenarios):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


@pytest.fixture
def client(operator_client: Client) -> Client:
    return operator_client


def step2(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_reads(engine, body)
    return client.get(URL, {"scenario": SCN, "version": ""})


def rendered(response: Any) -> dict[str, Any]:
    """What the rendered step-2 form posts back unchanged, plus its hidden inputs."""
    form = response.context["form"]
    data: dict[str, Any] = {}
    for name in form.fields:
        value = form[name].value()
        if name == "strict":
            if value:
                data[name] = "on"
            continue
        data[name] = "" if value is None else str(value)
    data.update(scenario=SCN, version="2", idempotency_key=KEY, op="create")
    return data


def filled(response: Any, **changes: str) -> dict[str, Any]:
    """The rendered form with the open roles filled: the traveller ad hoc ("Wren") and the
    hero with the first saved persona; ``changes`` applied over it (``__`` for ``-``)."""
    data = rendered(response)
    data.update(
        {
            "slot-traveller-mode": "adhoc",
            "slot-traveller-adhoc_name": "Wren",
            "slot-traveller-adhoc_definition": "A wandering bard.",
            "slot-traveller-adhoc_age": "25",
            "player-hero-persona_id": PERSONA,
            "player-sidekick-assign": "none",
        }
    )
    data.update({k.replace("__", "-"): v for k, v in changes.items()})
    return data


def posts(engine: FakeEngine) -> list[Any]:
    return calls(engine, "POST", STORIES)


def sent(request: Any) -> Any:
    return json.loads(request.content)


def create(client: Client, engine: FakeEngine, answer: Any = None, **changes: str) -> Any:
    """Render step 2, fill it and press Create; the Engine answers ``answer`` (201 default)."""
    response = step2(client, engine)
    status, body = answer if answer is not None else (201, created())
    engine.respond("POST", STORIES, status, json=body)
    return client.post(URL, filled(response, **changes))


# --- step 1 ------------------------------------------------------------------------------------


def test_step1_lists_published_scenarios_companions_marked_with_paging(
    client: Client, engine: FakeEngine
) -> None:
    body = fixture("list")
    body["next_cursor"] = "CURSOR2"
    engine.respond("GET", S_LIST, 200, json=body)
    response = client.get(URL, {"q": "harvest"})
    (listed,) = calls(engine, "GET", S_LIST)
    params = dict(listed.url.params)
    assert params["status"] == "published"
    assert params["include_companions"] == "true"
    assert params["limit"] == "50"
    assert params["q"] == "harvest"
    html = response.content.decode()
    assert "Harvest Weekend (…00SCN2)" in html
    assert "Mira&#x27;s story (…00SCN1) (companion)" in html
    assert "Next page" in html
    assert "cursor=CURSOR2" in html


def test_step1_choose_shows_the_version_select(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", S_LIST, 200, json=fixture("list"))
    engine.respond("GET", S_VERSIONS, 200, json=fixture("versions"))
    response = client.get(URL, {"pick": SCN})
    html = response.content.decode()
    assert f'name="scenario" value="{SCN}"' in html
    assert '<option value="">latest</option>' in html
    assert '<option value="1">v1' in html


# --- step 2 rendering --------------------------------------------------------------------------


def test_step2_reads_the_scenario_in_full_and_renders_one_row_per_role(
    client: Client, engine: FakeEngine
) -> None:
    response = step2(client, engine)
    (read,) = calls(engine, "GET", f"{S_LIST}/{SCN}")
    assert read.url.params["projection"] == "full"
    assert "version" not in read.url.params  # none given: the latest published
    html = response.content.decode()
    page = text(html)
    for role_id in ("guard", "traveller", "elder", "extra", "hero", "sidekick"):
        assert f'id="role-{role_id}"' in html
    assert "Locked: Aldric v2" in page
    assert "Keep Bram" in page
    assert "eligibility: labels: human · ratings: Teen · ages: 18+" in page
    assert "Leave empty" in html  # only the optional NPC role offers it
    assert html.count('value="empty"') == 1
    assert "No player" in html
    assert html.count('value="none"') == 1
    assert "x" * 200 + "…" in page
    assert "Full setup" in page
    # Each card read is pinned to the role's version (latest when none).
    cards = {
        r.url.path: dict(r.url.params) for r in engine.requests if "/characters/" in str(r.url)
    }
    assert cards[f"{C_LIST}/{CHAR}"]["version"] == "2"
    assert "version" not in cards[f"{C_LIST}/{CHAR2}"]
    # Pinned version, owner and the player pre-filled with the acting principal.
    assert 'name="version" value="2"' in html
    assert response.context["form"]["owner"].value() == "console:nate"
    assert response.context["form"]["player-hero-principal"].value() == "console:nate"
    assert "Ari (30) …PERSA1" in html
    # Overrides pre-filled with the scenario's values; one Cast limit field only.
    form = response.context["form"]
    assert form["ovr-0"].value() == "sequential"
    assert form["ovr-1"].value() == "true"
    assert form["ovr-2"].value() == 30
    assert json.loads(form["ovr-3"].value()) == POLICIES
    assert "ovr-4" not in form.fields
    assert form["max_characters_at_start"].value() == 6
    assert "(scenario&#x27;s rating)" in html
    assert "(Engine default)" in html
    assert "Creation bounds" in html


def test_step2_read_error_shows_the_panel_with_a_link_back(
    client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 422, json=error("not_published", "never published"))
    response = client.get(URL, {"scenario": SCN, "version": "9"})
    html = response.content.decode()
    assert "not_published" in html
    assert "Back to choosing a scenario" in html


def test_archived_scenario_disables_create_but_a_forced_post_is_sent(
    client: Client, engine: FakeEngine
) -> None:
    response = step2(client, engine, scenario(status="archived"))
    html = response.content.decode()
    assert "Archived — unarchive to start a story" in html
    assert 'value="create">Create story</button>' not in html
    engine.respond(
        "POST", STORIES, 422, json=error("entry_archived", "archived", field="body.scenario.id")
    )
    forced = client.post(URL, filled(response))
    assert len(posts(engine)) == 1
    assert "entry_archived" in forced.context["form"].top_errors[0]


def test_age_texts() -> None:
    assert age_text(None) == "any"
    assert age_text([None, None]) == "any"
    assert age_text([18, None]) == "18+"
    assert age_text([None, 12]) == "up to 12"
    assert age_text([12, 17]) == "12\u201317"


# --- the body ----------------------------------------------------------------------------------


def test_body_for_each_role_kind(client: Client, engine: FakeEngine) -> None:
    create(
        client,
        engine,
        slot__traveller__mode="catalog",
        slot__traveller__catalog_id=CHAR2,
        slot__traveller__version="3",
        slot__elder__mode="adhoc",
        slot__elder__adhoc_name="Edda",
        slot__elder__adhoc_definition="Line one\r\nline two",
        slot__elder__adhoc_age="80",
        slot__elder__adhoc_fallback="She nods.",
        slot__extra__mode="catalog",
        slot__extra__catalog_id=CHAR,
        player__sidekick__assign="player",
        player__sidekick__principal="alice",
        player__sidekick__persona_mode="adhoc",
        player__sidekick__persona_name="Kit",
        player__sidekick__persona_age="19",
        player__sidekick__persona_description="Eager.",
    )
    (post,) = posts(engine)
    body = sent(post)
    assert "guard" not in body["slots"]  # locked: never sent
    assert body["slots"] == {
        "traveller": {"catalog_id": CHAR2, "version": 3},
        "elder": {
            "ad_hoc": {
                "name": "Edda",
                "definition": "Line one\nline two",
                "true_age": 80,
                "safe_fallback_text": "She nods.",
            }
        },
        "extra": {"catalog_id": CHAR},
    }
    assert body["players"] == {
        "hero": {"principal": "console:nate", "persona": {"id": PERSONA}},
        "sidekick": {
            "principal": "alice",
            "persona": {"ad_hoc": {"name": "Kit", "age": 19, "description": "Eager."}},
        },
    }
    assert post.headers["Idempotency-Key"] == KEY


def test_kept_default_and_empty_optional_roles_omit_their_slots(
    client: Client, engine: FakeEngine
) -> None:
    create(client, engine)
    body = sent(posts(engine)[0])
    assert set(body["slots"]) == {"traveller"}  # elder kept, extra empty, guard locked
    assert body["slots"]["traveller"] == {
        "ad_hoc": {"name": "Wren", "definition": "A wandering bard.", "true_age": 25}
    }
    assert set(body["players"]) == {"hero"}  # the sidekick has no player


def test_blank_version_sends_the_shown_version_and_the_body_is_full(
    client: Client, engine: FakeEngine
) -> None:
    create(client, engine)
    (post,) = posts(engine)
    body = sent(post)
    assert body["scenario"] == {"id": SCN, "version": 2}
    assert body["projection"] == "full"
    assert "projection" not in post.url.params
    assert body["owner"] == "console:nate"
    assert body["strict"] is False
    # The POST re-read the scenario at the pinned version.
    reads = [r for r in calls(engine, "GET", f"{S_LIST}/{SCN}") if "version" in r.url.params]
    assert reads
    assert reads[-1].url.params["version"] == "2"


def test_overrides_send_only_changed_allowed_paths(client: Client, engine: FakeEngine) -> None:
    create(client, engine, ovr__0="simultaneous")
    assert sent(posts(engine)[0])["overrides"] == {
        "story_settings_defaults.interaction_mode": "simultaneous"
    }


def test_unchanged_overrides_send_nothing_and_default_sends_null(
    client: Client, engine: FakeEngine
) -> None:
    create(client, engine)
    assert "overrides" not in sent(posts(engine)[0])
    engine.reset()
    create(client, engine, ovr__1="", ovr__2="60", max_characters_at_start="4", strict="on")
    body = sent(posts(engine)[0])
    assert body["overrides"] == {
        "story_settings_defaults.allow_new_arrivals": None,
        "story_settings_defaults.audit_retention_days": 60,
    }
    assert body["max_characters_at_start"] == 4
    assert body["strict"] is True


def test_rating_profile_and_name_are_sent_only_when_chosen(
    client: Client, engine: FakeEngine
) -> None:
    create(client, engine)
    body = sent(posts(engine)[0])
    assert not {"content_rating_id", "engine_profile_id", "name"} & set(body)
    engine.reset()
    create(client, engine, content_rating_id=TEEN, engine_profile_id="structured", name="Night")
    body = sent(posts(engine)[0])
    assert body["content_rating_id"] == TEEN
    assert body["engine_profile_id"] == "structured"
    assert body["name"] == "Night"


def test_an_invalid_form_sends_nothing(client: Client, engine: FakeEngine) -> None:
    response = create(client, engine, slot__traveller__adhoc_name="", ovr__3="{bad")
    assert posts(engine) == []
    form = response.context["form"]
    assert form.errors["slot-traveller-adhoc_name"] == ["Enter a name."]
    assert "Enter valid JSON" in form.errors["ovr-3"][0]


def test_load_personas_lists_the_typed_principals_personas_without_sending(
    client: Client, engine: FakeEngine
) -> None:
    response = step2(client, engine)
    data = filled(response, player__hero__principal="alice", op="load_personas")
    engine.reset()
    page = client.post(URL, data)
    assert posts(engine) == []
    owners = [r.url.params.get("owner") for r in calls(engine, "GET", PERSONAS)]
    assert "alice" in owners
    assert page.context["show_errors"] is False


# --- answers -------------------------------------------------------------------------------------


def test_201_redirects_to_the_creation_report_with_every_group(
    client: Client, engine: FakeEngine
) -> None:
    response = create(client, engine)
    assert response.status_code == 302
    assert response.url == REPORT_URL
    page = client.get(REPORT_URL)
    html = page.content.decode()
    shown = text(html)
    assert STORY in html
    assert "Harvest Weekend v2" in shown
    assert f"Teen ( {RATING_VERSION} )" in shown
    assert "Lite default ( lite-default v1)" in shown
    assert "Aldric Guard (guard)" in shown
    assert f"{CHAR}@2" in shown
    assert f"/catalog/characters/{CHAR}/?version=2" in html
    assert "Wren Traveller (traveller) ad-hoc" in shown
    assert "Ari Hero (hero) player" in shown
    assert "0 errors, 0 conflicts, 3 warnings" in shown
    assert "Needs structured_memory" in shown
    assert "She hid the deed in the barn." in html  # the deferred payload
    assert "turn_concurrency" in shown
    assert "multi_player" in shown
    assert views.LITE_NOTICE in html
    assert "fact_consistency" in html  # unavailable checks
    # personality_in_role and rating_looser first among the warnings.
    warnings = html[html.index("report-warnings") :]
    assert (
        warnings.index("personality_in_role")
        < warnings.index("rating_looser")
        < warnings.index("profile_unavailable")
    )
    assert f'href="/stories/{STORY}/"' in html  # the play view
    assert "Impact report" in html


def test_the_creation_is_recorded_on_the_scenario_with_the_new_story(
    client: Client, engine: FakeEngine
) -> None:
    create(client, engine)
    row = EngineOperation.objects.get(kind=CREATION_KIND)
    assert (row.entity_type, row.entity_id) == ("scenario", SCN)
    assert row.story_id == STORY
    assert row.http_status == 201
    assert row.request["body"]["projection"] == "full"
    assert creation_row(STORY) == row


def test_a_structured_report_shows_materialized_under_more(
    client: Client, engine: FakeEngine
) -> None:
    materialized = [
        {
            "field_ref": "seed:1",
            "location": location(),
            "outcomes": ["canon"],
            "rule": None,
            "winner": None,
            "loser": None,
        }
    ]
    create(client, engine, answer=(201, created(report=report(materialized=materialized))))
    html = client.get(REPORT_URL).content.decode()
    assert views.LITE_NOTICE not in html
    assert "Materialized" in html
    assert "&quot;canon&quot;" in html


def test_applied_false_renders_the_confirm_and_create_anyway_resends_with_a_fresh_key(
    client: Client, engine: FakeEngine
) -> None:
    response = create(client, engine, answer=(200, held()))
    html = response.content.decode()
    assert views.NOT_CREATED in html
    assert "structured_memory" in html
    assert "the profile lacks structured_memory" in html
    assert "Create anyway" in html
    assert "Back" in html
    token = response.context["token"]
    fresh = response.context["idempotency_key"]
    assert fresh != KEY
    (first,) = posts(engine)
    engine.reset()
    engine.respond("POST", STORIES, 201, json=created())
    engine.respond("GET", S_ITEM, 200, json=scenario())
    confirmed = client.post(
        URL,
        {"op": "confirm", twostage.CONFIRM_FIELD: token, "idempotency_key": fresh},
    )
    assert confirmed.status_code == 302
    assert confirmed["Location"] == REPORT_URL
    (second,) = posts(engine)
    assert sent(second) == {**sent(first), "ignore_warnings": True}
    assert second.headers["Idempotency-Key"] == fresh
    # Complement: the held call's row names no story; the report lookup ignores it.
    held_row, created_row = EngineOperation.objects.filter(kind=CREATION_KIND).order_by("id")
    assert held_row.http_status == 200
    assert held_row.story_id == ""
    assert (held_row.entity_type, held_row.entity_id) == ("scenario", SCN)
    assert creation_row(STORY) == created_row


def test_a_second_applied_false_renders_the_page_again_with_a_new_payload(
    client: Client, engine: FakeEngine
) -> None:
    response = create(client, engine, answer=(200, held()))
    token = response.context["token"]
    engine.respond("POST", STORIES, 200, json=held())
    again = client.post(
        URL, {"op": "confirm", twostage.CONFIRM_FIELD: token, "idempotency_key": KEY}
    )
    assert views.NOT_CREATED in again.content.decode()
    assert (
        twostage.unsign(views.KIND, again.context["token"]).body
        == twostage.unsign(views.KIND, token).body
    )


@pytest.mark.parametrize("tamper", ["tampered", "expired"])
def test_an_expired_or_tampered_create_anyway_makes_zero_calls(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    token = twostage.sign(views.KIND, SCN, {"scenario": {"id": SCN, "version": 2}}, base=2)
    if tamper == "tampered":
        token = token[:-2] + "xx"
    else:
        later = time.time() + twostage.MAX_AGE_S + 5
        monkeypatch.setattr(time, "time", lambda: later)
    response = client.post(
        URL,
        {
            "op": "confirm",
            twostage.CONFIRM_FIELD: token,
            "idempotency_key": KEY,
            "scenario": SCN,
            "version": "2",
        },
    )
    assert engine.requests == []
    assert response.status_code == 302
    assert [str(m) for m in get_messages(response.wsgi_request)] == [twostage.EXPIRED_TEXT]


def test_back_rebuilds_step2_with_the_operators_input(client: Client, engine: FakeEngine) -> None:
    response = create(client, engine, answer=(200, held()), ovr__0="simultaneous")
    token = response.context["token"]
    engine.reset()
    back = client.post(URL, {"op": "back", twostage.CONFIRM_FIELD: token, "idempotency_key": KEY})
    assert posts(engine) == []
    form = back.context["form"]
    assert form["slot-traveller-mode"].value() == "adhoc"
    assert form["slot-traveller-adhoc_name"].value() == "Wren"
    assert form["slot-elder-mode"].value() == "keep"
    assert form["player-hero-persona_id"].value() == PERSONA
    assert form["player-sidekick-assign"].value() == "none"
    assert form["ovr-0"].value() == "simultaneous"
    # Sent again unchanged, it makes the same body.
    assert form.is_valid()
    assert form.body() == twostage.unsign(views.KIND, token).body


def test_a_422_after_create_anyway_rebuilds_step2_from_the_signed_body(
    client: Client, engine: FakeEngine
) -> None:
    response = create(client, engine, answer=(200, held()))
    token = response.context["token"]
    refused = report(errors=[entry("eligibility_failed", "rejected", role="traveller")])
    engine.respond("POST", STORIES, 422, json=error("eligibility_failed", "no", report=refused))
    page = client.post(
        URL, {"op": "confirm", twostage.CONFIRM_FIELD: token, "idempotency_key": KEY}
    )
    form = page.context["form"]
    assert form["slot-traveller-adhoc_name"].value() == "Wren"
    assert form.role_errors["traveller"] == ["eligibility_failed: rule of eligibility_failed"]
    assert page.context["refusal"] is not None


# --- refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "role_id"),
    [
        ("slot_required", "traveller"),
        ("locked_slot_reassigned", "guard"),
        ("player_role_fixed", "hero"),
        ("persona_required", "hero"),
        ("entry_archived", "elder"),
        ("not_published", "elder"),
        ("eligibility_failed", "traveller"),
    ],
)
def test_each_role_code_lands_on_its_role_row(
    client: Client, engine: FakeEngine, code: str, role_id: str
) -> None:
    refused = report(errors=[entry(code, "rejected", role=role_id)], warnings=[])
    response = create(client, engine, answer=(422, error(code, "refused", report=refused)))
    form = response.context["form"]
    assert form.role_errors == {role_id: [f"{code}: rule of {code}"]}
    html = response.content.decode()
    row = html[html.index(f'id="role-{role_id}"') :]
    assert f"{code}: rule of {code}" in row[: row.index("</fieldset>")]
    assert "Engine report" in html  # details.report below the form


def test_a_role_code_without_a_report_uses_details_role_then_the_field(
    client: Client, engine: FakeEngine
) -> None:
    response = create(client, engine, answer=(422, error("slot_required", "x", role="extra")))
    assert response.context["form"].role_errors == {"extra": ["slot_required: x"]}
    engine.reset()
    response = create(
        client,
        engine,
        answer=(422, error("persona_required", "y", field="body.players.hero.persona")),
    )
    assert response.context["form"].errors["player-hero-persona_mode"] == ["persona_required: y"]


def test_a_role_the_form_lacks_lands_at_the_top_naming_it(
    client: Client, engine: FakeEngine
) -> None:
    response = create(client, engine, answer=(422, error("slot_required", "x", role="ghost")))
    assert "Role ghost: slot_required: x" in response.context["form"].top_errors


@pytest.mark.parametrize(
    ("code", "field"),
    [
        ("cast_limit_exceeded", "max_characters_at_start"),
        ("rating_check_failed", "content_rating_id"),
        ("rating_retired", "content_rating_id"),
        ("profile_deleted", "engine_profile_id"),
        ("budget_below_safety_minimum", "engine_profile_id"),
    ],
)
def test_role_less_codes_land_beside_their_field(
    client: Client, engine: FakeEngine, code: str, field: str
) -> None:
    response = create(client, engine, answer=(422, error(code, "refused")))
    assert response.context["form"].errors[field] == [f"{code}: refused"]


def test_override_not_allowed_lands_on_its_override_field(
    client: Client, engine: FakeEngine
) -> None:
    refused = report(
        errors=[entry("override_not_allowed", "rejected", field="story_settings_defaults.policies")]
    )
    response = create(
        client, engine, answer=(422, error("override_not_allowed", "no", report=refused))
    )
    assert response.context["form"].errors["ovr-3"] == [
        "override_not_allowed: rule of override_not_allowed"
    ]


def test_validation_error_is_placed_by_details_field(client: Client, engine: FakeEngine) -> None:
    response = create(
        client,
        engine,
        answer=(
            422,
            error("validation_error", "too long", field="body.slots.traveller.ad_hoc.name"),
        ),
    )
    assert response.context["form"].errors["slot-traveller-adhoc_name"] == ["too long"]
    engine.reset()
    response = create(
        client,
        engine,
        answer=(
            422,
            error(
                "validation_error",
                "bad",
                field="body.overrides.story_settings_defaults.interaction_mode",
            ),
        ),
    )
    assert response.context["form"].errors["ovr-0"] == ["bad"]
    engine.reset()
    response = create(
        client, engine, answer=(422, error("validation_error", "what", field="body.mystery"))
    )
    assert "mystery: what" in response.context["form"].top_errors


@pytest.mark.parametrize(
    "code",
    ["seed_unnormalizable", "viewpoint_unresolved", "creation_conflicts", "guardrail_violation"],
)
def test_top_codes_show_at_the_top_of_the_form(
    client: Client, engine: FakeEngine, code: str
) -> None:
    response = create(client, engine, answer=(422, error(code, "refused")))
    form = response.context["form"]
    assert form.top_errors == [f"The Engine refused the story: {code} — refused"]
    assert not form.errors
    html = response.content.decode()
    assert html.index("top-errors") < html.index('id="role-guard"')


def test_503_model_unavailable_shows_the_panel_with_input_kept(
    client: Client, engine: FakeEngine
) -> None:
    body = engine_fixture("stories/error_model_unavailable")
    response = create(client, engine, answer=(503, body))
    html = response.content.decode()
    assert "model_unavailable" in html
    assert response.context["form"]["slot-traveller-adhoc_name"].value() == "Wren"


def test_a_failed_reread_sends_nothing_and_keeps_the_input(
    client: Client, engine: FakeEngine
) -> None:
    response = step2(client, engine)
    data = filled(response)
    engine.respond("GET", S_ITEM, 404, json=error("not_found", "gone"))
    page = client.post(URL, data)
    assert posts(engine) == []
    html = page.content.decode()
    assert "not_found" in html
    assert 'name="slot-traveller-adhoc_name" value="Wren"' in html
    assert 'name="op" value="load_personas">Retry' in html


# --- the creation report page and the entry points ----------------------------------------------


def test_the_report_page_is_rerendered_from_the_row_and_degrades_per_section(
    client: Client, engine: FakeEngine
) -> None:
    create(client, engine)
    engine.reset()
    engine.respond("GET", S_ITEM, 503, json=error("unavailable", "down"))
    engine.respond("GET", PROFILE, 404, json=error("not_found", "gone"))
    html = client.get(REPORT_URL).content.decode()
    shown = text(html)
    assert engine.requests
    assert not posts(engine)
    assert "scenario unavailable: 503 unavailable" in shown
    assert "lite-default v1 (profile unavailable: 404 not_found)" in shown
    assert "Aldric guard" in shown  # role id alone without the scenario's labels


def test_no_row_means_not_available(client: Client, engine: FakeEngine) -> None:
    response = client.get(REPORT_URL)
    assert views.NOT_AVAILABLE in response.content.decode()
    assert engine.requests == []
    assert has_creation_report(STORY) is False


def test_the_story_page_links_the_creation_report_only_when_a_row_exists(
    client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, story=story_json(id=STORY))
    assert "Creation report" not in client.get(f"/stories/{STORY}/").content.decode()
    create(client, engine)
    answer_page(engine, story=story_json(id=STORY))
    html = client.get(f"/stories/{STORY}/").content.decode()
    assert f'href="{REPORT_URL}">Creation report</a>' in html


def test_entry_points(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORIES, 200, json={"items": []})
    assert f'href="{URL}">New story from scenario' in client.get("/stories/").content.decode()
    answer_ratings(engine)
    engine.respond("GET", "/admin/settings", 200, json=engine_fixture("get_deployment_settings"))
    assert f'href="{URL}">From scenario' in client.get("/stories/new/").content.decode()


def test_post_stories_stays_listed_as_create_story() -> None:
    """``create_story_from_scenario`` (``listed=False``) leaves ``create_story``'s registration
    of ``POST /stories`` in place for the contract test."""
    from console.engine.client import OPERATIONS
    from console.engine.models import StoryCreated

    op = OPERATIONS["POST /stories"]
    assert op.response_model is StoryCreated
    assert op.entity is None
