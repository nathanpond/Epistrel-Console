"""Suggest ratings for a character or scenario (#82)."""

import copy
import json
from html import unescape
from typing import Any

import httpx
import pytest
from django.test import Client

from console.engine.models import RatingSuggestion, SuggestionModelCall
from console.engine.timeouts import rating_suggestion
from console.models import EngineOperation
from console.views.rating_suggestion import FALLBACK_RATINGS, parse_version, suggest_box
from tests.catalog.helpers import CHAR, ITEM, full, text
from tests.catalog.helpers import draft as character_draft
from tests.catalog.scenario_helpers import S_ITEM, SCN, answer_profiles, detail
from tests.catalog.scenario_helpers import draft as scenario_draft
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import (
    EVERYONE,
    EXPLICIT,
    MATURE,
    POLICIES,
    TEEN,
    TEEN_POLICY,
    answer_ratings,
    calls,
    error,
    rating,
)

pytestmark = pytest.mark.django_db

C_SUGGEST = "/catalog/characters/{character_id}/rating-suggestion"
S_SUGGEST = "/catalog/scenarios/{scenario_id}/rating-suggestion"
C_PATH = f"/catalog/characters/{CHAR}/rating-suggestion"
S_PATH = f"/catalog/scenarios/{SCN}/rating-suggestion"
C_URL = f"/catalog/characters/{CHAR}/rating-suggestion/"
S_URL = f"/catalog/scenarios/{SCN}/rating-suggestion/"
RATINGS = "/admin/content-ratings"
HTMX = {"HX-Request": "true"}
RETIRED = "01M5R0OLD00000000000000009"
CAST = "01JC00000000000000000CHAR2"
MEDIA = "01JC000000000000000000MED1"
SEED = "01JC0000000000000000SEED01"
RTV = {
    EVERYONE: "00000000000000000000RTV001",
    TEEN: "00000000000000000000RTV002",
    MATURE: "00000000000000000000RTV003",
    EXPLICIT: "00000000000000000000RTV004",
}


def location(entry: str = CHAR, version: int | str = "draft", **parts: Any) -> dict[str, Any]:
    return {"entry": entry, "version": version, **parts}


def concern(
    kind: str = "field",
    *,
    policy_id: str | None = "no_addresses",
    clause: str | None = None,
    explanation: str = "Graphic violence.",
    **where: Any,
) -> dict[str, Any]:
    loc = where.pop("location", None) or location(field="definition.background_flavor")
    return {
        "policy_id": policy_id,
        "clause": clause,
        "element": {"kind": kind, "location": loc},
        "explanation": explanation,
    }


def row(
    rating_id: str, name: str, appropriate: bool | None = True, **changes: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rating_id": rating_id,
        "rating_name": name,
        "rating_version": RTV.get(rating_id, "01M5R0RTV0000000000000OLD9"),
        "appropriate": appropriate,
        "concerns": [],
    }
    return {**body, **changes}


def suggestion(
    *rows: dict[str, Any], entry_type: str = "character", **changes: Any
) -> dict[str, Any]:
    entry_id = CHAR if entry_type == "character" else SCN
    body: dict[str, Any] = {
        "advisory": True,
        "entry": {"type": entry_type, "id": entry_id, "version": "draft"},
        "rows": list(rows)
        or [
            row(EVERYONE, "Everyone", False, concerns=[concern()]),
            row(TEEN, "Teen"),
            row(MATURE, "Mature"),
            row(EXPLICIT, "Explicit"),
        ],
        "evaluated": [{"rating_id": r, "rating_version": v} for r, v in RTV.items()],
        "unavailable": [],
        "model_calls": [
            {
                "purpose": "rating_suggestion",
                "model": "m-small",
                "attempt": 1,
                "outcome": "ok",
                "latency_ms": 812,
                "tokens_in": 12345,
                "tokens_out": 67,
                "cost": 0.00123,
                "request_id": "ignored",
            }
        ],
        "elapsed_ms": 2345,
    }
    return {**body, **changes}


def old_rating() -> dict[str, Any]:
    """A retired rating (listed only with ``include_retired``)."""
    changes = {"id": RETIRED, "name": "Old", "retired_at": "2026-01-01T00:00:00Z"}
    return {**rating("Teen"), **changes, "version_id": "01M5R0RTV0000000000000OLD9"}


def answer_lookups(engine: FakeEngine, *extra: dict[str, Any]) -> None:
    answer_ratings(engine, *extra)
    engine.respond("GET", POLICIES, 200, json=engine_fixture("list_policies"))


def answer(
    engine: FakeEngine, body: dict[str, Any] | None = None, *, scenario: bool = False
) -> None:
    answer_lookups(engine)
    route = S_SUGGEST if scenario else C_SUGGEST
    engine.respond("POST", route, 200, json=body or suggestion())


def post(client: Client, url: str = C_URL, *, htmx: bool = True, **data: str) -> Any:
    form = {"version": "draft", "name": "Sir Aldric", **data}
    return client.post(url, form, headers=HTMX if htmx else {})


def posted(engine: FakeEngine, path: str = C_PATH) -> Any:
    (request,) = calls(engine, "POST", path)
    return request


# --- the request ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "body", "heading"),
    [("draft", {"version": "draft"}, "Draft"), ("2", {"version": 2}, "Version 2")],
)
def test_the_body_names_the_version_and_the_header_the_entry(
    operator_client: Client, engine: FakeEngine, version: str, body: dict[str, Any], heading: str
) -> None:
    entry_version: int | str = 2 if version == "2" else "draft"
    answer(
        engine,
        suggestion(entry={"type": "character", "id": CHAR, "version": entry_version}),
    )
    html = post(operator_client, version=version).content.decode()
    request = posted(engine)
    assert json.loads(request.content) == body
    assert f"Rating suggestion for Sir Aldric (character) — {heading}" in text(html)
    assert "Advisory — the Engine does not apply this" in html


def test_a_blank_name_shows_the_unnamed_label(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = post(operator_client, name="").content.decode()
    assert f"Rating suggestion for (unnamed) …{CHAR[-6:]} (character) — Draft" in text(html)


@pytest.mark.parametrize(("ticked", "query"), [(True, "include_retired=true"), (False, "")])
def test_include_retired_is_sent_only_when_ticked(
    operator_client: Client, engine: FakeEngine, ticked: bool, query: str
) -> None:
    answer(engine)
    post(operator_client, include_retired="true" if ticked else "")
    assert posted(engine).url.query.decode() == query


def test_no_projection_and_no_idempotency_key(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    post(operator_client, include_retired="true")
    request = posted(engine)
    assert "projection" not in request.url.params
    assert "Idempotency-Key" not in request.headers
    assert request.headers["X-Epistrel-Principal"]


def test_it_is_recorded_as_a_rating_suggestion(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = post(operator_client).content.decode()
    (row_,) = EngineOperation.objects.filter(kind="rating_suggestion")
    assert (row_.entity_type, row_.entity_id) == ("character", CHAR)
    assert row_.idempotency_key == ""
    assert row_.outcome == "ok"
    assert f'href="/operations/{row_.pk}/">View in operations log</a>' in html


def test_a_scenario_suggestion_posts_to_the_scenario_route(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = suggestion(entry_type="scenario", entry={"type": "scenario", "id": SCN, "version": 2})
    answer(engine, body, scenario=True)
    html = post(operator_client, S_URL, version="2", name="Harvest Weekend").content.decode()
    assert json.loads(posted(engine, S_PATH).content) == {"version": 2}
    assert "Rating suggestion for Harvest Weekend (scenario) — Version 2" in text(html)
    (row_,) = EngineOperation.objects.filter(kind="rating_suggestion")
    assert (row_.entity_type, row_.entity_id) == ("scenario", SCN)


@pytest.mark.parametrize("version", ["", "0", "-1", "1.5", "latest", "\uff12"])
def test_a_bad_version_is_refused_without_engine_calls(
    operator_client: Client, engine: FakeEngine, version: str
) -> None:
    response = post(operator_client, version=version)
    assert response.status_code == 400
    assert "Choose a version" in response.content.decode()
    assert engine.requests == []


def test_get_goes_back_to_the_detail_page(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(C_URL)
    assert response.status_code == 302
    assert response["Location"] == f"/catalog/characters/{CHAR}/"
    assert operator_client.get(S_URL)["Location"] == f"/catalog/scenarios/{SCN}/"
    assert engine.requests == []


# --- timeouts ------------------------------------------------------------------------------------


def read_timeout(request: httpx.Request) -> float:
    return float(request.extensions["timeout"]["read"])


@pytest.mark.parametrize(("ticked", "count"), [(False, 4), (True, 5)])
def test_the_timeout_is_computed_from_the_rating_count(
    operator_client: Client, engine: FakeEngine, ticked: bool, count: int
) -> None:
    retired = old_rating()
    answer_lookups(engine, retired)
    engine.respond("POST", C_SUGGEST, 200, json=suggestion())
    post(operator_client, include_retired="true" if ticked else "")
    assert read_timeout(posted(engine)) == rating_suggestion(count) == 20.0 * count + 10
    # The ratings read always asks for the retired ones too (labels and retired marks).
    (ratings_read,) = calls(engine, "GET", RATINGS)
    assert ratings_read.url.params["include_retired"] == "1"


def test_a_failed_ratings_read_falls_back_to_ten_ratings(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", RATINGS, 503, json=error("database_unavailable"))
    engine.respond("GET", POLICIES, 200, json=engine_fixture("list_policies"))
    engine.respond("POST", C_SUGGEST, 200, json=suggestion())
    html = post(operator_client).content.decode()
    assert read_timeout(posted(engine)) == rating_suggestion(FALLBACK_RATINGS) == 210.0
    assert "Couldn&#x27;t load: ratings — names shown as ids" in html
    # Raw version ids and no retired marks without the ratings read.
    assert f"<code>{RTV[TEEN]}</code>" in html


def test_a_console_side_timeout_says_the_engine_may_still_be_working(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.fail("POST", C_SUGGEST, httpx.ReadTimeout("slow"))
    html = post(operator_client).content.decode()
    assert (
        "The suggestion didn't finish within 90 s; the Engine may still be working — run it again"
    ) in unescape(html)
    (row_,) = EngineOperation.objects.filter(kind="rating_suggestion")
    assert row_.outcome != "ok"


# --- errors --------------------------------------------------------------------------------------


@pytest.mark.parametrize("htmx", [True, False])
def test_a_404_says_that_version_does_not_exist(
    operator_client: Client, engine: FakeEngine, htmx: bool
) -> None:
    answer_lookups(engine)
    engine.respond("POST", C_SUGGEST, 404, json=error("not_found", "no such version"))
    html = post(operator_client, htmx=htmx, version="7").content.decode()
    assert "That version doesn&#x27;t exist" in html
    if not htmx:
        assert f'href="/catalog/characters/{CHAR}/?version=7">Back to Sir Aldric</a>' in html


def test_other_errors_show_the_engine_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("POST", C_SUGGEST, 503, json=error("erasure_pending", "held"))
    html = post(operator_client).content.decode()
    assert 'class="engine-error"' in html
    assert "erasure_pending" in html


# --- the table -----------------------------------------------------------------------------------


def test_rows_render_in_the_engines_order(operator_client: Client, engine: FakeEngine) -> None:
    body = suggestion(row(MATURE, "Mature"), row(EVERYONE, "Everyone"), row(EXPLICIT, "Explicit"))
    answer(engine, body)
    html = post(operator_client).content.decode()
    order = [html.index(f'id="suggestion-{r}"') for r in (MATURE, EVERYONE, EXPLICIT)]
    assert order == sorted(order)
    assert f'<a href="/guardrails/ratings/{MATURE}/">Mature</a>' in html
    assert "version 1" in html  # the rating's current version, by its listed id


def test_appropriate_marks(operator_client: Client, engine: FakeEngine) -> None:
    body = suggestion(
        row(EVERYONE, "Everyone", False, concerns=[concern()]),
        row(TEEN, "Teen", True),
        row(MATURE, "Mature", None, error="deadline"),
    )
    answer(engine, body)
    html = post(operator_client).content.decode()
    assert '✗<span class="visually-hidden"> not appropriate</span>' in html
    assert '✓<span class="visually-hidden"> appropriate</span>' in html
    mature = html[html.index(f'id="suggestion-{MATURE}"') :]
    assert '<td class="appropriate">unknown</td>' in mature
    assert "Couldn&#x27;t assess: deadline" in mature


def test_row_errors_truncation_and_dropped_concerns(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = suggestion(
        row(EVERYONE, "Everyone", False, concerns=[concern()], error="model_unavailable"),
        row(TEEN, "Teen", True, truncated=True, dropped_concerns=2),
        row(MATURE, "Mature", None, error="invalid_response"),
        row(EXPLICIT, "Explicit"),
    )
    answer(engine, body)
    html = post(operator_client).content.decode()
    start, end = html.index(f'id="suggestion-{EVERYONE}"'), html.index(f'id="suggestion-{TEEN}"')
    everyone = html[start:end]
    # An error with a deterministic concern: ✗, the concern and the error line.
    assert "✗" in everyone
    assert "Graphic violence." in everyone
    assert "Couldn&#x27;t assess: model_unavailable" in everyone
    teen = html[html.index(f'id="suggestion-{TEEN}"') : html.index(f'id="suggestion-{MATURE}"')]
    assert "The model saw only part of this entry (private text first)" in teen
    assert (
        "2 concerns dropped: they named a policy, clause or element the model wasn&#x27;t given"
        in teen
    )
    assert "Couldn&#x27;t assess: invalid_response" in html
    explicit = html[html.index(f'id="suggestion-{EXPLICIT}"') :]
    assert "<td>—</td>" in explicit[: explicit.index("</tr>")]


def test_a_retired_row_is_marked_from_the_ratings_list(
    operator_client: Client, engine: FakeEngine
) -> None:
    retired = old_rating()
    answer_lookups(engine, retired)
    engine.respond(
        "POST",
        C_SUGGEST,
        200,
        json=suggestion(row(TEEN, "Teen"), row(RETIRED, "Old", rating_version=RTV[TEEN])),
    )
    html = post(operator_client, include_retired="true").content.decode()
    old = html[html.index(f'id="suggestion-{RETIRED}"') :]
    assert "(retired)" in old[: old.index("</tr>")]
    teen = html[html.index(f'id="suggestion-{TEEN}"') : html.index(f'id="suggestion-{RETIRED}"')]
    assert "(retired)" not in teen


def test_an_unknown_rating_version_shows_the_raw_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, suggestion(row(TEEN, "Teen", rating_version="01M5R0RTV00000000000000OLD")))
    html = post(operator_client).content.decode()
    assert "<code>01M5R0RTV00000000000000OLD</code>" in html


def test_policies_and_clauses(operator_client: Client, engine: FakeEngine) -> None:
    body = suggestion(
        row(
            EVERYONE,
            "Everyone",
            False,
            concerns=[
                concern(policy_id=TEEN_POLICY),
                concern(policy_id="unknown_policy"),
                concern(policy_id=None, clause="No blood."),
                concern(policy_id=None),
            ],
        )
    )
    answer(engine, body)
    html = post(operator_client).content.decode()
    assert (
        f'Policy <a href="/guardrails/policies/{TEEN_POLICY}/">No gore words</a> '
        f"<code>{TEEN_POLICY}</code>"
    ) in html
    assert (
        'Policy <a href="/guardrails/policies/unknown_policy/"><code>unknown_policy</code></a>'
        in html
    )
    assert "Rating description: “No blood.”" in html
    assert "<td>—</td>" in html


def test_failed_policy_read_shows_ids_unlinked(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    engine.respond("GET", POLICIES, 503, json=error("database_unavailable"))
    engine.respond("POST", C_SUGGEST, 200, json=suggestion())
    html = post(operator_client).content.decode()
    assert "Couldn&#x27;t load: policies — names shown as ids" in html
    assert "Policy <code>no_addresses</code>" in html


ELEMENTS = [
    (
        concern(location=location(field="definition.background_flavor")),
        f'Field <a href="/catalog/characters/{CHAR}/edit/#f-definition-background_flavor">'
        "<code>definition.background_flavor</code></a>",
    ),
    (
        concern(location=location(version=2, field="definition.appearance")),
        f'Field <a href="/catalog/characters/{CHAR}/?version=2#f-definition-appearance">'
        "<code>definition.appearance</code></a> (version 2)",
    ),
    (
        concern("reveal", location=location(field="definition.likes_dislikes.likes[0]")),
        f'Reveal at <a href="/catalog/characters/{CHAR}/edit/'
        '#f-definition-likes_dislikes-likes-0-">'
        "<code>definition.likes_dislikes.likes[0]</code></a>",
    ),
    (
        concern("media_caption", location=location(media_id=MEDIA)),
        f'Caption of media <a href="/media/{MEDIA}/"><code>{MEDIA}</code></a>',
    ),
]


@pytest.mark.parametrize(("item", "expected"), ELEMENTS)
def test_character_element_wording_and_links(
    operator_client: Client, engine: FakeEngine, item: dict[str, Any], expected: str
) -> None:
    answer(engine, suggestion(row(EVERYONE, "Everyone", False, concerns=[item])))
    html = post(operator_client).content.decode()
    assert expected in html


SCENARIO_ELEMENTS = [
    (
        concern("seed", location=location(SCN, seed_id=SEED, role="hero")),
        f'Seed <a href="/catalog/scenarios/{SCN}/edit/#seed-{SEED}"><code>{SEED}</code></a> '
        "(role <code>hero</code>)",
    ),
    (
        concern("role", location=location(SCN, role="hero")),
        f'Role <a href="/catalog/scenarios/{SCN}/edit/#role-hero"><code>hero</code></a>',
    ),
    (
        concern("cast_character", location=location(CAST, version=3, role="hero")),
        f'Cast character <a href="/catalog/characters/{CAST}/?version=3"><code>{CAST}</code></a> '
        "(role <code>hero</code>, version 3)",
    ),
    (
        concern("field", location=location(CAST, version=3, field="definition.personality")),
        f'Field <a href="/catalog/characters/{CAST}/?version=3#f-definition-personality">'
        "<code>definition.personality</code></a> (version 3)",
    ),
]


@pytest.mark.parametrize(("item", "expected"), SCENARIO_ELEMENTS)
def test_scenario_element_wording_and_links(
    operator_client: Client, engine: FakeEngine, item: dict[str, Any], expected: str
) -> None:
    body = suggestion(row(EVERYONE, "Everyone", False, concerns=[item]), entry_type="scenario")
    answer(engine, body, scenario=True)
    html = post(operator_client, S_URL).content.decode()
    assert expected in html


def test_unavailable_roles(operator_client: Client, engine: FakeEngine) -> None:
    body = suggestion(
        entry_type="scenario",
        unavailable=[
            {"role": "hero", "entry": CAST, "version": 2, "reason": "character_unavailable"},
            {"role": "rival", "entry": CHAR, "version": None, "reason": "character_unavailable"},
        ],
    )
    answer(engine, body, scenario=True)
    html = post(operator_client, S_URL).content.decode()
    assert (
        f'Role <code>hero</code>: character <a href="/catalog/characters/{CAST}/?version=2">'
        f"<code>{CAST}</code></a> unavailable (version 2)"
    ) in html
    assert (
        f'Role <code>rival</code>: character <a href="/catalog/characters/{CHAR}/">'
        f"<code>{CHAR}</code></a> unavailable (latest)"
    ) in html


def test_model_calls_and_elapsed_time(operator_client: Client, engine: FakeEngine) -> None:
    body = suggestion()
    body["model_calls"].append({"purpose": "rating_suggestion", "cost": None, "extra": 1})
    answer(engine, body)
    html = post(operator_client).content.decode()
    assert "2 model calls · 2.3 s" in html
    assert (
        "<td>rating_suggestion</td><td>m-small</td><td>1</td><td>ok</td><td>812</td>"
        "<td>12,345</td><td>67</td><td>0.0012</td>"
    ) in html
    # A call missing its fields shows "—" in each, never skipped.
    assert "<td>rating_suggestion</td><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>" in html


def test_evaluated_goes_in_more(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = post(operator_client).content.decode()
    more = html[html.index('class="suggestion-more"') :]
    assert f'<td><a href="/guardrails/ratings/{TEEN}/">Teen</a></td><td>version 1</td>' in more


def test_run_again_reposts_the_same_parameters(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    html = post(operator_client, version="2", include_retired="true").content.decode()
    form = html[html.index('class="rating-suggestion-rerun"') :]
    assert f'hx-post="{C_URL}"' in form
    assert 'hx-disable="find button"' in form
    assert '<input type="hidden" name="version" value="2">' in form
    assert '<input type="hidden" name="include_retired" value="true">' in form
    assert '<input type="hidden" name="name" value="Sir Aldric">' in form
    assert "Run again" in form
    assert "Asking the model about each rating — this can take a minute" in form


def test_without_js_a_page_links_back_to_the_entry(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine)
    html = post(operator_client, htmx=False).content.decode()
    assert "<!doctype html>" in html.lower()
    assert f'href="/catalog/characters/{CHAR}/?view=draft">Back to Sir Aldric</a>' in html
    assert 'id="rating-suggestion"' in html


def test_complement_nothing_is_written(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine)
    post(operator_client)
    post(operator_client, version="1", include_retired="true")
    methods = {(r.method, r.url.path) for r in engine.requests}
    assert methods == {("GET", RATINGS), ("GET", POLICIES), ("POST", C_PATH)}
    assert not [r for r in engine.requests if r.method in {"PATCH", "PUT", "DELETE"}]


# --- the trigger on the detail pages -------------------------------------------------------------


def character_detail(engine: FakeEngine, *answers: dict[str, Any]) -> None:
    engine.respond_sequence("GET", ITEM, [{"status": 200, "json": a} for a in answers])


def test_the_character_detail_has_the_trigger(operator_client: Client, engine: FakeEngine) -> None:
    character_detail(engine, full(), character_draft())
    html = operator_client.get(f"/catalog/characters/{CHAR}/").content.decode()
    section = html[html.index('id="rating-suggestion-section"') :]
    assert f'hx-post="{C_URL}"' in section
    assert 'hx-target="#rating-suggestion"' in section
    assert 'hx-disable="find button"' in section
    assert "Asking the model about each rating — this can take a minute" in section
    assert '<option value="draft">Draft</option>' in section
    assert '<option value="2" selected>Version 2</option><option value="1">Version 1</option>' in (
        section
    )
    assert '<input type="checkbox" name="include_retired" value="true">' in section
    assert '<input type="hidden" name="name" value="Sir Aldric">' in section
    assert '<div id="rating-suggestion"' in section
    # Below the metadata section, and no suggestion was asked for.
    assert html.index('<section id="metadata">') < html.index('id="rating-suggestion-section"')
    assert not calls(engine, "POST")


def test_the_draft_view_selects_the_draft(operator_client: Client, engine: FakeEngine) -> None:
    character_detail(engine, character_draft())
    html = operator_client.get(f"/catalog/characters/{CHAR}/?view=draft").content.decode()
    assert '<option value="draft" selected>Draft</option>' in html


def test_the_public_card_view_has_no_trigger(operator_client: Client, engine: FakeEngine) -> None:
    card = copy.deepcopy(engine_fixture("get_catalog_character_card"))
    engine.respond("GET", ITEM, 200, json=card)
    html = operator_client.get(f"/catalog/characters/{CHAR}/?projection=card").content.decode()
    assert "rating-suggestion" not in html


def test_the_scenario_detail_has_the_trigger(operator_client: Client, engine: FakeEngine) -> None:
    answers = [{"status": 200, "json": detail()}, {"status": 200, "json": scenario_draft()}]
    engine.respond_sequence("GET", S_ITEM, answers)
    answer_ratings(engine)
    answer_profiles(engine)
    html = operator_client.get(f"/catalog/scenarios/{SCN}/").content.decode()
    section = html[html.index('id="rating-suggestion-section"') :]
    assert f'hx-post="{S_URL}"' in section
    assert '<input type="hidden" name="name" value="Harvest Weekend">' in section
    assert '<option value="2" selected>Version 2</option>' in section


# --- units ---------------------------------------------------------------------------------------


def test_parse_version() -> None:
    assert parse_version("draft") == "draft"
    assert parse_version(" 3 ") == 3
    for bad in (None, "", "0", "x", "-2", "²"):
        assert parse_version(bad) is None


def test_suggest_box_options() -> None:
    box = suggest_box("character", CHAR, name=None, current_version=None, shown=None)
    assert box.options == [("draft", "Draft")]
    assert box.selected == "draft"
    box = suggest_box("scenario", SCN, name="S", current_version=3, shown=2)
    assert [v for v, _ in box.options] == ["draft", "3", "2", "1"]
    assert box.selected == "2"


def test_model_calls_parse_leniently() -> None:
    parsed = RatingSuggestion.from_json(suggestion(model_calls=[{"attempt": True, "cost": 1}]))
    assert parsed is not None
    assert parsed.calls() == [SuggestionModelCall(cost=1.0)]
    assert SuggestionModelCall.from_raw("nonsense") == SuggestionModelCall()


def test_an_unknown_row_error_still_parses() -> None:
    body = suggestion(row(TEEN, "Teen", None, error="brand_new"))
    parsed = RatingSuggestion.from_json(body)
    assert parsed is not None
    assert parsed.rows[0].error == "brand_new"
