"""Attach images and videos to characters and scenarios (#71): the editors' media sections, the
picker and slot-row partials, refusals placed at their slot, and the detail pages."""

from typing import Any

import pytest
from django.http import QueryDict
from django.test import Client

from console.media_slots import TIMES
from console.models import EngineOperation
from tests.catalog import scenario_helpers as sh
from tests.catalog.helpers import (
    CHAR,
    DETAIL,
    EDIT,
    ITEM,
    LIST,
    MED1,
    MED2,
    MED3,
    MED4,
    NEW_URL,
    PUBLISH,
    answer_edit_reads,
    answer_ratings,
    body_of,
    calls,
    draft,
    form_data,
    full,
    patched,
    text,
    writes,
)
from tests.catalog.media_helpers import (
    GNE1,
    MEDIA_LIST,
    PHT1,
    PHT2,
    PST1,
    VDE1,
    answer_media,
    asset,
    media,
    media_reads,
    sections_posted,
    set_rows,
)
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

GALLERY = "public_card__images__gallery"
REFS = "visual_identity__reference_images"
PRIMARY = "public_card__images__primary"
COVER = "public_card__media__cover"
S_GALLERY = "public_card__media__gallery"
VIDEOS = "public_card__media__videos"


def ids(n: int, letter: str = "A") -> list[str]:
    """``n`` distinct valid media ids."""
    return [f"01JC0000000000000000{letter}{i:05d}" for i in range(n)]


# --- the character editor -------------------------------------------------------------------------


def character_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_edit_reads(engine, body)
    response = client.get(EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def character_post(response: Any, **changes: Any) -> QueryDict:
    """What the edit page posts back unchanged (the form and its media sections), plus
    ``changes``."""
    data = sections_posted(response)
    for name, value in form_data(response).items():
        data[name] = value
    data["action"] = "save"
    for name, value in changes.items():
        data[name] = value
    return data


def post(client: Client, url: str, data: QueryDict) -> Any:
    return client.post(url, dict(data.lists()))


def patch_sent(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", ITEM.format(character_id=CHAR))
    body: dict[str, Any] = body_of(sent)
    return body


def test_the_edit_page_shows_each_slot_with_its_looked_up_metadata(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_media(engine, {MED1: asset(MED1, width=512, height=768, alt_text="Sir Aldric")})
    answer_edit_reads(engine)
    response = operator_client.get(EDIT)
    html = response.content.decode()
    # One de-duplicated lookup per attached id.
    assert sorted(media_reads(engine)) == sorted([MED1, MED2, MED3, MED4])
    assert "<legend>Images</legend>" in html
    assert "<legend>Reference images</legend>" in html
    assert f'name="{PRIMARY}" id="f-public_card-images-primary-id" value="{MED1}"' in html
    assert (
        f'name="{GALLERY}-0-media_id" id="f-public_card-images-gallery-0--id" value="{MED2}"'
        in (html)
    )
    assert f'src="/media/{MED1}/content/?img=1" width="80" alt="Sir Aldric"' in html
    assert f"image/png · 512 {TIMES} 768 px" in html
    # The reference rows' audiences as stored, and one blank row per list.
    assert f'name="{REFS}-0-audience"' in html
    assert '<option value="perceivable" selected>perceivable</option>' in html
    assert '<option value="restricted" selected>restricted</option>' in html
    assert f'name="{GALLERY}-TOTAL_FORMS" value="2"' in html
    assert f'name="{REFS}-TOTAL_FORMS" value="3"' in html
    assert "Choose…" in html
    assert 'href="/media/upload/?kind=image" target="_blank"' in html


def test_an_untouched_page_sends_no_media(operator_client: Client, engine: FakeEngine) -> None:
    response = character_page(operator_client, engine)
    done = post(operator_client, EDIT, character_post(response))
    assert done.status_code == 302
    assert writes(engine) == []  # "No changes"


def test_the_patch_carries_each_changed_section_exactly_as_picked(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = character_post(response)
    data[PRIMARY] = f" {PHT1.lower()} "  # typed: trimmed and upper-cased
    data = set_rows(data, GALLERY, [{"media_id": PHT2}, {"media_id": PHT1}])
    data = set_rows(
        data,
        REFS,
        [
            {"media_id": MED4, "audience": "restricted"},
            {"media_id": PHT2, "audience": "perceivable"},
        ],
    )
    done = post(operator_client, EDIT, data)
    assert done.status_code == 302
    assert patch_sent(engine) == {
        "draft_revision": 5,
        "public_card": {"images": {"primary": PHT1, "gallery": [PHT2, PHT1]}},
        "visual_identity": {
            "reference_images": [
                {"media_id": MED4, "audience": "restricted"},
                {"media_id": PHT2, "audience": "perceivable"},
            ]
        },
    }


def test_an_unchanged_images_section_is_absent_from_the_patch(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = character_post(response, public_card__name="Sir Aldric the Bold")
    post(operator_client, EDIT, data)
    sent = patch_sent(engine)
    assert sent["public_card"] == {"name": "Sir Aldric the Bold"}
    assert "visual_identity" not in sent


def test_reordering_the_gallery_sends_it_whole(operator_client: Client, engine: FakeEngine) -> None:
    body = draft()
    body["images"]["gallery"].append({**body["images"]["gallery"][0], "media_id": PHT1})
    response = character_page(operator_client, engine, body)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = character_post(response)
    data[f"{GALLERY}-0-ORDER"], data[f"{GALLERY}-1-ORDER"] = "2", "1"
    post(operator_client, EDIT, data)
    assert patch_sent(engine)["public_card"] == {
        "images": {"primary": MED1, "gallery": [PHT1, MED2]}
    }


def test_removing_the_primary_sends_null(operator_client: Client, engine: FakeEngine) -> None:
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = character_post(response)
    data[PRIMARY] = ""
    data[f"{GALLERY}-0-DELETE"] = "on"
    post(operator_client, EDIT, data)
    assert patch_sent(engine)["public_card"] == {"images": {"primary": None, "gallery": []}}


def test_the_remove_button_empties_the_primary_without_a_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    data = character_post(response, op=f"remove:{PRIMARY}")
    page = post(operator_client, EDIT, data)
    assert page.status_code == 200
    assert engine.requests == []
    assert page.context["media"]["public_card_images"].single.media_id == ""
    # Then Save sends the removal.
    engine.respond("PATCH", ITEM, 200, json=patched())
    resubmit = character_post(page)
    post(operator_client, EDIT, resubmit)
    assert patch_sent(engine)["public_card"]["images"]["primary"] is None


def test_an_unset_audience_is_refused_with_no_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    data = set_rows(character_post(response), REFS, [{"media_id": MED3}])
    refused = post(operator_client, EDIT, data)
    assert refused.status_code == 200
    assert engine.requests == []  # complement: zero calls, the lookups ride in the snapshot
    (row, _blank) = refused.context["media"]["visual_identity_reference_images"].lists[0].rows
    assert row.errors == ("Choose perceivable or restricted",)
    html = refused.content.decode()
    assert '<option value="" selected>— choose —</option>' in html


def test_a_stored_reference_without_audience_does_not_block_unrelated_saves(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = draft()
    body["visual_identity"]["reference_images"][0]["audience"] = None
    response = character_page(operator_client, engine, body)
    html = response.content.decode()
    assert '<option value="" selected>— choose —</option>' in html
    engine.respond("PATCH", ITEM, 200, json=patched())
    post(operator_client, EDIT, character_post(response, public_card__name="Renamed"))
    assert patch_sent(engine) == {"draft_revision": 5, "public_card": {"name": "Renamed"}}


def test_clear_visual_identity_wins_over_the_reference_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 200, json=patched())
    data = set_rows(character_post(response), REFS, [{"media_id": MED3}])  # no audience
    data["clear_visual_identity"] = "on"
    post(operator_client, EDIT, data)
    assert patch_sent(engine) == {"draft_revision": 5, "visual_identity": None}
    assert "Also removes the reference images" in response.content.decode()


def test_the_gallery_and_reference_limits_are_enforced_before_any_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    data = character_post(response)
    data = set_rows(data, GALLERY, [{"media_id": m} for m in ids(21)])
    data = set_rows(data, REFS, [{"media_id": m, "audience": "perceivable"} for m in ids(11, "B")])
    refused = post(operator_client, EDIT, data)
    assert engine.requests == []
    sections = refused.context["media"]
    assert sections["public_card_images"].lists[0].errors == ("At most 20 gallery images",)
    assert sections["visual_identity_reference_images"].lists[0].errors == (
        "At most 10 reference images",
    )
    # At the limit itself: accepted, and "Add another" is hidden.
    engine.respond("PATCH", ITEM, 200, json=patched())
    ok = set_rows(character_post(response), GALLERY, [{"media_id": m} for m in ids(20)])
    post(operator_client, EDIT, ok)
    assert len(patch_sent(engine)["public_card"]["images"]["gallery"]) == 20


def test_duplicates_in_the_gallery_are_refused_but_the_primary_may_repeat(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    data = set_rows(
        character_post(response),
        GALLERY,
        [{"media_id": MED1}, {"media_id": PHT1}, {"media_id": PHT1}],
    )
    refused = post(operator_client, EDIT, data)
    assert engine.requests == []
    rows = refused.context["media"]["public_card_images"].lists[0].rows
    assert rows[0].errors == ()  # the primary (MED1) may also be in the gallery
    assert rows[2].errors == ("Already in this list (row 2)",)


def test_a_bad_id_and_a_row_without_id_are_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    data = character_post(response)
    data[PRIMARY] = "not-an-id"
    data = set_rows(data, REFS, [{"audience": "restricted"}])
    refused = post(operator_client, EDIT, data)
    assert engine.requests == []
    shown = refused.context["media"]
    assert shown["public_card_images"].single.errors == ("Not a media id (26-character ULID)",)
    row = shown["visual_identity_reference_images"].lists[0].rows[0]
    assert row.errors == ("Choose a media asset or remove this row",)


def test_add_another_and_move_up_are_non_saving(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    added = post(operator_client, EDIT, character_post(response, op=f"add:{GALLERY}"))
    assert added.status_code == 200
    assert engine.requests == []
    assert len(added.context["media"]["public_card_images"].lists[0].rows) == 3
    data = set_rows(character_post(response), GALLERY, [{"media_id": MED2}, {"media_id": PHT1}])
    data["op"] = f"up:{GALLERY}-1"
    moved = post(operator_client, EDIT, data)
    assert engine.requests == []
    rows = moved.context["media"]["public_card_images"].lists[0].rows
    assert [r.media_id for r in rows[:2]] == [PHT1, MED2]
    assert [r.order for r in rows[:2]] == ["1", "2"]


def test_a_validation_error_lands_at_the_sent_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 422, json=media("error_validation_gallery"))
    # A removed first row: the Engine's index 1 is the third rendered row.
    data = set_rows(
        character_post(response),
        GALLERY,
        [{"media_id": PHT1, "DELETE": "on"}, {"media_id": MED2}, {"media_id": PHT2}],
    )
    data = set_rows(data, REFS, [{"media_id": MED3, "audience": "perceivable"}])
    refused = post(operator_client, EDIT, data)
    shown = refused.context["media"]
    rows = shown["public_card_images"].lists[0].rows
    assert rows[2].errors == (
        "String should match pattern '^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}$'",
    )
    assert rows[1].errors == ()
    ref = shown["visual_identity_reference_images"].lists[0].rows[0]
    assert ref.errors == ("Field required",)
    assert refused.context["form"].non_field_errors() == []


def test_media_retired_is_placed_at_its_slot(operator_client: Client, engine: FakeEngine) -> None:
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 422, json=media("error_media_retired"))
    data = set_rows(character_post(response), GALLERY, [{"media_id": MED2}, {"media_id": PHT2}])
    refused = post(operator_client, EDIT, data)
    rows = refused.context["media"]["public_card_images"].lists[0].rows
    assert rows[1].errors == ("This asset is retired — choose another",)
    assert "This asset is retired — choose another" in refused.content.decode()


def test_an_unknown_media_id_is_not_called_retired(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#164: the Engine answers an unknown id as ``media_retired`` with ``reason: unknown``."""
    unknown = media("error_media_retired")
    unknown["error"]["details"]["reason"] = "unknown"
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 422, json=unknown)
    data = set_rows(character_post(response), GALLERY, [{"media_id": MED2}, {"media_id": PHT2}])
    refused = post(operator_client, EDIT, data)
    rows = refused.context["media"]["public_card_images"].lists[0].rows
    assert rows[1].errors == ("No such asset — choose another",)
    assert "This asset is retired" not in refused.content.decode()


def test_a_pathless_unknown_media_id_marks_its_slots_as_unknown(
    operator_client: Client, engine: FakeEngine
) -> None:
    unknown = media("error_media_retired")
    unknown["error"]["details"] = {"media_id": PHT2, "reason": "unknown"}
    response = character_page(operator_client, engine)
    engine.respond("PATCH", ITEM, 422, json=unknown)
    data = set_rows(character_post(response), GALLERY, [{"media_id": MED2}, {"media_id": PHT2}])
    refused = post(operator_client, EDIT, data)
    rows = refused.context["media"]["public_card_images"].lists[0].rows
    assert rows[1].errors == ("No such asset — choose another",)


def test_publish_invalid_report_entries_are_placed_at_their_rows(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = character_page(operator_client, engine)
    engine.respond("POST", PUBLISH, 422, json=media("publish_invalid_reference_audience"))
    done = post(operator_client, EDIT, character_post(response, action="publish"))
    row = EngineOperation.objects.get(kind="publish_catalog_character")
    assert done["Location"] == f"{EDIT}?report={row.pk}"
    answer_edit_reads(engine)
    page = operator_client.get(done["Location"])
    shown = page.context["media"]
    refs = shown["visual_identity_reference_images"].lists[0].rows
    assert refs[1].notes == (
        "reference_image_missing_audience: FR-CAT-4: a reference image declares its audience",
    )
    assert refs[0].notes == ()
    gallery = shown["public_card_images"].lists[0].rows
    assert gallery[0].notes == ("This asset is retired — choose another",)
    assert shown["public_card_images"].single.notes == (
        "primary_image_required: require_primary_image is on",
    )
    html = page.content.decode()
    # The report links to the row anchors the page renders.
    assert 'href="#f-visual_identity-reference_images-1-"' in html  # the row: nearest ancestor
    assert 'id="f-visual_identity-reference_images-1-"' in html


def test_an_unreadable_id_shows_unavailable_with_a_remove_control(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = draft()
    body["images"]["gallery"][0]["media_id"] = GNE1
    answer_media(engine, missing=frozenset({GNE1}))
    answer_edit_reads(engine, body)
    html = operator_client.get(EDIT).content.decode()
    assert f"<code>{GNE1}</code> unavailable (404 not_found)" in html
    assert f'src="/media/{GNE1}/content/?img=1"' not in html  # never a broken image alone
    assert f'name="{GALLERY}-0-DELETE"' in html


def test_a_retired_asset_is_tagged(operator_client: Client, engine: FakeEngine) -> None:
    answer_media(engine, {MED2: media("get_media_retired", id=MED2)})
    answer_edit_reads(engine)
    html = operator_client.get(EDIT).content.decode()
    assert '<strong class="retired">retired</strong>' in html


def test_archived_entries_render_the_slots_disabled(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_edit_reads(engine, draft(status="archived"))
    html = operator_client.get(EDIT).content.decode()
    assert f'name="{PRIMARY}" id="f-public_card-images-primary-id" value="{MED1}"' in html
    start = html.index('id="f-public_card-images-primary-id"')
    assert " disabled" in html[start : html.index(">", start)]
    # #69's stand-in for "Add another".
    assert (
        '<button type="button" class="archived-disabled" disabled '
        'title="Archived — unarchive to edit">Add another</button>'
    ) in html
    assert 'value="add:public_card__images__gallery"' not in html


# --- create ---------------------------------------------------------------------------------------


def create_data(client: Client, engine: FakeEngine) -> QueryDict:
    answer_ratings(engine)
    page = client.get(NEW_URL)
    engine.reset()
    data = sections_posted(page)
    data.update(form_data(page))  # the hidden snapshot (the rating options) included
    data["owner"] = "alice"
    data["public_card__name"] = "Mira"
    return data


def test_create_omits_empty_sections(operator_client: Client, engine: FakeEngine) -> None:
    data = create_data(operator_client, engine)
    engine.respond("POST", LIST, 201, json=engine_fixture("catalog_characters_post"))
    post(operator_client, NEW_URL, data)
    (sent,) = calls(engine, "POST", LIST)
    body = body_of(sent)
    assert body["public_card"] == {"name": "Mira"}
    assert "visual_identity" not in body


def test_create_sends_the_picked_sections(operator_client: Client, engine: FakeEngine) -> None:
    data = create_data(operator_client, engine)
    data = set_rows(data, GALLERY, [{"media_id": PHT1}])
    data = set_rows(data, REFS, [{"media_id": PHT2, "audience": "restricted"}])
    engine.respond("POST", LIST, 201, json=engine_fixture("catalog_characters_post"))
    post(operator_client, NEW_URL, data)
    (sent,) = calls(engine, "POST", LIST)
    body = body_of(sent)
    assert body["public_card"] == {"name": "Mira", "images": {"gallery": [PHT1]}}
    assert body["visual_identity"] == {
        "reference_images": [{"media_id": PHT2, "audience": "restricted"}]
    }


def test_create_refuses_a_reference_without_audience(
    operator_client: Client, engine: FakeEngine
) -> None:
    data = set_rows(create_data(operator_client, engine), REFS, [{"media_id": PHT2}])
    refused = post(operator_client, NEW_URL, data)
    assert refused.status_code == 200
    assert engine.requests == []


# --- the character detail page --------------------------------------------------------------------


def answer_detail(engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=full())
    answer_ratings(engine)


def test_the_detail_page_shows_the_media_by_slot(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = full()
    body["images"]["gallery"].append({**body["images"]["gallery"][0], "media_id": GNE1})
    answer_media(engine, {MED3: media("get_media_retired", id=MED3)}, missing=frozenset({GNE1}))
    engine.respond("GET", ITEM, 200, json=body)
    html = operator_client.get(DETAIL).content.decode()
    page = text(html)
    assert "Primary" in page
    assert "Gallery 1" in page
    assert f'<a href="/media/{MED1}/"><img src="/media/{MED1}/content/?img=1"' in html
    assert f"<code>{GNE1}</code> unavailable (404 not_found)" in html
    assert "Reference — perceivable retired" in page  # MED3 is retired: tagged, still shown
    assert f'src="/media/{MED3}/content/?img=1"' in html


def test_the_card_view_shows_the_card_images(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=engine_fixture("get_catalog_character_card"))
    html = operator_client.get(DETAIL, {"projection": "card"}).content.decode()
    assert f'src="/media/{MED1}/content/?img=1"' in html
    assert "Reference —" not in html


# --- the picker and the slot-row partial ----------------------------------------------------------


def test_the_picker_lists_live_media_and_marks_ids_in_use(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", MEDIA_LIST, 200, json=media("list_media"))
    response = operator_client.get(
        "/media/picker/",
        {"field": GALLERY, "kind": "image", "index": "1", f"{GALLERY}-0-media_id": PHT2},
    )
    assert response.status_code == 200
    (listed,) = calls(engine, "GET", MEDIA_LIST)
    assert dict(listed.url.params) == {"limit": "50", "kind": "image"}
    html = response.content.decode()
    assert f"<code>{PHT1}</code>" in html
    assert f'src="/media/{PHT1}/content/?img=1" width="96"' in html
    assert '<small class="subject">persona …PERSN1</small>' in html
    tiles = html.split('<li class="tile')
    assert "in use" in next(t for t in tiles if PHT2 in t)
    assert "in use" not in next(t for t in tiles if f"<code>{PHT1}</code>" in t)
    # Picking a tile swaps the row with the partial for that id.
    assert (
        f"/media/slot-row/?field={GALLERY}&amp;kind=image&amp;media_id={PHT1}&amp;index=1" in html
    )
    assert 'hx-target="#f-public_card-images-gallery-1-"' in html
    assert "Next page" in html
    assert 'href="/media/upload/?kind=image" target="_blank"' in html


def test_the_picker_shows_a_video_poster_and_filters_by_owner(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", MEDIA_LIST, 200, json=media("list_media"))
    response = operator_client.get(
        "/media/picker/",
        {"field": VIDEOS, "kind": "video", "index": "0", "picker_owner": "alice", "cursor": "c1"},
    )
    (listed,) = calls(engine, "GET", MEDIA_LIST)
    assert dict(listed.url.params) == {
        "limit": "50",
        "kind": "video",
        "owner": "alice",
        "cursor": "c1",
    }
    html = response.content.decode()
    assert f'src="/media/{PST1}/content/?img=1"' in html
    assert "First page" in html


@pytest.mark.parametrize(
    "params",
    [
        {"field": "public_card.images.gallery", "kind": "image", "index": "0"},
        {"field": "definition__true_age", "kind": "image", "index": "0"},
        {"field": GALLERY, "kind": "video", "index": "0"},
        {"field": GALLERY, "kind": "image", "index": "20"},
        {"field": GALLERY, "kind": "image"},
        {"field": PRIMARY, "kind": "image", "index": "3"},
    ],
)
@pytest.mark.parametrize("url", ["/media/picker/", "/media/slot-row/"])
def test_a_malformed_slot_is_a_400_with_no_call(
    operator_client: Client, engine: FakeEngine, url: str, params: dict[str, str]
) -> None:
    response = operator_client.get(url, params)
    assert response.status_code == 400
    assert engine.requests == []


def test_close_answers_an_empty_panel(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get("/media/picker/", {"close": "1"})
    assert response.status_code == 200
    assert response.content == b""
    assert engine.requests == []


def test_the_slot_row_partial_looks_the_id_up(operator_client: Client, engine: FakeEngine) -> None:
    answer_media(
        engine,
        {VDE1: media("list_media")["items"][2]},
    )
    response = operator_client.get(
        "/media/slot-row/",
        {
            "field": VIDEOS,
            "kind": "video",
            "index": "2",
            "media_id": VDE1.lower(),
            f"{VIDEOS}-2-caption": "Night storm",
        },
    )
    assert response.status_code == 200
    assert media_reads(engine) == [VDE1]
    html = response.content.decode()
    assert 'id="f-public_card-media-videos-2-"' in html
    assert f'name="{VIDEOS}-2-media_id"' in html
    assert f'value="{VDE1}"' in html
    assert 'value="Night storm"' in html
    assert 'placeholder="Storm footage"' in html
    assert f"video/mp4 · 1280 {TIMES} 720 px · 1:05.3" in html
    assert f'src="/media/{PST1}/content/?img=1"' in html


def test_the_slot_row_partial_refuses_a_bad_id_without_a_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(
        "/media/slot-row/", {"field": PRIMARY, "kind": "image", "media_id": "nope"}
    )
    assert response.status_code == 200
    assert engine.requests == []
    assert "Not a media id (26-character ULID)" in response.content.decode()


# --- the scenario editor --------------------------------------------------------------------------


def scenario_media_draft() -> dict[str, Any]:
    body = sh.draft()
    body["public_card"]["media"] = {
        "cover": PHT1,
        "gallery": [PHT1, PHT2],
        "videos": [{"media_id": VDE1, "caption": None}],
    }
    return body


def scenario_page(client: Client, engine: FakeEngine, body: dict[str, Any] | None = None) -> Any:
    answer_media(engine, {VDE1: media("list_media")["items"][2]})
    sh.answer_edit_reads(engine, body if body is not None else scenario_media_draft())
    response = client.get(sh.EDIT)
    assert response.status_code == 200
    engine.reset()
    return response


def scenario_post(response: Any) -> QueryDict:
    data = sh.posted(response)
    for key, values in sections_posted(response).lists():
        data.setlist(key, values)
    return data


def scenario_patch(engine: FakeEngine) -> dict[str, Any]:
    (sent,) = calls(engine, "PATCH", sh.PATH)
    body: dict[str, Any] = body_of(sent)
    return body


def test_the_scenario_editor_shows_the_media_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = scenario_page(operator_client, engine).content.decode()
    assert "<legend>Media</legend>" in html
    assert f'name="{COVER}" id="f-public_card-media-cover-id" value="{PHT1}"' in html
    assert f'name="{VIDEOS}-0-caption"' in html
    assert 'placeholder="Storm footage"' in html  # the asset's caption
    assert 'href="/media/upload/?kind=video" target="_blank"' in html


def test_an_unchanged_scenario_media_section_is_not_sent(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = scenario_page(operator_client, engine)
    engine.respond("PATCH", sh.S_ITEM, 200, json=sh.fixture("patch"))
    data = scenario_post(response)
    data["public_card__name"] = "Harvest Week"
    post(operator_client, sh.EDIT, data)
    assert scenario_patch(engine)["public_card"] == {"name": "Harvest Week"}


def test_a_changed_scenario_media_section_is_sent_whole(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = scenario_page(operator_client, engine)
    engine.respond("PATCH", sh.S_ITEM, 200, json=sh.fixture("patch"))
    data = scenario_post(response)
    data[COVER] = ""
    data = set_rows(
        data,
        VIDEOS,
        [{"media_id": VDE1, "caption": "  The storm  "}, {"media_id": MED2, "caption": ""}],
    )
    post(operator_client, sh.EDIT, data)
    assert scenario_patch(engine)["public_card"] == {
        "media": {
            "cover": None,
            "gallery": [PHT1, PHT2],
            "videos": [
                {"media_id": VDE1, "caption": "The storm"},
                {"media_id": MED2, "caption": None},
            ],
        }
    }


def test_videos_and_captions_are_bounded_before_any_call(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = scenario_page(operator_client, engine)
    data = set_rows(scenario_post(response), VIDEOS, [{"media_id": m} for m in ids(21)])
    refused = post(operator_client, sh.EDIT, data)
    assert engine.requests == []
    videos = refused.context["media"]["public_card_media"].lists[1]
    assert videos.errors == ("At most 20 videos",)
    long = set_rows(scenario_post(response), VIDEOS, [{"media_id": VDE1, "caption": "x" * 501}])
    refused = post(operator_client, sh.EDIT, long)
    assert engine.requests == []
    row = refused.context["media"]["public_card_media"].lists[1].rows[0]
    assert row.errors == ("At most 500 characters",)


def test_scenario_duplicates_are_refused_and_media_retired_placed(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = scenario_page(operator_client, engine)
    dup = set_rows(scenario_post(response), S_GALLERY, [{"media_id": PHT2}, {"media_id": PHT2}])
    refused = post(operator_client, sh.EDIT, dup)
    assert engine.requests == []
    rows = refused.context["media"]["public_card_media"].lists[0].rows
    assert rows[1].errors == ("Already in this list (row 1)",)
    retired = sh.fixture("422_validation")
    retired["error"] = {
        "code": "media_retired",
        "message": "media asset cannot be referenced (retired)",
        "details": {
            "media_id": VDE1,
            "field": "body.public_card.media.videos[0].media_id",
            "reason": "retired",
        },
    }
    engine.respond("PATCH", sh.S_ITEM, 422, json=retired)
    data = scenario_post(response)
    data[f"{VIDEOS}-0-caption"] = "new caption"
    refused = post(operator_client, sh.EDIT, data)
    row = refused.context["media"]["public_card_media"].lists[1].rows[0]
    assert row.errors == ("This asset is retired — choose another",)


def test_the_scenario_create_page_has_the_media_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    sh.answer_profiles(engine)
    page = operator_client.get(sh.NEW_URL)
    assert "<legend>Media</legend>" in page.content.decode()
    engine.reset()
    data = sh.posted(page)
    for key, values in sections_posted(page).lists():
        data.setlist(key, values)
    data["public_card__name"] = "New"
    data[COVER] = PHT1
    engine.respond("POST", sh.S_LIST, 201, json=sh.fixture("post"))
    post(operator_client, sh.NEW_URL, data)
    (sent,) = calls(engine, "POST", sh.S_LIST)
    assert body_of(sent)["public_card"] == {"name": "New", "media": {"cover": PHT1}}


# --- the scenario detail page ---------------------------------------------------------------------


def test_the_scenario_detail_plays_the_video_with_its_poster(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = sh.detail()
    body["public_card"]["media"] = {
        "cover": PHT1,
        "gallery": [PHT2],
        "videos": [{"media_id": VDE1, "caption": None}],
    }
    answer_media(engine, {VDE1: media("list_media")["items"][2]})
    engine.respond("GET", sh.S_ITEM, 200, json=body)
    answer_ratings(engine)
    sh.answer_profiles(engine)
    html = operator_client.get(sh.DETAIL).content.decode()
    assert (
        f'<video controls preload="metadata" src="/media/{VDE1}/content/" '
        f'poster="/media/{PST1}/content/?img=1"></video>'
    ) in html
    assert '<p class="caption">Storm footage</p>' in html  # the asset's caption
    assert 'class="media-shown media-large"' in html  # the cover
    assert f'src="/media/{PHT2}/content/?img=1" alt="A lighthouse at dusk"' in html
