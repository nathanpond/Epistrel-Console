"""Validate and publish scenarios, and edit a character's companion (#74)."""

import copy
import json
import re
from html import unescape
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import QueryDict
from django.test import Client

from console.catalog.scenario_report import headline, shown_report, unavailable_label
from console.engine.models import ValidationReport
from console.models import EngineOperation
from tests.catalog.helpers import (
    CHAR,
    COMPANION,
    ITEM,
    body_of,
    calls,
    card,
    error,
    text,
)
from tests.catalog.scenario_helpers import (
    COMPANION_SCN,
    DETAIL,
    EDIT,
    PATH,
    S_ITEM,
    SCN,
    answer_edit_reads,
    answer_profiles,
    answer_ratings,
    as_post,
    detail,
    draft,
    fixture,
    writes,
)
from tests.catalog.scenario_helpers import posted as editor_posted
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

VALIDATE = "/catalog/scenarios/{scenario_id}/validate"
PUBLISH = "/catalog/scenarios/{scenario_id}/publish"
VALIDATE_URL = f"/catalog/scenarios/{SCN}/validate/"
PUBLISH_URL = f"/catalog/scenarios/{SCN}/publish/"
COMPANION_EDIT = f"/catalog/scenarios/{COMPANION_SCN}/edit/"
COMPANION_PATH = f"/catalog/scenarios/{COMPANION_SCN}"
CHARACTER_DETAIL = f"/catalog/characters/{CHAR}/"
UNAVAILABLE = (
    "Checks the Engine could not run: Seed normalization (<code>seed_normalization</code>), "
    "Fact consistency (<code>fact_consistency</code>), State schema (<code>state_schema</code>)"
)
HTMX = {"HX-Request": "true"}


PAGE_HIDDEN = ("publish_idempotency_key", "next", "facts")


def posted(response: Any) -> QueryDict:
    """What the editor posts back unchanged, #74's hidden inputs included."""
    data = editor_posted(response)
    html = response.content.decode()
    form = html[html.index('class="scenario-form"') :]
    for name in PAGE_HIDDEN:
        match = re.search(rf'name="{name}" value="([^"]*)"', form)
        if match:
            data[name] = unescape(match.group(1))
    return data


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body)
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def detail_page(client: Client, engine: FakeEngine, query: str = "", **answers: Any) -> Any:
    """The detail page: the latest version, then the draft read for "differs" and Publish."""
    engine.respond_sequence(
        "GET",
        S_ITEM,
        [
            {"status": 200, "json": answers.get("latest", detail())},
            {"status": 200, "json": answers.get("draft", draft())},
        ],
    )
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(f"{DETAIL}{query}")
    assert response.status_code == 200
    return response


def hidden_value(html: str, name: str, form_class: str) -> str:
    form = html[html.index(f'class="{form_class}"') :]
    form = form[: form.index("</form>")]
    match = re.search(rf'name="{name}" value="([^"]*)"', form)
    assert match is not None, name
    return match.group(1)


def form_fields(html: str, form_class: str) -> dict[str, str]:
    form = html[html.index(f'class="{form_class}"') :]
    form = form[: form.index("</form>")]
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form))


def answer_validate(engine: FakeEngine, name: str = "validate", status: int = 200) -> None:
    engine.respond("POST", VALIDATE, status, json=fixture(name))


def published(**changes: Any) -> dict[str, Any]:
    return fixture("publish", **changes)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


# --- fixtures and report wording ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "status", "name"),
    [
        ("POST", VALIDATE, 200, "catalog_scenarios_validate"),
        ("POST", VALIDATE, 200, "catalog_scenarios_validate_invalid"),
        ("POST", PUBLISH, 201, "catalog_scenarios_publish"),
        ("POST", PUBLISH, 422, "catalog_scenarios_publish_invalid"),
        ("POST", PUBLISH, 422, "catalog_scenarios_character_not_published"),
        ("PATCH", S_ITEM, 422, "catalog_scenarios_companion_slots_fixed"),
        ("GET", COMPANION, 200, "catalog_characters_companion_card"),
        ("GET", S_ITEM, 200, "catalog_scenarios_companion_draft"),
    ],
)
def test_the_fixtures_match_the_schema(method: str, path: str, status: int, name: str) -> None:
    FakeEngine().respond(method, path, status, json=engine_fixture(name))


def report(errors: int = 0, warnings: int = 0) -> ValidationReport:
    entry = engine_fixture("catalog_scenarios_validate_invalid")["report"]["errors"][0]
    raw = {"errors": [entry] * errors, "warnings": [entry] * warnings, "unavailable": []}
    parsed = ValidationReport.from_json(raw)
    assert parsed is not None
    return parsed


@pytest.mark.parametrize(
    ("errors", "warnings", "expected"),
    [
        (0, 0, "Valid"),
        (0, 1, "Valid — 1 warning"),
        (0, 2, "Valid — 2 warnings"),
        (1, 0, "1 error"),
        (3, 0, "3 errors"),
        (2, 1, "2 errors, 1 warning"),
    ],
)
def test_the_headline_is_pluralised(errors: int, warnings: int, expected: str) -> None:
    assert headline(report(errors, warnings)) == expected


def test_unavailable_checks_show_their_label_and_unknown_codes_raw() -> None:
    assert unavailable_label("seed_normalization") == "Seed normalization (seed_normalization)"
    assert unavailable_label("new_check") == "new_check"


def test_an_entry_about_another_entry_links_to_a_role_character_or_shows_its_id() -> None:
    raw = engine_fixture("catalog_scenarios_validate_invalid")["report"]
    other = copy.deepcopy(raw["warnings"][0])
    other["location"]["entry"] = "01JC00000000000000000OTHR1"
    parsed = ValidationReport.from_json({**raw, "warnings": [raw["warnings"][0], other]})
    assert parsed is not None
    shown = shown_report(parsed, scenario_id=SCN, heading="Draft", characters={CHAR: "mother"})
    linked, unknown = shown.warnings
    assert (linked.entry_url, linked.entry_label) == (CHARACTER_DETAIL, "mother")
    assert unknown.other_entry
    assert not unknown.entry_url
    assert not shown.errors[0].other_entry  # the scenario's own entry


# --- validate -------------------------------------------------------------------------------------


def test_validate_from_the_edit_page_sends_the_draft_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(edit_page(operator_client, engine))
    answer_validate(engine)
    response = operator_client.post(VALIDATE_URL, as_post(data), headers=HTMX)
    (sent,) = calls(engine, "POST", f"{PATH}/validate")
    assert body_of(sent) == {"draft_revision": 5}
    assert "version" not in sent.url.params
    assert "Idempotency-Key" not in sent.headers
    html = response.content.decode()
    assert "<html" not in html  # the partial alone, swapped into #report
    assert 'class="headline valid">Valid</span>' in html
    assert "Draft revision 5" in html
    assert UNAVAILABLE in html


def test_validate_makes_no_patch_or_publish_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: Validate checks the saved draft; the unsaved edit in the posted form is not
    saved, and nothing is published."""
    data = posted(edit_page(operator_client, engine))
    data["public_card__name"] = "Renamed, unsaved"
    answer_validate(engine)
    operator_client.post(VALIDATE_URL, as_post(data))
    assert [(r.method, r.url.path) for r in writes(engine)] == [("POST", f"{PATH}/validate")]


def test_validate_with_a_version_sends_no_draft_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = detail_page(operator_client, engine, "?version=2").content.decode()
    fields = form_fields(html, "validate-form")
    assert fields["version"] == "2"
    assert "draft_revision" not in fields
    assert "Validate v2" in html
    assert "publish-form" not in html  # an older ?version=N view has no Publish
    answer_validate(engine)
    response = operator_client.post(VALIDATE_URL, fields)
    (sent,) = calls(engine, "POST", f"{PATH}/validate")
    assert sent.url.params["version"] == "2"
    assert body_of(sent) == {}
    page = response.content.decode()
    assert "<title>Validation — Harvest Weekend" in page
    assert "Version 2 — checked against the catalog as it stands now" in page
    assert f'href="/catalog/scenarios/{SCN}/?version=2">Back</a>' in page


def test_validate_on_the_draft_view_names_its_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", S_ITEM, 200, json=draft())
    answer_ratings(engine)
    answer_profiles(engine)
    html = operator_client.get(f"{DETAIL}?view=draft").content.decode()
    assert "Validate draft (revision 5)" in html
    assert form_fields(html, "validate-form")["draft_revision"] == "5"


def test_reports_render_through_the_shared_partial(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#160: one report partial for every page (#64's), not a scenario copy of it."""
    data = posted(edit_page(operator_client, engine, fixture("draft_cast")))
    answer_validate(engine, "validate_invalid")
    response = operator_client.post(VALIDATE_URL, as_post(data), headers=HTMX)
    used = [t.name for t in response.templates]
    assert "console/_validation_report.html" in used
    assert not [name for name in used if name and name.startswith("console/catalog/scenarios/_r")]


def test_an_entry_about_the_scenario_itself_shows_only_its_field(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#161: no "entry <own id> vdraft"; an entry about another id keeps it, with its version."""
    data = posted(edit_page(operator_client, engine))
    answer_validate(engine, "validate_invalid")
    html = operator_client.post(VALIDATE_URL, as_post(data), headers=HTMX).content.decode()
    errors = html[html.index('<ul class="report-errors">') : html.index("</ul>")]
    assert SCN not in errors
    assert "vdraft" not in html
    assert (
        '<span class="location">field <a href="#f-starting_state-opening-narration">'
        "<code>starting_state.opening.narration</code></a></span>"
    ) in errors
    warnings = html[html.index('<ul class="report-warnings">') :]
    assert f"entry <code>{CHAR}</code> v2 · role <code>mother</code>" in warnings


def test_each_report_group_renders(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(edit_page(operator_client, engine, fixture("draft_cast")))
    answer_validate(engine, "validate_invalid")
    html = operator_client.post(VALIDATE_URL, as_post(data)).content.decode()
    assert 'class="headline invalid">1 error, 1 warning</span>' in html
    assert "<h4>Errors (1)</h4>" in html
    assert "<code>opening_narration_missing</code>" in html
    # Without JS the report is on its own page: the field links to the editor's anchor.
    assert (
        f'<a href="{EDIT}#f-starting_state-opening-narration">'
        "<code>starting_state.opening.narration</code></a>"
    ) in html
    assert "<h4>Warnings (1)</h4>" in html
    assert "<code>rating_looser</code>" in html
    # The warning is about a role's character: its entry links there, labelled by the role.
    assert f'entry <a href="{CHARACTER_DETAIL}"><code>mother</code></a> v2' in html
    assert UNAVAILABLE in html


def test_validate_under_htmx_on_the_edit_page_anchors_on_the_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(edit_page(operator_client, engine))
    answer_validate(engine, "validate_invalid")
    html = operator_client.post(VALIDATE_URL, as_post(data), headers=HTMX).content.decode()
    assert 'href="#f-starting_state-opening-narration"' in html


def test_a_version_report_is_plain_text(operator_client: Client, engine: FakeEngine) -> None:
    answer_validate(engine, "validate_invalid")
    html = operator_client.post(VALIDATE_URL, {"version": "2"}).content.decode()
    assert "field <code>starting_state.opening.narration</code>" in html
    assert "#f-starting_state" not in html


def test_validate_is_recorded_as_validate_scenario_even_when_invalid(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_validate(engine, "validate_invalid")
    operator_client.post(VALIDATE_URL, {"draft_revision": "5"})
    row = EngineOperation.objects.get(kind="validate_scenario")
    assert (row.outcome, row.entity_type, row.entity_id) == ("ok", "scenario", SCN)
    assert row.idempotency_key == ""
    stored: Any = row.response
    assert stored["report"]["errors"][0]["code"] == "opening_narration_missing"


def test_a_stale_validate_redirects_back_with_the_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    stale = error("draft_stale", "moved", current_draft_revision=7)
    engine.respond("POST", VALIDATE, 409, json=stale)
    response = operator_client.post(VALIDATE_URL, {"draft_revision": "5", "next": EDIT})
    assert response.status_code == 302
    assert response["Location"] == EDIT
    assert flashes(response) == [
        "The draft moved to revision 7 since this page loaded — review and retry"
    ]
    response = operator_client.post(
        VALIDATE_URL, {"draft_revision": "5", "next": EDIT}, headers=HTMX
    )
    assert response["HX-Redirect"] == EDIT


def test_an_unsafe_next_falls_back_to_the_detail_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    stale = error("draft_stale", "moved", current_draft_revision=7)
    engine.respond("POST", VALIDATE, 409, json=stale)
    for unsafe in ("https://evil.example/", "/catalog/scenarios/OTHER/"):
        response = operator_client.post(VALIDATE_URL, {"draft_revision": "5", "next": unsafe})
        assert response["Location"] == DETAIL


def test_the_edit_page_notes_what_validate_checks(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    html = response.content.decode()
    assert "Validates the last saved draft" in html
    assert f'formaction="{VALIDATE_URL}"' in html
    assert f'hx-post="{VALIDATE_URL}"' in html
    data = posted(response)
    data["op"] = "refresh"
    html = operator_client.post(EDIT, as_post(data)).content.decode()
    assert "Unsaved edits are not included — save first" in html


# --- publish --------------------------------------------------------------------------------------


def test_publish_from_the_detail_page_flashes_and_shows_the_warnings(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = detail_page(operator_client, engine).content.decode()
    fields = form_fields(html, "publish-form")
    assert fields["draft_revision"] == "5"
    engine.reset()
    engine.respond("POST", PUBLISH, 201, json=published())
    response = operator_client.post(PUBLISH_URL, fields)
    (sent,) = calls(engine, "POST", f"{PATH}/publish")
    assert body_of(sent) == {"draft_revision": 5}
    assert sent.headers["Idempotency-Key"] == fields["publish_idempotency_key"]
    row = EngineOperation.objects.get(kind="publish_scenario")
    assert (row.entity_type, row.entity_id) == ("scenario", SCN)
    assert response["Location"] == f"{DETAIL}?report={row.pk}"
    assert flashes(response) == ["Published v3 (draft revision 6)"]
    page = detail_page(operator_client, engine, f"?report={row.pk}").content.decode()
    assert "Published v3" in page
    assert "Valid — 1 warning" in page
    assert "<code>rating_looser</code>" in page


def test_publish_invalid_on_the_detail_page_rerenders_it_with_the_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    fields = form_fields(detail_page(operator_client, engine).content.decode(), "publish-form")
    engine.respond("POST", PUBLISH, 422, json=fixture("publish_invalid"))
    response = operator_client.post(PUBLISH_URL, fields)
    assert response.status_code == 200
    html = response.content.decode()
    report = html[html.index('id="report"') :]
    assert "2 errors" in report[:2000]
    # Draft-report locations link to the editor's fields.
    assert f'href="{EDIT}#f-world-description"' in report


def test_the_detail_notes_a_draft_equal_to_the_latest(
    operator_client: Client, engine: FakeEngine
) -> None:
    latest = detail()
    same = {**copy.deepcopy(latest), "version": None, "draft_revision": 5}
    html = detail_page(operator_client, engine, latest=latest, draft=same).content.decode()
    assert "The draft equals v2; publishing makes an identical v3" in html


def test_publish_is_disabled_when_the_draft_read_failed(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        S_ITEM,
        [
            {"status": 200, "json": detail()},
            {"status": 503, "json": error("database_unavailable", "down")},
        ],
    )
    answer_ratings(engine)
    answer_profiles(engine)
    html = operator_client.get(DETAIL).content.decode()
    assert "The draft could not be read: 503 database_unavailable" in html
    assert re.search(r"<button type=\"submit\" disabled>Publish saved draft</button>", html)


def test_validate_and_publish_are_not_on_the_card_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    from tests.catalog.scenario_helpers import card_of

    engine.respond("GET", S_ITEM, 200, json=card_of(detail()))
    answer_ratings(engine)
    html = operator_client.get(f"{DETAIL}?projection=card").content.decode()
    assert "validate-form" not in html
    assert "publish-form" not in html


def test_publish_is_disabled_on_an_archived_scenario(
    operator_client: Client, engine: FakeEngine
) -> None:
    archived = detail(status="archived")
    html = detail_page(
        operator_client, engine, latest=archived, draft=draft(status="archived")
    ).content.decode()
    assert (
        '<button type="button" class="archived-disabled" disabled '
        'title="Archived — unarchive to publish">Publish saved draft</button>'
    ) in html
    assert "publish-form" not in html
    assert "validate-form" in html  # Validate stays available


def test_a_forced_publish_of_an_archived_scenario_shows_the_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", PUBLISH, 422, json=error("entry_archived", "archived"))
    engine.respond("GET", S_ITEM, 200, json=detail(status="archived"))
    answer_ratings(engine)
    answer_profiles(engine)
    response = operator_client.post(PUBLISH_URL, {"draft_revision": "5"})
    html = response.content.decode()
    assert '<p class="refusal errornote" role="alert">Archived — unarchive to publish</p>' in html


def test_a_stale_publish_redirects_back_with_the_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    stale = error("draft_stale", "moved", current_draft_revision=9)
    engine.respond("POST", PUBLISH, 409, json=stale)
    response = operator_client.post(PUBLISH_URL, {"draft_revision": "5", "next": DETAIL})
    assert response["Location"] == DETAIL
    assert flashes(response) == [
        "The draft moved to revision 9 since this page loaded — review and retry"
    ]


def test_save_and_publish_saves_then_publishes_the_saved_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(edit_page(operator_client, engine))
    data["public_card__name"] = "Harvest Moon"
    data["action"] = "publish"
    engine.respond("PATCH", S_ITEM, 200, json=fixture("patch"))
    engine.respond("POST", PUBLISH, 201, json=published())
    response = operator_client.post(EDIT, as_post(data))
    assert [(r.method, r.url.path) for r in writes(engine)] == [
        ("PATCH", PATH),
        ("POST", f"{PATH}/publish"),
    ]
    (sent,) = calls(engine, "POST", f"{PATH}/publish")
    assert body_of(sent) == {"draft_revision": 6}  # the revision the save produced
    assert sent.headers["Idempotency-Key"] == data["publish_idempotency_key"]
    patch = calls(engine, "PATCH", PATH)[0]
    assert patch.headers["Idempotency-Key"] == data["idempotency_key"]
    row = EngineOperation.objects.get(kind="publish_scenario")
    assert response["Location"] == f"{EDIT}?report={row.pk}"


def test_a_failed_save_makes_no_publish_call(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(edit_page(operator_client, engine))
    data["public_card__name"] = "Harvest Moon"
    data["action"] = "publish"
    engine.respond("PATCH", S_ITEM, 503, json=error("database_unavailable", "down"))
    operator_client.post(EDIT, as_post(data))
    assert calls(engine, "POST", f"{PATH}/publish") == []


def test_save_and_publish_with_no_changes_publishes_the_page_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(edit_page(operator_client, engine))
    data["action"] = "publish"
    engine.respond("POST", PUBLISH, 201, json=published())
    operator_client.post(EDIT, as_post(data))
    assert calls(engine, "PATCH", PATH) == []
    (sent,) = calls(engine, "POST", f"{PATH}/publish")
    assert body_of(sent) == {"draft_revision": 5}


def test_publish_invalid_places_errors_at_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(edit_page(operator_client, engine))
    data["public_card__name"] = "Harvest Moon"
    data["action"] = "publish"
    engine.respond("PATCH", S_ITEM, 200, json=fixture("patch"))
    engine.respond("POST", PUBLISH, 422, json=fixture("publish_invalid"))
    response = operator_client.post(EDIT, as_post(data))
    row = EngineOperation.objects.get(kind="publish_scenario")
    assert response["Location"] == f"{EDIT}?report={row.pk}"
    assert "Saved as draft revision 6 — not published" in flashes(response)
    page = operator_client.get(response["Location"])
    form = page.context["editor"].form
    assert form.errors["starting_state__opening__narration"] == [
        "opening_narration_missing: FR-SCN-5: an opening narration is required to publish"
    ]
    assert "world_description_missing" in form.errors["world__description"][0]
    html = page.content.decode()
    report = html[html.index('id="report"') :]
    report = report[: report.index('class="scenario-form"')]  # #report sits above the form
    assert "Draft revision 6" in report
    assert "2 errors" in report


def test_rating_retired_lands_on_the_rating(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(edit_page(operator_client, engine))
    data["public_card__name"] = "Harvest Moon"
    data["action"] = "publish"
    engine.respond("PATCH", S_ITEM, 200, json=fixture("patch"))
    engine.respond("POST", PUBLISH, 422, json=error("rating_retired", "retired"))
    response = operator_client.post(EDIT, as_post(data))
    assert response.status_code == 200
    text_ = text(response.content.decode())
    assert "Saved as draft revision 6 — not published" in text_
    assert response.context["editor"].form.errors["metadata__content_rating"] == [
        "The scenario's content rating is retired — choose another"
    ]


def test_a_stale_save_and_publish_redirects_with_the_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(edit_page(operator_client, engine))
    data["action"] = "publish"
    stale = error("draft_stale", "moved", current_draft_revision=8)
    engine.respond("POST", PUBLISH, 409, json=stale)
    response = operator_client.post(EDIT, as_post(data))
    assert response["Location"] == EDIT
    assert flashes(response) == [
        "The draft moved to revision 8 since this page loaded — review and retry"
    ]


def test_save_and_publish_is_disabled_on_an_archived_scenario(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, draft(status="archived")).content.decode()
    assert (
        '<button type="submit" name="action" value="publish" disabled '
        'title="Archived — unarchive to publish">'
    ) in html
    assert "Validate saved draft</button>" in html


# --- the companion --------------------------------------------------------------------------------


def companion_draft(**changes: Any) -> dict[str, Any]:
    return fixture("companion_draft", **changes)


def companion_page(client: Client, engine: FakeEngine, **changes: Any) -> Any:
    engine.respond("GET", S_ITEM, 200, json=companion_draft(**changes))
    engine.respond("GET", ITEM, 200, json=card())
    answer_ratings(engine)
    answer_profiles(engine)
    response = client.get(COMPANION_EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def companion_patch(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", COMPANION_PATH)
    body: dict[str, Any] = body_of(sent)
    return body


def save_companion(client: Client, engine: FakeEngine, data: QueryDict) -> Any:
    engine.respond("PATCH", S_ITEM, 200, json=fixture("patch"))
    return client.post(COMPANION_EDIT, as_post(data))


def test_the_companion_editor_shows_fixed_slots(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = companion_page(operator_client, engine).content.decode()
    assert html.count("Fixed — follows Sir Aldric") == 2
    assert f"<code>{CHAR}@v2</code>" in html
    assert "Companion of" in html
    assert "— pinned to v2" in html
    assert "Copied from Sir Aldric at creation" in html
    # No role buttons; the role id, kind and fill are hidden inputs, not editable ones.
    assert 'value="add:role"' not in html
    assert 'value="remove:role-0"' not in html
    assert 'type="hidden" name="role-0-fill" value="locked"' in html
    assert 'name="role-0-public_label"' in html  # every other role field stays editable
    assert 'name="cast__max_characters_at_start"' in html


def test_a_companion_never_pinned_says_so(operator_client: Client, engine: FakeEngine) -> None:
    html = companion_page(operator_client, engine, pinned_character_version=None).content.decode()
    assert "not pinned until Sir Aldric is published" in html


def test_a_tampered_companion_post_keeps_the_slots(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(companion_page(operator_client, engine))
    data["role-0-fill"] = "open"
    data["role-0-character__catalog_id"] = "01JC00000000000000000CHAR2"
    data["role-1-kind"] = "npc"
    data["role-1-public_label"] = "The visitor"  # a real edit, so the cast is sent
    save_companion(operator_client, engine, data)
    roles = companion_patch(engine)["cast"]["roles"]
    assert [(r["id"], r["kind"], r["fill"], r["character"]) for r in roles] == [
        ("npc", "npc", "locked", {"catalog_id": CHAR, "version": 2}),
        ("player", "player", "open", None),
    ]
    assert roles[1]["public_label"] == "The visitor"


def test_editing_a_companion_seed_sends_the_cast(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(companion_page(operator_client, engine))
    data["role-0-seed-0-text"] = "She keeps three hives."
    data["world__description"] = "A valley farm."
    save_companion(operator_client, engine, data)
    patch = companion_patch(engine)
    assert patch["world"] == {"description": "A valley farm."}
    npc = patch["cast"]["roles"][0]
    assert npc["seeded_memories"][0]["text"] == "She keeps three hives."
    assert npc["character"] == {"catalog_id": CHAR, "version": 2}


def test_no_cast_edit_sends_no_cast(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(companion_page(operator_client, engine))
    data["world__description"] = "A valley farm."
    save_companion(operator_client, engine, data)
    assert "cast" not in companion_patch(engine)


def test_a_companion_role_button_is_ignored(operator_client: Client, engine: FakeEngine) -> None:
    data = posted(companion_page(operator_client, engine))
    data["op"] = "remove:role-1"
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    assert [f.raw("id") for f in response.context["editor"].cast.roles.forms] == ["npc", "player"]
    data["op"] = "add:role"
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    assert len(response.context["editor"].cast.roles.forms) == 2
    assert writes(engine) == []


def test_companion_slots_fixed_is_placed_at_the_role(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(companion_page(operator_client, engine))
    data["role-0-public_label"] = "Ada the beekeeper"
    engine.respond("PATCH", S_ITEM, 422, json=fixture("companion_slots_fixed"))
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    npc = response.context["editor"].cast.roles.forms[0]
    assert npc.non_field_errors() == [
        "cast.roles[0].fill: a companion scenario keeps its two slots"
    ]
    # A role the form doesn't have: the top of Cast.
    unknown = fixture("companion_slots_fixed")
    unknown["error"]["details"]["role"] = "ghost"
    engine.respond("PATCH", S_ITEM, 422, json=unknown)
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    assert response.context["editor"].cast.cast_errors == [
        "cast.roles[0].fill: a companion scenario keeps its two slots"
    ]


def test_publishing_a_companion_before_its_character_says_publish_it_first(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(companion_page(operator_client, engine))
    data["action"] = "publish"
    engine.respond("POST", PUBLISH, 422, json=fixture("character_not_published"))
    html = operator_client.post(COMPANION_EDIT, as_post(data)).content.decode()
    assert f'Publish <a href="/catalog/characters/{CHAR}/edit/">Sir Aldric</a> first' in html


def test_a_companion_report_naming_character_not_published_says_publish_it_first(
    operator_client: Client, engine: FakeEngine
) -> None:
    """The Engine reports it inside ``publish_invalid`` (its report's error code)."""
    data = posted(companion_page(operator_client, engine))
    data["action"] = "publish"
    refused = fixture("publish_invalid")
    entry = refused["error"]["details"]["report"]["errors"][0]
    entry["code"] = "character_not_published"
    entry["location"]["entry"] = COMPANION_SCN
    engine.respond("POST", PUBLISH, 422, json=refused)
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    engine.respond("GET", S_ITEM, 200, json=companion_draft())
    engine.respond("GET", ITEM, 200, json=card())
    html = operator_client.get(response["Location"]).content.decode()
    assert f'Publish <a href="/catalog/characters/{CHAR}/edit/">Sir Aldric</a> first' in html


def test_publishing_a_companion_flashes_its_pin(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(companion_page(operator_client, engine))
    data["action"] = "publish"
    engine.respond("POST", PUBLISH, 201, json=published(id=COMPANION_SCN))
    engine.respond("GET", S_ITEM, 200, json=companion_draft(pinned_character_version=4))
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    assert flashes(response) == ["Published v3 (draft revision 6); pinned to Sir Aldric v4"]
    (latest,) = calls(engine, "GET", COMPANION_PATH)
    assert dict(latest.url.params) == {"projection": "full"}  # the latest, not the draft


def test_the_pin_is_dropped_when_the_reread_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = posted(companion_page(operator_client, engine))
    data["action"] = "publish"
    engine.respond("POST", PUBLISH, 201, json=published(id=COMPANION_SCN))
    engine.respond("GET", S_ITEM, 503, json=error("database_unavailable", "down"))
    response = operator_client.post(COMPANION_EDIT, as_post(data))
    assert flashes(response) == ["Published v3 (draft revision 6)"]


# --- the character's companion link ---------------------------------------------------------------


def character_detail(client: Client, engine: FakeEngine, query: str = "") -> str:
    from tests.catalog.helpers import draft as character_draft
    from tests.catalog.helpers import full

    engine.respond_sequence(
        "GET",
        ITEM,
        [{"status": 200, "json": full()}, {"status": 200, "json": character_draft()}],
    )
    answer_ratings(engine)
    response = client.get(f"{CHARACTER_DETAIL}{query}")
    assert response.status_code == 200
    return response.content.decode()


def test_the_character_detail_links_its_companion(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = character_detail(operator_client, engine)
    assert "Companion scenario" in html
    assert f'<a class="companion-link" href="/catalog/scenarios/{COMPANION_SCN}/">' in html
    assert f'href="/catalog/scenarios/{COMPANION_SCN}/edit/">Edit companion</a>' in html
    cards = [
        r
        for r in calls(engine, "GET", f"/catalog/characters/{CHAR}/companion")
        if r.url.params.get("projection") == "card"
    ]
    assert len(cards) == 1
    assert "draft" not in cards[0].url.params


def test_the_companion_link_falls_back_and_reports_a_failed_read(
    operator_client: Client, engine: FakeEngine
) -> None:
    unnamed = {**engine_fixture("catalog_characters_companion_card"), "name": None}
    engine.respond("GET", COMPANION, 200, json=unnamed)
    html = character_detail(operator_client, engine)
    assert f'href="/catalog/scenarios/{COMPANION_SCN}/">Companion scenario</a>' in html
    engine.respond("GET", COMPANION, 503, json=error("database_unavailable", "down"))
    html = character_detail(operator_client, engine)
    assert "Companion unavailable: 503 database_unavailable" in html


def test_no_companion_says_so(operator_client: Client, engine: FakeEngine) -> None:
    from tests.catalog.helpers import full

    engine.respond("GET", ITEM, 200, json=full(companion_id=None))
    answer_ratings(engine)
    html = operator_client.get(f"{CHARACTER_DETAIL}?version=1").content.decode()
    assert "No companion" in html
    assert [
        r
        for r in calls(engine, "GET", f"/catalog/characters/{CHAR}/companion")
        if r.url.params.get("projection") == "card"
    ] == []


def test_edit_companion_is_disabled_when_the_companion_is_archived(
    operator_client: Client, engine: FakeEngine
) -> None:
    archived = {**engine_fixture("catalog_characters_companion_card"), "status": "archived"}
    engine.respond("GET", COMPANION, 200, json=archived)
    html = character_detail(operator_client, engine)
    assert "Edit companion</button>" in html
    assert "(Archived — unarchive to edit)" in html
    assert '/edit/">Edit companion</a>' not in html


def test_edit_companion_is_not_offered_on_an_older_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    from tests.catalog.helpers import full

    engine.respond("GET", ITEM, 200, json=full())
    answer_ratings(engine)
    html = operator_client.get(f"{CHARACTER_DETAIL}?version=1").content.decode()
    assert "companion-link" in html
    assert "Edit companion" not in html


def test_the_companion_draft_fixture_reads_as_a_companion() -> None:
    body = json.loads(json.dumps(companion_draft()))
    assert body["companion_of"] == CHAR
    assert len(body["cast"]["roles"]) == 2
