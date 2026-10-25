"""#72: add a character to a running story, promote one to the catalog, and the character edit
page's "Before this can be published" banner."""

import copy
import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.test import Client, override_settings

from console.catalog.readiness import missing_for_publish
from console.engine.choices import RatingChoices, rating_choices
from console.engine.client import EngineClient, EngineError
from console.engine.models import CatalogCharacter, ReportEntry
from console.engine_access import engine_client_for
from console.models import EngineOperation
from console.views import catalog_characters, media_picker, play, story_characters
from console.views.story_characters import StoryHeader, warning_line
from tests.catalog.helpers import COMPANION, ITEM, companion, draft
from tests.catalog.media_helpers import answer_media
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.fixtures import engine_fixture
from tests.play.helpers import MARA, STORY, TOBIN, answer_page, story_json
from tests.ratings.helpers import TEEN, answer_ratings, answer_settings, rating

STORY_PATH = "/stories/{story_id}"
ADD = "/stories/{story_id}/characters"
PROMOTE = "/stories/{story_id}/characters/{character_id}/promote"
NEW_URL = f"/stories/{STORY}/characters/new/"
PROMOTE_URL = f"/stories/{STORY}/characters/{TOBIN}/promote/"
PLAY_URL = f"/stories/{STORY}/"
IMAGE_1 = "01JB2Z8Y5Q3W6K9M1N4P7R0M01"
IMAGE_2 = "01JB2Z8Y5Q3W6K9M1N4P7R0M02"
CATALOG_ID = "01JB2Z8Y5Q3W6K9M1N4P7R0C01"
EARLIER = "01JB2Z8Y5Q3W6K9M1N4P7R0C00"
KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KY9"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the play view, the story-character pages and the character edit page
    talk to, through the real recording client."""
    fake = FakeEngine()
    answer_relations(fake)  # #79: every owned entity's page reads its relations
    answer_effective_config(fake)  # #85: the play header's fingerprint

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    for module in (play, story_characters, catalog_characters, media_picker):
        monkeypatch.setattr(module, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"stories/{name}"))
    return body


def archived() -> dict[str, Any]:
    return story_json(status="archived", archived_at="2026-10-24T22:00:00Z")


def omniscient(**changes: Any) -> dict[str, Any]:
    """The omniscient read: each character with its true age and ``promoted_to``."""
    story = story_json(**changes)
    for character in story["characters"]:
        character.setdefault("true_age", 30)
        character.setdefault("promoted_to", [])
    return story


def writes(engine: FakeEngine) -> list[Any]:
    return [r for r in engine.requests if r.method != "GET"]


def hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, name
    return match.group(1)


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


# --- add: the form and the body -------------------------------------------------------------


def add_page(client: Client, engine: FakeEngine, story: dict[str, Any] | None = None) -> str:
    engine.respond("GET", STORY_PATH, 200, json=story or omniscient())
    response = client.get(NEW_URL)
    assert response.status_code == 200
    return response.content.decode()


def add_data(html: str, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "snapshot": hidden(html, "snapshot"),
        "idempotency_key": KEY,
        "expected_revision": hidden(html, "expected_revision"),
        "name": "Dale",
        "definition": "A delivery driver in a hurry,\r\npolite and nosy.",
        "true_age": "52",
        "safe_fallback_text": "",
        "deflection_style": "",
        "images-TOTAL_FORMS": "1",
        "images-INITIAL_FORMS": "0",
        "images-MIN_NUM_FORMS": "0",
        "images-MAX_NUM_FORMS": "21",
        "images-0-media_id": "",
        "images-0-ORDER": "1",
    }
    data.update(changes)
    return data


def test_the_add_page_reads_the_story_once_omnisciently(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = add_page(operator_client, engine)
    (read,) = engine.requests
    assert read.url.params["viewpoint"] == "omniscient"
    assert "Timeline revision 1 · Cast 2/20" in html
    assert hidden(html, "expected_revision") == "1"
    for field in ("f-name", "f-definition", "f-true_age", "f-safe_fallback_text"):
        assert f'id="{field}"' in html
    assert 'name="images-0-media_id"' in html


def test_the_add_body_and_the_flash(operator_client: Client, engine: FakeEngine) -> None:
    html = add_page(operator_client, engine)
    engine.respond("POST", ADD, 201, json=fixture("add_character_201"))
    data = add_data(
        html,
        **{
            "deflection_style": "Talks about traffic.",
            "safe_fallback_text": "Got to run.",
            "images-TOTAL_FORMS": "2",
            "images-0-media_id": IMAGE_2.lower(),
            "images-0-ORDER": "2",
            "images-1-media_id": IMAGE_1,
            "images-1-ORDER": "1",
        },
    )
    response = operator_client.post(NEW_URL, data)
    assert response.status_code == 302
    assert response["Location"] == PLAY_URL
    (sent,) = writes(engine)
    assert json.loads(sent.content) == {
        "name": "Dale",
        "definition": "A delivery driver in a hurry,\npolite and nosy.",
        "true_age": 52,
        "safe_fallback_text": "Got to run.",
        "deflection_style": "Talks about traffic.",
        "images": [IMAGE_1, IMAGE_2],
    }
    assert sent.headers["X-Epistrel-Expected-Revision"] == "1"
    assert sent.headers["Idempotency-Key"] == KEY
    assert flashes(response) == [
        "Added Dale (cast #2) — timeline revision 2 — key 01JB2Z8Y5Q3W6K9M1N4P7R0KY1"
    ]
    row = EngineOperation.objects.get(kind="add_story_character")
    assert (row.entity_type, row.entity_id, row.story_id) == ("story", STORY, STORY)


def test_blank_optionals_are_left_out(operator_client: Client, engine: FakeEngine) -> None:
    html = add_page(operator_client, engine)
    engine.respond("POST", ADD, 201, json=fixture("add_character_201"))
    operator_client.post(NEW_URL, add_data(html, deflection_style="   "))
    (sent,) = writes(engine)
    assert set(json.loads(sent.content)) == {"name", "definition", "true_age"}


@pytest.mark.parametrize(
    ("changes", "field", "text"),
    [
        ({"name": "x" * 81}, "name", "at most 80 characters"),
        ({"name": "   "}, "name", "This field is required."),
        ({"definition": " \n "}, "definition", "This field is required."),
        ({"definition": "d" * 20001}, "definition", "at most 20000 characters"),
        ({"true_age": "151"}, "true_age", "less than or equal to 150"),
        ({"true_age": "-1"}, "true_age", "greater than or equal to 0"),
        ({"true_age": ""}, "true_age", "Enter a true age."),
        ({"deflection_style": "s" * 2001}, "deflection_style", "at most 2000 characters"),
        ({"safe_fallback_text": "s" * 501}, "safe_fallback_text", "at most 500 characters"),
        ({"images-0-media_id": "not-an-id"}, "images", "Not a media id"),
    ],
)
def test_console_checks_make_no_call(
    operator_client: Client, engine: FakeEngine, changes: dict[str, str], field: str, text: str
) -> None:
    html = add_page(operator_client, engine)
    engine.requests.clear()
    response = operator_client.post(NEW_URL, add_data(html, **changes))
    assert response.status_code == 200
    assert text in response.content.decode()
    assert engine.requests == []


def test_a_duplicate_image_is_refused_with_its_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = add_page(operator_client, engine)
    engine.requests.clear()
    data = add_data(
        html,
        **{
            "images-TOTAL_FORMS": "2",
            "images-0-media_id": IMAGE_1,
            "images-1-media_id": IMAGE_1,
            "images-1-ORDER": "2",
        },
    )
    page = operator_client.post(NEW_URL, data).content.decode()
    assert "Already in this list (row 1)" in page
    assert engine.requests == []


def test_more_than_twenty_images_are_refused(operator_client: Client, engine: FakeEngine) -> None:
    html = add_page(operator_client, engine)
    engine.requests.clear()
    rows: dict[str, str] = {"images-TOTAL_FORMS": "21"}
    for index in range(21):
        rows[f"images-{index}-media_id"] = f"01JB2Z8Y5Q3W6K9M1N4P7R0M{index:02d}"
        rows[f"images-{index}-ORDER"] = str(index + 1)
    page = operator_client.post(NEW_URL, add_data(html, **rows)).content.decode()
    assert "At most 20 images" in page
    assert engine.requests == []


def test_add_another_image_row_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = add_page(operator_client, engine)
    engine.requests.clear()
    page = operator_client.post(NEW_URL, add_data(html, op="add:images")).content.decode()
    assert 'name="images-1-media_id"' in page
    assert 'value="Dale"' in page
    assert "This field is required" not in page
    assert engine.requests == []


def test_the_image_rows_work_with_the_picker_endpoints(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#71's slot-row and picker endpoints accept the add form's ``images`` list."""
    answer_media(engine)
    row = operator_client.get(
        "/media/slot-row/", {"field": "images", "kind": "image", "index": "0", "media_id": IMAGE_1}
    )
    assert row.status_code == 200
    assert 'name="images-0-media_id"' in row.content.decode()
    assert IMAGE_1 in row.content.decode()
    refused = operator_client.get("/media/slot-row/", {"field": "images", "kind": "video"})
    assert refused.status_code == 400


# --- add: refusals --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "status", "texts"),
    [
        ("error_validation_error_cast_full", 422, ["The cast is full (20 characters)"]),
        (
            "error_validation_error_duplicate",
            422,
            ["This story already has a character named Dale"],
        ),
        ("error_media_retired", 422, ["This asset is retired — choose another"]),
        (
            "error_guardrail_violation",
            422,
            ["Rejected by a guardrail — the character was not added", "Minors: no sexual content"],
        ),
        (
            "error_budget_below_safety_minimum",
            422,
            ["The story&#x27;s budget is below the safety minimum for the input gate"],
        ),
        ("error_story_archived", 409, ["The story is archived — unarchive to add characters"]),
        ("error_story_paused", 409, ["The story is paused — resume it to add characters"]),
        (
            "error_erased_subject_referenced",
            409,
            ["Refers to an erased subject", "01JB2Z8Y5Q3W6K9M1N4P7R0E01"],
        ),
        (
            "error_story_busy",
            423,
            ["Story busy — another turn is running", "01JB2Z8Y5Q3W6K9M1N4P7R0OP1"],
        ),
        (
            "error_revision_conflict",
            409,
            ["The story changed since this form opened (now revision 3) — check and submit again"],
        ),
        ("error_model_unavailable", 503, ["503", "model_unavailable"]),
        ("error_idempotency_conflict", 409, ["idempotency_conflict"]),
    ],
)
def test_each_add_refusal_keeps_the_input(
    operator_client: Client, engine: FakeEngine, name: str, status: int, texts: list[str]
) -> None:
    html = add_page(operator_client, engine)
    engine.respond("POST", ADD, status, json=fixture(name))
    data = add_data(html, **{"images-0-media_id": IMAGE_1})
    response = operator_client.post(NEW_URL, data)
    assert response.status_code == 200
    page = response.content.decode()
    for text in texts:
        assert text in page
    assert 'value="Dale"' in page
    assert hidden(page, "idempotency_key") != KEY  # a fresh key per render (#43)


def test_a_retired_image_is_marked_at_its_row(operator_client: Client, engine: FakeEngine) -> None:
    html = add_page(operator_client, engine)
    engine.respond("POST", ADD, 422, json=fixture("error_media_retired"))
    page = operator_client.post(
        NEW_URL, add_data(html, **{"images-0-media_id": IMAGE_1})
    ).content.decode()
    row = page.split('id="f-images-0-"', 1)[1].split('class="picker-slot"', 1)[0]
    assert "This asset is retired — choose another" in row


def test_a_validation_error_at_an_image_lands_on_its_row(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = add_page(operator_client, engine)
    engine.respond("POST", ADD, 422, json=fixture("error_validation_error_not_an_image"))
    data = add_data(
        html,
        **{
            "images-TOTAL_FORMS": "2",
            "images-0-media_id": IMAGE_1,
            "images-1-media_id": IMAGE_2,
            "images-1-ORDER": "2",
        },
    )
    page = operator_client.post(NEW_URL, data).content.decode()
    row = page.split('id="f-images-1-"', 1)[1].split('class="picker-slot"', 1)[0]
    assert "is not an image" in row


def test_a_revision_conflict_resends_with_the_current_revision(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = add_page(operator_client, engine)
    engine.respond("POST", ADD, 409, json=fixture("error_revision_conflict"))
    page = operator_client.post(NEW_URL, add_data(html)).content.decode()
    assert hidden(page, "expected_revision") == "3"


# --- add: archived (complement) -------------------------------------------------------------


def test_an_archived_story_disables_add_character(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, story=archived())
    html = operator_client.get(PLAY_URL).content.decode()
    assert (
        '<button type="button" class="add-character" disabled '
        'title="Archived — unarchive to add characters">Add character (2/20)</button>'
    ) in html
    assert f'href="/stories/{STORY}/characters/new/"' not in html


def test_a_running_story_offers_add_character(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    html = operator_client.get(PLAY_URL).content.decode()
    assert f'<a class="button add-character" href="{NEW_URL}">Add character (2/20)</a>' in html


def test_the_add_page_of_an_archived_story_redirects(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", STORY_PATH, 200, json=archived())
    response = operator_client.get(NEW_URL)
    assert response.status_code == 302
    assert response["Location"] == PLAY_URL
    assert flashes(response) == ["The story is archived — unarchive to add characters"]


def test_a_forced_add_on_an_archived_story_shows_the_engine_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    snapshot = StoryHeader(STORY, "Harbour", 1, 2, "archived").signed()
    engine.respond("POST", ADD, 409, json=fixture("error_story_archived"))
    data = add_data(
        f'name="snapshot" value="{snapshot}" name="expected_revision" value="1"',
        snapshot=snapshot,
    )
    page = operator_client.post(NEW_URL, data).content.decode()
    assert len(writes(engine)) == 1
    assert "HTTP 409 story_archived" in page
    assert "The story is archived — unarchive to add characters" in page


# --- the cast panel -------------------------------------------------------------------------


def test_promote_shows_on_non_players_only(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    html = operator_client.get(PLAY_URL).content.decode()
    assert f'href="{PROMOTE_URL}">Promote to catalog</a>' in html
    assert f"/characters/{MARA}/promote/" not in html
    assert '<li>Mara (player) <a class="button danger erase"' in html


def test_every_cast_member_links_erase_in_this_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#86: "Erase…" pre-fills a story-scope erasure of the character, players included."""
    answer_page(engine)
    html = operator_client.get(PLAY_URL).content.decode()
    for character in (MARA, TOBIN):
        assert (
            f'href="/erasure/new/?kind=character&amp;subject={character}&amp;scope=story'
            f'&amp;story={STORY}">Erase…</a>'
        ) in html


def test_promote_is_enabled_on_an_archived_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine, story=archived())
    html = operator_client.get(PLAY_URL).content.decode()
    assert f'<a class="button promote" href="{PROMOTE_URL}">Promote to catalog</a>' in html


def test_promoted_links_oldest_first_and_promote_again(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_page(engine)
    story = omniscient()
    story["characters"][1]["promoted_to"] = [EARLIER, CATALOG_ID]
    engine.respond_sequence(
        "GET", STORY_PATH, [{"status": 200, "json": story_json()}, {"status": 200, "json": story}]
    )
    html = operator_client.get(PLAY_URL).content.decode()
    reads = [r for r in engine.requests if r.url.path == f"/stories/{STORY}"]
    assert [r.url.params.get("viewpoint") for r in reads] == [None, "omniscient"]
    assert ">Promote again</a>" in html
    first = html.index(f"<code>{EARLIER}</code>")
    assert first < html.index(f"<code>{CATALOG_ID}</code>")
    assert f'href="/catalog/characters/{EARLIER}/">Promoted → ' in html


def test_promotion_links_unavailable(operator_client: Client, engine: FakeEngine) -> None:
    answer_page(engine)
    unavailable = {"error": {"code": "database_unavailable", "message": "down", "details": {}}}
    engine.respond_sequence(
        "GET",
        STORY_PATH,
        [{"status": 200, "json": story_json()}, {"status": 503, "json": unavailable}],
    )
    html = operator_client.get(PLAY_URL).content.decode()
    assert "Promotion links unavailable: 503 database_unavailable" in html
    assert f'href="{PROMOTE_URL}">Promote to catalog</a>' in html
    assert "Promoted →" not in html


# --- promote --------------------------------------------------------------------------------


def promote_page(
    client: Client, engine: FakeEngine, story: dict[str, Any] | None = None, **ratings: Any
) -> str:
    engine.respond("GET", STORY_PATH, 200, json=story or omniscient())
    answer_ratings(engine, **ratings)
    response = client.get(PROMOTE_URL)
    assert response.status_code == 200
    return response.content.decode()


def promote_data(html: str, **changes: str) -> dict[str, str]:
    data = {
        "snapshot": hidden(html, "snapshot"),
        "idempotency_key": KEY,
        "name": "Tobin",
        "owner": "console:nate",
        "content_rating_id": "",
    }
    data.update(changes)
    return data


def test_the_promote_form_defaults(operator_client: Client, engine: FakeEngine) -> None:
    html = promote_page(operator_client, engine)
    assert 'name="name" value="Tobin"' in html
    assert 'value="console:nate"' in html
    assert '<option value="" selected>The story&#x27;s rating (Teen)</option>' in html
    assert "seed_memories" not in html
    reads = [r for r in engine.requests if r.url.path == f"/stories/{STORY}"]
    assert [r.url.params.get("viewpoint") for r in reads] == ["omniscient"]


def test_the_promote_body_defaults(operator_client: Client, engine: FakeEngine) -> None:
    html = promote_page(operator_client, engine)
    engine.requests.clear()
    engine.respond("POST", PROMOTE, 201, json=fixture("promote_201"))
    response = operator_client.post(PROMOTE_URL, promote_data(html))
    assert response.status_code == 302
    assert response["Location"] == f"/catalog/characters/{CATALOG_ID}/edit/"
    (sent,) = engine.requests  # the POST does not re-read
    assert json.loads(sent.content) == {"name": "Tobin", "owner": "console:nate"}
    assert "X-Epistrel-Expected-Revision" not in sent.headers
    row = EngineOperation.objects.get(kind="promote_story_character")
    assert (row.entity_type, row.entity_id, row.story_id) == ("character", CATALOG_ID, STORY)


def test_a_chosen_rating_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    html = promote_page(operator_client, engine)
    engine.respond("POST", PROMOTE, 201, json=fixture("promote_201"))
    operator_client.post(PROMOTE_URL, promote_data(html, content_rating_id=TEEN, name="Dale"))
    (sent,) = writes(engine)
    assert json.loads(sent.content) == {
        "name": "Dale",
        "owner": "console:nate",
        "content_rating_id": TEEN,
    }


def test_the_redirect_carries_the_warnings(operator_client: Client, engine: FakeEngine) -> None:
    html = promote_page(operator_client, engine)
    engine.respond("POST", PROMOTE, 201, json=fixture("promote_201"))
    response = operator_client.post(PROMOTE_URL, promote_data(html))
    entry = f"entry {CATALOG_ID} vdraft"
    assert flashes(response) == [
        f"Promoted Tobin → draft {CATALOG_ID} (revision 1)",
        "Warning definition_truncated: Definition cut to 4000 characters in background flavor "
        f"({entry} · field definition.background_flavor)",
        f"Warning media_retired: Image {IMAGE_1} was retired and left out "
        f"({entry} · field public_card.images)",
    ]


def test_an_unknown_warning_keeps_the_engine_text() -> None:
    entry = ReportEntry.from_json(
        {
            "code": "something_new",
            "location": {"entry": CATALOG_ID, "version": "draft", "field": "name"},
            "outcome": "kept",
            "text": "<b>bold</b>",
        }
    )
    assert entry is not None
    assert warning_line(entry) == (
        f"Warning something_new: <b>bold</b> (entry {CATALOG_ID} vdraft · field name)"
    )


def test_the_promote_page_is_enabled_on_an_archived_story(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = promote_page(operator_client, engine, story=omniscient(status="archived"))
    assert '<button type="submit">Promote</button>' in html


def test_a_player_character_redirects(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 200, json=omniscient())
    response = operator_client.get(f"/stories/{STORY}/characters/{MARA}/promote/")
    assert response.status_code == 302
    assert response["Location"] == PLAY_URL
    assert flashes(response) == ["Player characters play a persona and can't be promoted"]


def test_a_forced_promote_of_a_player_shows_the_engine_422(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    engine.respond("POST", PROMOTE, 422, json=fixture("error_validation_error_player_character"))
    data = {"name": "Mara", "owner": "console:nate", "content_rating_id": ""}
    page = operator_client.post(f"/stories/{STORY}/characters/{MARA}/promote/", data)
    html = page.content.decode()
    assert "a player character plays a persona and cannot be promoted to the catalog" in html
    assert "HTTP 422 validation_error" in html
    assert len(writes(engine)) == 1


@pytest.mark.parametrize(
    ("name", "status", "texts"),
    [
        ("error_rating_retired", 422, ["That rating is retired — choose another"]),
        (
            "error_story_busy",
            423,
            ["Story busy — another turn is running", "01JB2Z8Y5Q3W6K9M1N4P7R0OP1"],
        ),
        ("error_erased_subject_referenced", 409, ["Refers to an erased subject"]),
        ("error_idempotency_conflict", 409, ["idempotency_conflict"]),
    ],
)
def test_each_promote_refusal(
    operator_client: Client, engine: FakeEngine, name: str, status: int, texts: list[str]
) -> None:
    html = promote_page(operator_client, engine)
    engine.respond("POST", PROMOTE, status, json=fixture(name))
    page = operator_client.post(PROMOTE_URL, promote_data(html, name="Tobin T")).content.decode()
    for text in texts:
        assert text in page
    assert 'value="Tobin T"' in page


def test_the_rating_default_names_a_retired_story_rating(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = promote_page(operator_client, engine, Teen={"retired_at": "2026-10-01T00:00:00Z"})
    assert "The story&#x27;s rating (Teen, retired)</option>" in html
    assert f'<option value="{TEEN}"' not in html


def test_failed_ratings_offer_only_the_default(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 200, json=omniscient())
    down = {"error": {"code": "database_unavailable", "message": "down", "details": {}}}
    engine.respond("GET", "/admin/content-ratings", 503, json=down)
    html = operator_client.get(PROMOTE_URL).content.decode()
    select = html.split('name="content_rating_id"', 1)[1].split("</select>", 1)[0]
    assert select.count("<option") == 1
    assert "The story&#x27;s rating</option>" in select
    assert "Ratings unavailable: 503 database_unavailable" in html


def test_a_bad_owner_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = promote_page(operator_client, engine)
    engine.requests.clear()
    page = operator_client.post(PROMOTE_URL, promote_data(html, owner="system")).content.decode()
    assert "reserved for the Engine" in page
    assert engine.requests == []


# --- the readiness banner -------------------------------------------------------------------


def promoted_draft() -> dict[str, Any]:
    """A draft as a promotion leaves it: name, true age, rating and background flavor only."""
    body = draft(public_description=None, aliases=[], labels=[], visual_identity=None)
    body["images"] = {"primary": None, "gallery": []}
    body["definition"] = {
        "true_age": 40,
        "appearance": None,
        "personality": None,
        "values_motivations": None,
        "background_flavor": "A ferry pilot.",
        "likes_dislikes": None,
        "dispositions": None,
        "interaction_style": None,
        "deflection_style": None,
        "safe_fallback_text": None,
    }
    return body


def complete_draft() -> dict[str, Any]:
    body = draft()
    body["definition"]["dispositions"]["caution"] = {"setting": "careful", "nuance": None}
    return body


def edit_page(client: Client, engine: FakeEngine, body: dict[str, Any]) -> str:
    engine.respond("GET", ITEM, 200, json=body)
    engine.respond("GET", COMPANION, 200, json=companion())
    answer_ratings(engine)
    answer_settings(engine)
    answer_media(engine)
    response = client.get(f"/catalog/characters/{body['id']}/edit/")
    assert response.status_code == 200
    return response.content.decode()


def banner(html: str) -> str:
    return html.split('class="banner banner-warning publish-readiness"', 1)[1].split("</div>")[0]


def labels(html: str) -> list[str]:
    return re.findall(r'<a href="#[^"]+">([^<]+)</a>', banner(html))


def test_the_banner_lists_exactly_the_empty_required_fields(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, promoted_draft())
    assert "Before this can be published:" in banner(html)
    assert labels(html) == [
        "public description",
        "appearance",
        "personality",
        "values &amp; motivations",
        "disposition: threat response",
        "disposition: dominance",
        "disposition: honesty",
        "disposition: warmth",
        "disposition: caution",
        "speech style",
        "vocabulary",
    ]
    assert '<a href="#f-definition-dispositions-warmth">' in banner(html)
    assert 'id="f-definition-dispositions-warmth"' in html
    assert html.index("publish-readiness") < html.index('class="character-form"')


def test_the_banner_is_absent_when_everything_is_filled(
    operator_client: Client, engine: FakeEngine
) -> None:
    html = edit_page(operator_client, engine, complete_draft())
    assert "publish-readiness" not in html
    assert "Before this can be published" not in html


def test_the_banner_flags_a_retired_rating(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=complete_draft())
    engine.respond("GET", COMPANION, 200, json=companion())
    answer_ratings(engine, Teen={"retired_at": "2026-10-01T00:00:00Z"})
    answer_settings(engine)
    answer_media(engine)
    html = operator_client.get(f"/catalog/characters/{complete_draft()['id']}/edit/").content
    assert labels(html.decode()) == ["content rating (retired)"]


def test_the_banner_is_absent_on_the_new_page(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    html = operator_client.get("/catalog/characters/new/").content.decode()
    assert "publish-readiness" not in html


def test_the_banner_shows_on_an_archived_draft(operator_client: Client, engine: FakeEngine) -> None:
    html = edit_page(operator_client, engine, {**promoted_draft(), "status": "archived"})
    assert "Before this can be published:" in html


def parsed(body: dict[str, Any]) -> CatalogCharacter:
    found = CatalogCharacter.from_json(body)
    assert found is not None
    return found


def ratings_ok(*extra: tuple[str, str]) -> RatingChoices:
    return RatingChoices([(TEEN, "Teen"), *extra])


def test_readiness_rules() -> None:
    """Whitespace counts as missing; a rating not listed is missing; lists may be empty; a
    reference image without an audience, the primary image (when required) and the visual
    identity's required fields are listed."""
    body = complete_draft()
    body["name"] = "   "
    body["definition"]["likes_dislikes"] = None
    body["definition"]["interaction_style"]["mannerisms"] = []
    body["content_rating"] = {**body["content_rating"], "id": "01JB2Z8Y5Q3W6K9M1N4P7R0RAT"}
    body["visual_identity"]["seed"] = None
    body["visual_identity"]["reference_images"][1]["audience"] = None
    body["images"]["primary"] = None
    found = missing_for_publish(parsed(body), ratings_ok(), require_primary_image=True)
    assert [(i.label, i.field_id) for i in found.items] == [
        ("name", "f-public_card-name"),
        ("primary image", "f-public_card-images-primary"),
        ("content rating (missing)", "f-metadata-content_rating_id"),
        ("visual identity: seed", "f-visual_identity-seed"),
        ("reference image 2: audience", "f-visual_identity-reference_images-1-"),
    ]
    assert found.notes == ()


def test_readiness_without_a_rating_or_when_unchecked() -> None:
    body = complete_draft()
    body["content_rating"] = None
    found = missing_for_publish(parsed(body), ratings_ok(), require_primary_image=None)
    assert [i.label for i in found.items] == ["content rating"]
    unchecked = RatingChoices([], EngineError("database_unavailable", "down", {}, 503))
    found = missing_for_publish(parsed(complete_draft()), unchecked, require_primary_image=False)
    assert found.items == ()
    assert found.notes == ("content rating not checked (503 database_unavailable)",)
    assert found.shown


def test_readiness_uses_the_rating_choices_labels(fake_engine: FakeEngine) -> None:
    """``rating_choices`` with the draft's rating as ``current`` labels a retired one
    "(retired)", which the banner reads."""
    answer_ratings(fake_engine, Teen={"retired_at": "2026-10-01T00:00:00Z"})
    client = EngineClient("http://engine.test", "key", transport=fake_engine.transport)
    choices = rating_choices(client, current=TEEN)
    found = missing_for_publish(parsed(complete_draft()), choices, require_primary_image=False)
    assert [i.label for i in found.items] == ["content rating (retired)"]
    assert rating("Teen")["id"] == TEEN
