"""Upload media with declared subjects, browse the library and retire assets (#70)."""

import copy
import json
import re
from collections.abc import Iterator
from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpRequest
from django.test import Client, override_settings

from console.engine.client import EngineClient
from console.engine_access import engine_client_for
from console.models import EngineOperation, KnownPrincipal
from console.templatetags.console_format import human_bytes
from console.views import media
from console.views.media import duration
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import TEEN, answer_ratings, calls, error, hidden, text

pytestmark = pytest.mark.django_db

MEDIA_DIR = Path(__file__).resolve().parent / "fixtures" / "engine" / "media"
PNG = (MEDIA_DIR / "sample.png").read_bytes()
MP4 = (MEDIA_DIR / "sample.mp4").read_bytes()
LIST = "/media"
ITEM = "/media/{media_id}"
CHARACTERS = "/catalog/characters"
CHARACTER = "/catalog/characters/{character_id}"
PERSONAS = "/personas"
PERSONA = "/personas/{persona_id}"
MED1 = "01JC000000000000000000MED1"
MED2 = "01JC000000000000000000MED2"
POSTER = "01JC000000000000000000MED3"
ALDRIC = "01JC00000000000000000CHAR1"
DRAFTY = "01JC00000000000000000CHAR3"
ARI = "01JP0000000000000000PERSA1"
STORY_CHAR = "01JS0000000000000000CHARS1"
KEY = "01JP00000000000000000KEY02"
URL = "/media/"
UPLOAD = "/media/upload/"
DETAIL = f"/media/{MED1}/"
RETIRE = f"/media/{MED1}/retire/"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the media views talk to, through the real recording client."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(media, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def asset(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_media"))
    return {**body, **changes}


def video(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": MED2,
        "kind": "video",
        "mime_type": "video/mp4",
        "size": 232,
        "width": 16,
        "height": 16,
        "duration_ms": 65_300,
        "poster_media_id": POSTER,
    }
    return asset(**{**fields, **changes})


def uploaded(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("upload_media"))
    return {**body, **changes}


def character(character_id: str, name: str, status: str = "published") -> dict[str, Any]:
    item: dict[str, Any] = copy.deepcopy(engine_fixture("list_catalog_characters")["items"][0])
    item.update(id=character_id, name=name, status=status)
    if status == "draft":
        item["version"] = None
    return item


def persona(persona_id: str, name: str, owner: str) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_persona"))
    return {**body, "id": persona_id, "name": name, "owner_principal": owner}


def answer_characters(
    engine: FakeEngine,
    published: list[dict[str, Any]] | None = None,
    draft: list[dict[str, Any]] | None = None,
) -> None:
    """``GET /catalog/characters`` by ``status``: Sir Aldric published, Drafty draft-only."""
    pages = {
        "published": published if published is not None else [character(ALDRIC, "Sir Aldric")],
        "draft": draft if draft is not None else [character(DRAFTY, "Drafty", "draft")],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        items = pages[request.url.params.get("status", "published")]
        return httpx.Response(200, json={"items": items, "next_cursor": None})

    engine.side_effect("GET", CHARACTERS, handler)


def answer_personas(engine: FakeEngine, *items: dict[str, Any]) -> None:
    listed = list(items) or [persona(ARI, "Ari", "alice")]
    engine.respond("GET", PERSONAS, 200, json={"items": listed, "next_cursor": None})


def answer_lookups(engine: FakeEngine, posters: list[dict[str, Any]] | None = None) -> None:
    """Everything the upload page reads: ratings, the picker's two sources, poster options."""
    answer_ratings(engine)
    answer_characters(engine)
    answer_personas(engine)
    listed = posters if posters is not None else [asset(id=POSTER, alt_text="A lighthouse")]
    engine.respond("GET", LIST, 200, json={"items": listed, "next_cursor": None})


def png(name: str = "sample.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, PNG, content_type="image/png")


def mp4() -> SimpleUploadedFile:
    return SimpleUploadedFile("clip.mp4", MP4, content_type="video/mp4")


def upload_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "idempotency_key": KEY,
        "kind": "image",
        "file": png(),
        "content_rating": TEEN,
        "subjects": [f"catalog_character:{ALDRIC}"],
        "subjects_typed": "",
        "alt_text": "  Sir Aldric  ",
        "caption": "",
        "owner": " alice\t",
        "poster": "",
    }
    data.update(changes)
    return {k: v for k, v in data.items() if v is not None}


def posts(engine: FakeEngine) -> list[httpx.Request]:
    return calls(engine, "POST", LIST)


def parts(request: httpx.Request) -> list[tuple[str, str | None, str, bytes]]:
    """The multipart body's parts: ``(name, filename, content type, bytes)`` in order."""
    head = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
    message = BytesParser(policy=policy.HTTP).parsebytes(head + request.content)
    found: list[tuple[str, str | None, str, bytes]] = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        found.append((str(name), part.get_filename(), part.get_content_type(), payload))
    return found


def metadata_of(request: httpx.Request) -> dict[str, Any]:
    name, filename, content_type, payload = parts(request)[0]
    assert (name, filename, content_type) == ("metadata", None, "application/json")
    body: dict[str, Any] = json.loads(payload)
    return body


def flashes(response: Any) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def refused_upload(
    client: Client, engine: FakeEngine, status: int, body: dict[str, Any], **changes: Any
) -> str:
    answer_lookups(engine)
    engine.respond("POST", LIST, status, json=body)
    response = client.post(UPLOAD, upload_data(**changes))
    assert response.status_code == 200
    assert len(posts(engine)) == 1
    return response.content.decode()


# --- upload ---------------------------------------------------------------------------------------


def test_upload_page_offers_the_picker_ratings_and_poster_ids(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    response = operator_client.get(f"{UPLOAD}?kind=video")
    html = response.content.decode()
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in html
    assert 'accept="image/*,video/*"' in html
    assert 'accept="image/*"' in html
    assert re.search(r'<input type="radio" name="kind" value="video"[^>]* checked>', html)
    assert '<optgroup label="Catalog characters">' in html
    assert f'value="catalog_character:{ALDRIC}">Sir Aldric</option>' in html
    assert f'value="catalog_character:{DRAFTY}">Drafty (draft only)</option>' in html
    assert f'value="persona:{ARI}">Ari (alice)</option>' in html
    assert '<option value="" selected>— choose —</option>' in html
    assert f'<option value="{POSTER}">A lighthouse</option>' in html
    assert 'list="poster-options"' in html
    assert 'value="console:nate"' in html  # owner pre-filled with the acting principal
    assert "needs ffmpeg on the Engine" in html
    (listed,) = calls(engine, "GET", LIST)
    assert listed.url.params["kind"] == "image"
    assert listed.url.params["limit"] == "50"
    statuses = sorted(r.url.params["status"] for r in calls(engine, "GET", CHARACTERS))
    assert statuses == ["draft", "published"]


def test_kind_is_not_preselected_without_the_query(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    html = operator_client.get(UPLOAD).content.decode()
    assert "checked" not in html


def test_upload_streams_metadata_and_file_and_records_no_bytes(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond("POST", LIST, 201, json=uploaded())
    data = upload_data(
        subjects=[f"persona:{ARI}", f"catalog_character:{ALDRIC}"],
        subjects_typed=f"{STORY_CHAR.lower()}\n\npersona:{ARI}\ncatalog_character:{DRAFTY}",
    )
    response = operator_client.post(UPLOAD, data)
    assert response.status_code == 302
    assert response["Location"] == DETAIL
    assert flashes(response) == [f"Uploaded image {MED1} — key 01JP00000000000000000KEY01"]
    (sent,) = posts(engine)
    assert sent.headers["Idempotency-Key"] == KEY
    assert sent.headers["Content-Type"].startswith("multipart/form-data")
    assert [(n, f, t) for n, f, t, _ in parts(sent)] == [
        ("metadata", None, "application/json"),
        ("file", "sample.png", "image/png"),
    ]
    assert parts(sent)[1][3] == PNG
    metadata = metadata_of(sent)
    assert metadata == {
        "kind": "image",
        "content_rating_id": TEEN,
        # picked in option order (catalog characters first), then typed in line order;
        # the typed persona duplicates a picked one and is dropped
        "depicted_subjects": [
            {"kind": "catalog_character", "id": ALDRIC},
            {"kind": "persona", "id": ARI},
            {"kind": "character", "id": STORY_CHAR},
            {"kind": "catalog_character", "id": DRAFTY},
        ],
        "owner": "alice",
        "alt_text": "Sir Aldric",
    }
    row = EngineOperation.objects.get()
    assert (row.kind, row.entity_type, row.entity_id) == ("upload_media", "media", MED1)
    assert row.request["body"] == {
        "metadata": metadata,
        "file": {"name": "sample.png", "size": len(PNG), "content_type": "image/png"},
        "poster_file": None,
    }
    stored = json.dumps(row.request)
    assert "PNG" not in stored
    assert PNG.decode("latin-1") not in stored
    assert KnownPrincipal.objects.filter(principal="alice").exists()


def test_a_video_sends_its_poster_file_after_the_file(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond("POST", LIST, 201, json=uploaded(id=MED2, kind="video", mime_type="video/mp4"))
    poster = SimpleUploadedFile("poster.png", PNG, content_type="image/png")
    response = operator_client.post(
        UPLOAD, upload_data(kind="video", file=mp4(), poster_file=poster)
    )
    assert response.status_code == 302
    assert flashes(response)[0].startswith(f"Uploaded video {MED2} — key ")
    (sent,) = posts(engine)
    assert [(n, f, t) for n, f, t, _ in parts(sent)] == [
        ("metadata", None, "application/json"),
        ("file", "clip.mp4", "video/mp4"),
        ("poster_file", "poster.png", "image/png"),
    ]
    assert "poster" not in metadata_of(sent)
    row = EngineOperation.objects.get()
    assert row.request["body"]["poster_file"] == {
        "name": "poster.png",
        "size": len(PNG),
        "content_type": "image/png",
    }


def test_a_poster_id_travels_as_metadata_poster(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond("POST", LIST, 201, json=uploaded(id=MED2, kind="video"))
    data = upload_data(kind="video", file=mp4(), poster=f" {POSTER.lower()} ", caption="Storm")
    operator_client.post(UPLOAD, data)
    metadata = metadata_of(posts(engine)[0])
    assert metadata["poster"] == POSTER
    assert metadata["caption"] == "Storm"


def test_no_subjects_without_depicts_nobody_makes_no_upload(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    response = operator_client.post(UPLOAD, upload_data(subjects=[]))
    html = response.content.decode()
    assert response.status_code == 200
    assert posts(engine) == []
    assert "Declare who this depicts, or tick “Depicts nobody”" in html


def test_depicts_nobody_sends_an_empty_list(operator_client: Client, engine: FakeEngine) -> None:
    """Complement: ticked, the same upload goes, with ``depicted_subjects: []``."""
    answer_lookups(engine)
    engine.respond("POST", LIST, 201, json=uploaded(depicted_subjects=[]))
    response = operator_client.post(UPLOAD, upload_data(subjects=[], depicts_nobody="on"))
    assert response.status_code == 302
    assert metadata_of(posts(engine)[0])["depicted_subjects"] == []


def test_depicts_nobody_with_subjects_is_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    html = operator_client.post(UPLOAD, upload_data(depicts_nobody="on")).content.decode()
    assert posts(engine) == []
    assert "Untick &#x27;Depicts nobody&#x27; or remove the subjects" in html


def test_a_typed_persona_line_is_a_persona(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("POST", LIST, 201, json=uploaded())
    data = upload_data(subjects=[], subjects_typed=f"  persona:{ARI}  \ncharacter:{STORY_CHAR}")
    operator_client.post(UPLOAD, data)
    assert metadata_of(posts(engine)[0])["depicted_subjects"] == [
        {"kind": "persona", "id": ARI},
        {"kind": "character", "id": STORY_CHAR},
    ]


def test_a_malformed_typed_line_is_named_and_makes_no_upload(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    data = upload_data(subjects_typed=f"persona:{ARI}\nscenario:{ARI}\nnot-an-id")
    html = unescape(text(operator_client.post(UPLOAD, data).content.decode()))
    assert posts(engine) == []
    assert f"Line 2 ('scenario:{ARI}')" in html
    assert "Line 3 ('not-an-id')" in html


def test_more_than_fifty_subjects_is_refused(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    ids = [f"01JS00000000000000000{n:05d}" for n in range(51)]
    data = upload_data(subjects=[], subjects_typed="\n".join([*ids, ids[0]]))
    html = operator_client.post(UPLOAD, data).content.decode()
    assert posts(engine) == []
    assert "At most 50 subjects (51 given)" in html


def test_fifty_subjects_after_duplicates_go(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("POST", LIST, 201, json=uploaded())
    ids = [f"01JS00000000000000000{n:05d}" for n in range(50)]
    data = upload_data(subjects=[], subjects_typed="\n".join([*ids, *ids]))
    assert operator_client.post(UPLOAD, data).status_code == 302
    assert len(metadata_of(posts(engine)[0])["depicted_subjects"]) == 50


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"poster": POSTER}, "Posters apply to videos only"),
        (
            {"poster_file": SimpleUploadedFile("p.png", PNG, content_type="image/png")},
            "Posters apply to videos only",
        ),
    ],
)
def test_a_poster_on_an_image_makes_no_upload(
    operator_client: Client, engine: FakeEngine, changes: dict[str, Any], message: str
) -> None:
    answer_lookups(engine)
    html = operator_client.post(UPLOAD, upload_data(**changes)).content.decode()
    assert posts(engine) == []
    assert message in html
    assert "Choose the file again" not in html  # the Console refused it, not the Engine


def test_a_poster_file_and_id_together_are_refused(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    poster = SimpleUploadedFile("p.png", PNG, content_type="image/png")
    data = upload_data(kind="video", file=mp4(), poster=POSTER, poster_file=poster)
    html = operator_client.post(UPLOAD, data).content.decode()
    assert posts(engine) == []
    assert "Choose a poster file or a poster id, not both" in html


def test_a_picked_subject_missing_from_the_list_is_kept(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    gone = "01JC00000000000000000XYZW1"
    data = upload_data(subjects=[f"catalog_character:{gone}"], kind="")
    html = operator_client.post(UPLOAD, data).content.decode()
    assert posts(engine) == []
    assert f'<option value="catalog_character:{gone}" selected>catalog_character …0XYZW1' in html
    assert "(missing)" in html


def test_a_picker_read_failure_leaves_typed_subjects_working(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    answer_characters(engine)
    engine.respond("GET", PERSONAS, 503, json=error("engine_unavailable"))
    engine.respond("GET", LIST, 503, json=error("media_store_unavailable"))
    html = operator_client.get(UPLOAD).content.decode()
    assert "Couldn&#x27;t load personas: 503 engine_unavailable" in html
    assert '<optgroup label="Personas">' not in html
    assert 'list="poster-options"' not in html  # the poster datalist is dropped silently
    engine.respond("POST", LIST, 201, json=uploaded())
    response = operator_client.post(UPLOAD, upload_data(subjects_typed=f"persona:{ARI}"))
    assert response.status_code == 302


def test_too_many_personas_offers_typing_instead(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    answer_characters(engine)
    many = [persona(f"01JP000000000000000000{n:04d}", f"P{n}", "bob") for n in range(200)]

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = int(request.url.params.get("cursor", "0"))
        return httpx.Response(200, json={"items": many, "next_cursor": str(cursor + 1)})

    engine.side_effect("GET", PERSONAS, handler)
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    html = operator_client.get(UPLOAD).content.decode()
    assert "Personas: Too many to list — enter the id below" in html
    assert len(calls(engine, "GET", PERSONAS)) == 3  # 600 read, past the 500 cap


def test_picker_labels_sort_by_name_and_disambiguate(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    answer_characters(
        engine,
        published=[character(ALDRIC, "Sir Aldric"), character(DRAFTY, "brenna")],
        draft=[character("01JC00000000000000000CHAR9", "Sir Aldric", "draft")],
    )
    answer_personas(engine)
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    html = operator_client.get(UPLOAD).content.decode()
    first = html.index(">brenna<")
    second = html.index(">Sir Aldric (…0CHAR1)<")
    third = html.index(">Sir Aldric (…0CHAR9) (draft only)<")
    assert first < second < third


def test_a_rating_read_failure_disables_upload(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", "/admin/content-ratings", 503, json=error("engine_unavailable"))
    answer_characters(engine)
    answer_personas(engine)
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    html = operator_client.get(UPLOAD).content.decode()
    assert "Ratings unavailable: 503 engine_unavailable — reload to retry" in html
    assert '<button type="submit" disabled>Upload</button>' in html
    response = operator_client.post(UPLOAD, upload_data())
    assert posts(engine) == []
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("status", "code", "details", "message"),
    [
        (
            413,
            "media_too_large",
            {"limit": 52428800, "size": 60000000},
            'This file is larger than the Engine accepts (<span title="52428800 bytes">'
            "50.0 MiB</span>).",
        ),
        (
            413,
            "use_multipart",
            {"size": 9000000, "threshold": 8388608},
            "This file is above the Engine&#x27;s single-upload size "
            '(<span title="8388608 bytes">8.0 MiB</span>); resumable uploads are not '
            "supported by the Console.",
        ),
        (413, "media_too_large", {}, "This file is larger than the Engine accepts."),
        (
            415,
            "media_type_not_allowed",
            {"reason": "kind_mismatch", "mime_type": "video/mp4"},
            "The file&#x27;s type doesn&#x27;t match image or isn&#x27;t allowed by the Engine",
        ),
        (
            422,
            "moderation_rejected",
            {"reason": "nudity"},
            "Rejected by the moderation hook: nudity",
        ),
        (422, "moderation_rejected", {}, "Rejected by the moderation hook: refused"),
        (
            422,
            "poster_required",
            {},
            "The Engine can&#x27;t extract a poster (no ffmpeg); add a poster file or id",
        ),
        (422, "poster_extraction_failed", {}, "Poster extraction failed; add a poster"),
        (
            409,
            "erased_subject_referenced",
            {"erasure_id": "01JE0000000000000000ERASE1", "match_count": 2},
            "A subject has been erased: refused",
        ),
        (
            503,
            "moderation_unavailable",
            {"reason": "timeout"},
            "The moderation hook did not answer; nothing was stored",
        ),
        (503, "media_store_unavailable", {}, "Media store unavailable; nothing was stored"),
    ],
)
def test_each_refusal_renders_its_message(
    operator_client: Client,
    engine: FakeEngine,
    status: int,
    code: str,
    details: dict[str, Any],
    message: str,
) -> None:
    html = refused_upload(operator_client, engine, status, error(code, **details))
    assert message in html
    assert f"<code>{code}</code>" in html
    assert "Choose the file again — browsers don&#x27;t keep it" in html
    assert "Engine error" not in html  # not the generic panel


def test_an_erased_subject_lists_its_details(operator_client: Client, engine: FakeEngine) -> None:
    body = error("erased_subject_referenced", erasure_id="01JE0000000000000000ERASE1")
    html = refused_upload(operator_client, engine, 409, body)
    assert "<dt>erasure_id</dt><dd>01JE0000000000000000ERASE1</dd>" in html


def test_a_poster_file_refused_by_415_is_prefixed(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = error("media_type_not_allowed", part="poster_file", reason="kind_mismatch")
    poster = SimpleUploadedFile("p.gif", b"GIF89a", content_type="image/gif")
    html = refused_upload(
        operator_client, engine, 415, body, kind="video", file=mp4(), poster_file=poster
    )
    assert (
        "Poster: The file&#x27;s type doesn&#x27;t match image or isn&#x27;t allowed by the Engine"
        in html
    )


@pytest.mark.parametrize(
    ("code", "field", "message"),
    [
        ("poster_invalid", "poster", "Not a live image asset"),
        ("media_retired", "poster", "This asset is retired"),
        ("rating_retired", "content_rating", "This rating is retired — choose another"),
    ],
)
def test_field_refusals_show_beside_their_field(
    operator_client: Client, engine: FakeEngine, code: str, field: str, message: str
) -> None:
    changes = {"kind": "video", "file": mp4(), "poster": POSTER}
    html = refused_upload(operator_client, engine, 422, error(code), **changes)
    section = html[html.index(f'name="{field}"') :]
    assert message in section[: section.index("</p>")]
    assert "Choose the file again" in html


def test_validation_errors_go_to_their_fields(operator_client: Client, engine: FakeEngine) -> None:
    body = error(
        "validation_error",
        errors=[
            {"field": "body.metadata.alt_text", "message": "too long", "type": "x"},
            {"field": "body.metadata.depicted_subjects[0].id", "message": "bad id", "type": "x"},
            {"field": "body.metadata.generation_metadata", "message": "odd", "type": "x"},
        ],
    )
    html = refused_upload(
        operator_client, engine, 422, body, subjects_typed=f"character:{STORY_CHAR}"
    )
    alt = html[html.index('id="f-alt_text"') :]
    assert "too long" in alt[: alt.index("</p>")]
    typed = html[html.index('id="f-subjects_typed"') :]
    assert "bad id" in typed[: typed.index("</p>")]
    assert "metadata.generation_metadata: odd" in html


def test_another_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    html = refused_upload(operator_client, engine, 401, error("unauthorized"))
    assert "Engine error 401 unauthorized" in html
    assert "Choose the file again" in html


# --- library --------------------------------------------------------------------------------------


def test_library_lists_thumbnails_through_the_proxy(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    no_poster = video(id=POSTER, poster_media_id=None, depicted_subjects=[])
    engine.respond(
        "GET", LIST, 200, json={"items": [asset(), video(), no_poster], "next_cursor": None}
    )
    response = operator_client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert f'src="/media/{MED1}/content/?img=1"' in html
    assert f'src="/media/{POSTER}/content/?img=1"' in html  # the video's poster
    assert "[video]" in html
    assert f'<span class="subject" title="{ALDRIC}">catalog_character …0CHAR1</span>' in html
    assert ">Teen<" in html
    assert f'href="/media/{MED1}/" title="{MED1}"' in html
    assert 'href="/media/upload/" target="_blank"' in html
    (listed,) = calls(engine, "GET", LIST)
    assert "state" not in listed.url.params


def test_show_retired_sends_state_retired(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    html = operator_client.get(f"{URL}?retired=1&kind=image&owner=alice").content.decode()
    (listed,) = calls(engine, "GET", LIST)
    assert listed.url.params["state"] == "retired"
    assert listed.url.params["kind"] == "image"
    assert listed.url.params["owner"] == "alice"
    assert "Retired media" in html
    assert "No media match these filters." in html


def test_empty_retired_library(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    assert "No retired media." in operator_client.get(f"{URL}?retired=1").content.decode()
    assert "No media." in operator_client.get(URL).content.decode()


def test_library_relation_filter_and_paging(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    engine.respond("GET", LIST, 200, json={"items": [asset()], "next_cursor": "c2"})
    html = operator_client.get(f"{URL}?relation=editor&relation_principal=bob").content.decode()
    (listed,) = calls(engine, "GET", LIST)
    assert listed.url.params.get_list("relation") == ["editor:bob"]
    assert "cursor=c2" in html
    assert not KnownPrincipal.objects.filter(principal="bob").exists()


def test_an_invalid_filter_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    response = operator_client.get(f"{URL}?relation=bad name")
    assert response.status_code == 200
    assert calls(engine) == []


def test_the_nav_links_to_the_library(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    assert 'href="/media/"' in operator_client.get(URL).content.decode()


# --- detail ---------------------------------------------------------------------------------------


def test_detail_shows_metadata_and_names_subjects(
    operator_client: Client, engine: FakeEngine
) -> None:
    subjects = [
        {"kind": "catalog_character", "id": ALDRIC},
        {"kind": "catalog_character", "id": DRAFTY},
        {"kind": "persona", "id": ARI},
        {"kind": "character", "id": STORY_CHAR},
    ]
    engine.respond("GET", ITEM, 200, json=asset(depicted_subjects=subjects, size=3_500_000))
    answer_ratings(engine)

    def card(request: httpx.Request) -> httpx.Response:
        character_id = request.url.path.rsplit("/", 1)[1]
        if character_id == DRAFTY and request.url.params.get("draft") != "true":
            return httpx.Response(422, json=error("not_published"))
        body = copy.deepcopy(engine_fixture("get_catalog_character_card"))
        body.update(id=character_id, name="Drafty" if character_id == DRAFTY else "Sir Aldric")
        return httpx.Response(200, json=body)

    engine.side_effect("GET", CHARACTER, card)
    engine.respond("GET", PERSONA, 200, json=persona(ARI, "Ari", "alice"))
    html = operator_client.get(DETAIL).content.decode()
    assert f'<img src="/media/{MED1}/content/?img=1" style="max-width: 640px"' in html
    assert f'<a class="original" href="/media/{MED1}/content/">Open original</a>' in html
    assert f'<a href="/catalog/characters/{ALDRIC}/">Sir Aldric</a>' in html
    assert f'<a href="/catalog/characters/{DRAFTY}/">Drafty</a>' in html
    assert f'<a href="/personas/{ARI}/">Ari</a>' in html
    assert f"<code>{STORY_CHAR}</code>" in html
    assert '<span title="3500000 bytes">3.3 MiB</span>' in html
    assert "1 \u00d7 1 px" in html
    assert "Teen" in html
    assert "01M5R0RTV00000000000000002" in html
    assert f'href="/media/{MED1}/retire/?kind=image"' in html
    assert "Retired</span>" not in html


def test_a_failed_subject_read_shows_the_id(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=asset())
    answer_ratings(engine)
    engine.respond("GET", CHARACTER, 404, json=error("not_found"))
    html = operator_client.get(DETAIL).content.decode()
    assert f'<a href="/catalog/characters/{ALDRIC}/">{ALDRIC}</a>' in html
    assert "(name unavailable: 404 not_found)" in html


def test_a_video_plays_with_its_poster(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=video(depicted_subjects=[]))
    engine.respond("GET", "/admin/content-ratings", 503, json=error("engine_unavailable"))
    html = operator_client.get(f"/media/{MED2}/").content.decode()
    assert (
        f'<video controls preload="metadata" src="/media/{MED2}/content/" '
        f'poster="/media/{POSTER}/content/?img=1"'
    ) in html
    assert "1:05.3" in html
    assert f'<a href="/media/{POSTER}/"><code>{POSTER}</code></a>' in html
    assert TEEN in html  # the rating read failed: the raw id
    assert "Nobody" in html


def test_a_retired_asset_has_no_retire_button(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=asset(state="retired", depicted_subjects=[]))
    answer_ratings(engine)
    html = operator_client.get(DETAIL).content.decode()
    assert 'class="tag retired">Retired</span>' in html
    assert "/retire/" not in html


def test_generation_metadata_shows_under_more(operator_client: Client, engine: FakeEngine) -> None:
    body = asset(depicted_subjects=[], generation_metadata={"model": "sdxl", "seed": 7})
    engine.respond("GET", ITEM, 200, json=body)
    answer_ratings(engine)
    html = operator_client.get(DETAIL).content.decode()
    assert "<summary>More</summary>" in html
    assert "sdxl" in html


# --- retire ---------------------------------------------------------------------------------------


def test_retire_confirm_makes_no_call(operator_client: Client, engine: FakeEngine) -> None:
    html = operator_client.get(f"{RETIRE}?kind=image").content.decode()
    assert calls(engine) == []
    assert "It leaves the library and can&#x27;t be newly attached" in html
    assert "A video&#x27;s poster is retired too." in html
    assert f'<img src="/media/{MED1}/content/?img=1"' in html
    assert f"<code>{MED1}</code>" in html
    assert "<code>image</code>" in html


def test_retire_deletes_and_returns_to_the_library(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("DELETE", ITEM, 204)
    page = operator_client.get(f"{RETIRE}?kind=image").content.decode()
    key = hidden(page, "idempotency_key")
    response = operator_client.post(RETIRE, {"idempotency_key": key, "kind": "image"})
    assert response.status_code == 302
    assert response["Location"] == URL
    assert flashes(response) == [f"Retired {MED1} — key {key}"]
    (sent,) = calls(engine, "DELETE")
    assert sent.headers["Idempotency-Key"] == key
    row = EngineOperation.objects.get()
    assert (row.kind, row.entity_type, row.entity_id) == ("retire_media", "media", MED1)


# --- helpers --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shown"),
    [(0, "0 bytes"), (1023, "1023 bytes"), (1536, "1.5 KiB"), (8388608, "8.0 MiB"), (-1, "—")],
)
def test_human_bytes(value: int, shown: str) -> None:
    assert human_bytes(value) == shown


@pytest.mark.parametrize(("ms", "shown"), [(None, "—"), (0, "0:00.0"), (65_300, "1:05.3")])
def test_duration(ms: int | None, shown: str) -> None:
    assert duration(ms) == shown
