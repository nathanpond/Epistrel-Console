"""Publish a catalog character, with or without its companion's draft (#66): Save & publish on the
edit page, the detail page's plain Publish, the refusals and the reports."""

import copy
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client

from console.models import EngineOperation
from tests.catalog.helpers import (
    CHAR,
    COMPANION,
    DETAIL,
    EDIT,
    ITEM,
    PUBLISH,
    PUBLISH_URL,
    SCENARIO,
    answer_edit_reads,
    answer_ratings,
    body_of,
    calls,
    companion,
    draft,
    error,
    form_data,
    full,
    patched,
    published,
    writes,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

PUBLISH_PATH = PUBLISH.format(character_id=CHAR)
WARMTH_NUANCE = "definition__dispositions__warmth__nuance"


def edit_page(client: Client, engine: FakeEngine, **kwargs: Any) -> Any:
    answer_edit_reads(engine, **kwargs)
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def publish_sent(engine: FakeEngine) -> tuple[dict[str, str], dict[str, Any]]:
    (sent,) = calls(engine, "POST", PUBLISH_PATH)
    return dict(sent.url.params), body_of(sent)


def invalid(entity: str = "character") -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("catalog_characters_publish_invalid"))
    body["error"]["details"]["entity"] = entity
    return body


def report_entry(code: str, field: str, outcome: str = "kept", entry: str = SCENARIO) -> Any:
    return {
        "code": code,
        "location": {"entry": entry, "version": "draft", "field": field},
        "outcome": outcome,
        "rule": None,
        "source": None,
        "text": None,
    }


# --- Save & publish -------------------------------------------------------------------------------


def test_publish_without_the_companion(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("POST", PUBLISH, 201, json=published())
    done = operator_client.post(EDIT, {**form_data(response), "action": "publish"})
    assert done.status_code == 302
    assert done["Location"] == DETAIL
    params, body = publish_sent(engine)
    assert params == {}
    assert body == {"draft_revision": 5}
    assert [r.method for r in engine.requests] == ["POST"]  # no change: no PATCH
    (sent,) = writes(engine)
    assert sent.headers["Idempotency-Key"] == form_data(response)["publish_idempotency_key"]
    row = EngineOperation.objects.get(kind="publish_catalog_character")
    assert (row.entity_type, row.entity_id) == ("character", CHAR)
    flash = flashes(done)
    assert flash == ["Published v3 (companion v2, previous content re-pinned)"]


def test_publish_with_the_companion(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("POST", PUBLISH, 201, json=published())
    data = {**form_data(response), "action": "publish", "with_companion": "on"}
    done = operator_client.post(EDIT, data)
    assert done.status_code == 302
    params, body = publish_sent(engine)
    assert params == {"with_companion": "true"}
    assert body == {"draft_revision": 5, "companion_draft_revision": 3}
    flash = flashes(done)
    assert flash == ["Published v3 (companion v2, its draft published)"]


def test_the_box_is_disabled_when_the_companion_read_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=draft())
    engine.respond("GET", COMPANION, 503, json=error("database_unavailable", "down"))
    answer_ratings(engine)
    response = operator_client.get(EDIT)
    html = response.content.decode()
    assert 'name="with_companion" disabled' in html
    assert "Companion draft unavailable: 503 database_unavailable" in html
    assert 'name="companion_draft_revision" value=""' in html
    # A ticked publish without a companion revision cannot happen: zero calls.
    engine.reset()
    data = {**form_data(response), "action": "publish", "with_companion": "on"}
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 200
    assert "companion&#x27;s draft revision is unknown" in refused.content.decode()
    assert engine.requests == []


def test_save_and_publish_patches_then_publishes_the_saved_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched(draft_revision=6))
    engine.respond("GET", COMPANION, 200, json=companion(4))  # the save moved it
    engine.respond("POST", PUBLISH, 201, json=published())
    data = {
        **form_data(response),
        WARMTH_NUANCE: "to friends",
        "action": "publish",
        "with_companion": "on",
    }
    done = operator_client.post(EDIT, data)
    assert done.status_code == 302
    assert done["Location"] == DETAIL
    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("PATCH", ITEM.format(character_id=CHAR)),
        ("GET", COMPANION.format(character_id=CHAR)),
        ("POST", PUBLISH_PATH),
    ]
    patch, publish = writes(engine)
    assert body_of(patch)["draft_revision"] == 5
    assert body_of(publish) == {"draft_revision": 6, "companion_draft_revision": 4}
    assert patch.headers["Idempotency-Key"] != publish.headers["Idempotency-Key"]


def test_a_refused_publish_leaves_the_save_and_lands_on_the_edit_page_with_the_report(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched(draft_revision=6))
    engine.respond("POST", PUBLISH, 422, json=invalid())
    data = {**form_data(response), WARMTH_NUANCE: "to friends", "action": "publish"}
    done = operator_client.post(EDIT, data)
    assert [r.method for r in engine.requests] == ["PATCH", "POST"]
    row = EngineOperation.objects.get(kind="publish_catalog_character")
    assert done.status_code == 302
    assert done["Location"] == f"{EDIT}?report={row.pk}"
    flash = flashes(done)
    assert flash == ["Saved as draft revision 6, but not published."]
    answer_edit_reads(engine, draft(draft_revision=6))
    page = operator_client.get(done["Location"])
    html = page.content.decode()
    assert "Character: validation report" in html
    assert "true_age_missing" in html


def test_a_failed_save_stops_before_publishing(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 422, json=error("validation_error", "bad", field="body.x"))
    data = {**form_data(response), WARMTH_NUANCE: "x", "action": "publish", "with_companion": "on"}
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 200
    assert [r.method for r in engine.requests] == ["PATCH"]


# --- publish_invalid ------------------------------------------------------------------------------


def test_publish_invalid_places_errors_beside_their_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("POST", PUBLISH, 422, json=invalid())
    done = operator_client.post(EDIT, {**form_data(response), "action": "publish"})
    answer_edit_reads(engine)
    page = operator_client.get(done["Location"])
    form = page.context["form"]
    notes = form.report_notes
    assert notes["definition__true_age"] == [
        "true_age_missing: FR-CAT-12: a true age is required to publish"
    ]
    assert notes["metadata__content_rating"] == [
        "content_rating_missing: FR-RATE-3: a content rating is required to publish"
    ]
    assert notes["definition__dispositions__caution__setting"] == [
        "field_required: required to publish"
    ]
    # An unmatched path (the images are #71's) is in the report only.
    assert all("primary_image_required" not in n for ns in notes.values() for n in ns)
    html = page.content.decode()
    assert "primary_image_required" in html
    assert 'href="#f-definition-true_age"' in html
    assert 'id="f-definition-true_age"' in html
    assert '<span class="report-note error">true_age_missing' in html
    assert "fact_consistency" in html  # unavailable checks shown


def test_companion_errors_are_labelled_and_not_placed_on_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("POST", PUBLISH, 422, json=invalid("companion"))
    done = operator_client.post(EDIT, {**form_data(response), "action": "publish"})
    answer_edit_reads(engine)
    page = operator_client.get(done["Location"])
    html = page.content.decode()
    assert "Companion: validation report" in html
    assert "true_age_missing" in html
    # complement: companion-entity errors are not placed on fields
    assert page.context["form"].report_notes == {}
    assert 'class="report-note' not in html


def test_a_report_for_another_entity_is_ignored(
    operator_client: Client, engine: FakeEngine
) -> None:
    row = EngineOperation.objects.create(
        kind="publish_catalog_character",
        http_method="POST",
        path="/x",
        entity_type="character",
        entity_id="01JC00000000000000000OTHER",
        response=invalid(),
    )
    answer_edit_reads(engine)
    page = operator_client.get(f"{EDIT}?report={row.pk}")
    assert "validation report" not in page.content.decode()


# --- warnings after a publish ---------------------------------------------------------------------


def test_rating_looser_from_the_companion_report_is_shown(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    body = published()
    body["companion"]["report"]["warnings"] = [
        report_entry("rating_looser", "metadata.content_rating_id")
    ]
    engine.respond("POST", PUBLISH, 201, json=body)
    done = operator_client.post(EDIT, {**form_data(response), "action": "publish"})
    row = EngineOperation.objects.get(kind="publish_catalog_character")
    assert done["Location"] == f"{DETAIL}?report={row.pk}"
    engine.respond("GET", ITEM, 200, json=full())
    engine.respond("GET", COMPANION, 200, json=companion())
    html = operator_client.get(done["Location"]).content.decode()
    assert "Companion: validation report" in html
    assert "rating_looser" in html
    assert "Character: validation report" not in html  # no findings of its own


def test_no_findings_no_report(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("POST", PUBLISH, 201, json=published())
    done = operator_client.post(EDIT, {**form_data(response), "action": "publish"})
    assert done["Location"] == DETAIL


# --- 409 ------------------------------------------------------------------------------------------


def test_a_409_on_publish_keeps_the_checkbox(operator_client: Client, engine: FakeEngine) -> None:
    response = edit_page(operator_client, engine)
    stale = error(
        "draft_stale",
        "stale",
        entity="companion",
        scenario_id=SCENARIO,
        draft_revision=3,
        current_draft_revision=5,
    )
    engine.respond("POST", PUBLISH, 409, json=stale)
    data = {**form_data(response), "action": "publish", "with_companion": "on"}
    refused = operator_client.post(EDIT, data)
    assert refused.status_code == 200
    html = refused.content.decode()
    assert "The companion&#x27;s draft was saved elsewhere (now revision 5)" in html
    assert 'name="with_companion" checked' in html


def test_a_409_after_a_save_redirects_with_the_banner(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = edit_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched(draft_revision=6))
    engine.respond("GET", COMPANION, 200, json=companion(4))
    stale = error(
        "draft_stale",
        "stale",
        entity="character",
        character_id=CHAR,
        draft_revision=6,
        current_draft_revision=7,
    )
    engine.respond("POST", PUBLISH, 409, json=stale)
    data = {
        **form_data(response),
        WARMTH_NUANCE: "x",
        "action": "publish",
        "with_companion": "on",
    }
    done = operator_client.post(EDIT, data)
    assert done["Location"] == f"{EDIT}?companion=1"
    flash = flashes(done)
    assert flash[0] == "Saved as draft revision 6, but not published."
    assert "now revision 7" in flash[1]
    answer_edit_reads(engine)
    page = operator_client.get(done["Location"]).content.decode()
    assert 'name="with_companion" checked' in page


# --- the detail page's Publish --------------------------------------------------------------------


def test_the_detail_page_offers_publish_and_edit(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        ITEM,
        [{"status": 200, "json": full()}, {"status": 200, "json": draft()}],
    )
    engine.respond("GET", COMPANION, 200, json=companion())
    html = operator_client.get(DETAIL).content.decode()
    assert f'href="{EDIT}"' in html
    assert f'action="{PUBLISH_URL}"' in html
    # The latest view reads the draft (its revision is what Publish names) and the companion's
    # draft (#74 adds the companion's card, for its link).
    assert len(calls(engine, "GET", ITEM.format(character_id=CHAR))) == 2
    assert len(companion_draft_reads(engine)) == 1


def test_the_detail_publish_names_the_draft_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        ITEM,
        [{"status": 200, "json": full()}, {"status": 200, "json": draft()}],
    )
    engine.respond("GET", COMPANION, 200, json=companion())
    html = operator_client.get(DETAIL).content.decode()
    assert 'name="draft_revision" value="5"' in html
    assert 'name="companion_draft_revision" value="3"' in html
    engine.reset()
    engine.respond("POST", PUBLISH, 201, json=published())
    key = "01JC0000000000000000PUBKEY"
    done = operator_client.post(
        PUBLISH_URL,
        {
            "draft_revision": "5",
            "companion_draft_revision": "3",
            "with_companion": "on",
            "publish_idempotency_key": key,
        },
    )
    assert done["Location"] == DETAIL
    params, body = publish_sent(engine)
    assert params == {"with_companion": "true"}
    assert body == {"draft_revision": 5, "companion_draft_revision": 3}
    (sent,) = writes(engine)
    assert sent.headers["Idempotency-Key"] == key


def test_the_detail_publish_without_the_box(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("POST", PUBLISH, 201, json=published())
    data = {"draft_revision": "5", "companion_draft_revision": "3"}
    operator_client.post(PUBLISH_URL, data)
    params, body = publish_sent(engine)
    assert params == {}
    assert body == {"draft_revision": 5}


def test_detail_publish_is_disabled_when_the_draft_read_fails(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        ITEM,
        [
            {"status": 200, "json": full()},
            {"status": 503, "json": error("database_unavailable", "down")},
        ],
    )
    html = operator_client.get(DETAIL).content.decode()
    assert "The draft could not be read: 503 database_unavailable" in html
    assert re_disabled(html)
    assert companion_draft_reads(engine) == []


def re_disabled(html: str) -> bool:
    return '<button type="submit" disabled>Publish</button>' in html


def test_detail_publish_box_disabled_without_a_companion(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        ITEM,
        [{"status": 200, "json": full()}, {"status": 200, "json": draft()}],
    )
    engine.respond("GET", COMPANION, 404, json=error("not_found", "gone"))
    html = operator_client.get(DETAIL).content.decode()
    assert 'name="with_companion" disabled' in html
    assert "Companion draft unavailable: 404 not_found" in html


def test_detail_publish_is_disabled_for_an_archived_entry(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=draft(status="archived"))
    html = operator_client.get(f"{DETAIL}?view=draft").content.decode()
    assert "Archived — unarchive to publish." in html
    assert re_disabled(html)


def test_detail_publish_invalid_redirects_to_the_edit_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("POST", PUBLISH, 422, json=invalid())
    done = operator_client.post(PUBLISH_URL, {"draft_revision": "5"})
    row = EngineOperation.objects.get(kind="publish_catalog_character")
    assert done["Location"] == f"{EDIT}?report={row.pk}"


def test_detail_publish_409_flashes_the_banner(operator_client: Client, engine: FakeEngine) -> None:
    stale = error(
        "draft_stale",
        "stale",
        entity="character",
        character_id=CHAR,
        draft_revision=5,
        current_draft_revision=9,
    )
    engine.respond("POST", PUBLISH, 409, json=stale)
    done = operator_client.post(PUBLISH_URL, {"draft_revision": "5"})
    assert done["Location"] == DETAIL
    flash = flashes(done)
    assert flash == [
        "This draft was saved elsewhere (now revision 9) — reload the page to continue from "
        "the latest; nothing was changed."
    ]


def test_detail_publish_without_a_revision_makes_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    done = operator_client.post(PUBLISH_URL, {"with_companion": "on", "draft_revision": "5"})
    assert done["Location"] == DETAIL
    assert engine.requests == []
    assert operator_client.get(PUBLISH_URL).status_code == 405


def test_the_publish_timeout_is_the_catalog_publish_one() -> None:
    from console.engine import timeouts
    from console.engine.client import EngineClient

    seen: dict[str, Any] = {}

    class Spy(EngineClient):
        def _request(self, method: str, path: str, **kwargs: Any) -> Any:
            seen.update(kwargs)
            raise AssertionError("stop")

    with pytest.raises(AssertionError):
        Spy("http://engine.test", None, principal="p").publish_catalog_character(
            CHAR, draft_revision=1, idempotency_key="k"
        )
    assert seen["timeout"] == timeouts.CATALOG_PUBLISH


def test_stale_banner_names_the_stale_side() -> None:
    from console.draft_stale import stale_banner
    from console.engine.client import EngineError

    def stale(**details: Any) -> EngineError:
        return EngineError("draft_stale", "stale", details, 409)

    moved = stale(entity="companion", current_draft_revision=None)
    assert stale_banner(moved, draft_revision=5).startswith("The companion changed")
    # Without ``entity`` (a save's 409), the revision that differs decides.
    by_revision = stale(current_draft_revision=5)
    banner = stale_banner(by_revision, draft_revision=5, companion_draft_revision=3)
    assert banner.startswith("The companion's draft was saved elsewhere (now revision 5)")
    assert stale_banner(stale(current_draft_revision=8), draft_revision=5).startswith(
        "This draft was saved elsewhere (now revision 8)"
    )


def companion_draft_reads(engine: FakeEngine) -> list[Any]:
    """The companion reads in ``full`` (#66's Publish), not #74's card read for the link."""
    reads = calls(engine, "GET", COMPANION.format(character_id=CHAR))
    return [r for r in reads if r.url.params.get("projection") == "full"]
