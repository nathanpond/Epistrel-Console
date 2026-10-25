"""/catalog/characters/ (#65): the list and its filters, a character's detail in every view, and
its versions."""

import re
from urllib.parse import quote

import pytest
from django.test import Client

from console.models import EngineOperation
from tests.catalog.helpers import (
    CHAR,
    CHAR2,
    DETAIL,
    ITEM,
    LIST,
    MED1,
    MED2,
    MED3,
    MED4,
    RATINGS,
    SCENARIO,
    SETTINGS,
    TEEN,
    URL,
    VERSIONS,
    answer_lookups,
    answer_ratings,
    answer_settings,
    calls,
    card,
    draft,
    error,
    full,
    item_reads,
    list_item,
    list_params,
    listing,
    text,
)
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_one_relation, shows_panel
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db


def row(html: str, character_id: str) -> str:
    match = re.search(rf'<tr id="character-{character_id}">(.*?)</tr>', html, re.S)
    assert match, f"no row for {character_id}"
    return match.group(1)


# --- List ----------------------------------------------------------------------------------------


def test_filters_map_to_the_query(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    query = {
        "q": "knight",
        "label": "fantasy, knight ,fantasy",
        "content_rating": TEEN,
        "listing_status": "public",
        "owner": " alice\t",
        "relation": "fan",
        "relation_principal": "bob",
        "status": "draft",
    }
    response = operator_client.get(URL, query)
    assert response.status_code == 200
    assert sorted(list_params(engine)) == sorted(
        [
            ("limit", "50"),
            ("q", "knight"),
            ("label", "fantasy"),
            ("label", "knight"),
            ("content_rating", TEEN),
            ("listing_status", "public"),
            ("owner", "alice"),
            ("relation", "fan:bob"),
            ("status", "draft"),
        ]
    )
    # GET filters never add to the known principals (#64).
    from console.models import KnownPrincipal

    assert not KnownPrincipal.objects.exists()


def test_the_default_status_is_not_sent_and_a_bare_relation_ends_with_a_colon(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    operator_client.get(URL, {"relation": "fan", "status": "published"})
    assert sorted(list_params(engine)) == [("limit", "50"), ("relation", "fan:")]


def test_archived_status_is_sent(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    operator_client.get(URL, {"status": "archived"})
    assert ("status", "archived") in list_params(engine)


def test_the_list_columns(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL).content.decode()
    first = row(html, CHAR)
    back = quote("/catalog/characters/", safe="")
    assert f'<a href="/catalog/characters/{CHAR}/?back={back}">Sir Aldric</a>' in first
    assert '<a class="label" href="/catalog/characters/?label=fantasy">fantasy</a>' in first
    assert "Teen" in first
    assert "public" in first
    assert "<code>console:nate</code>" in first
    assert 'href="/catalog/characters/?owner=console%3Anate">filter</a>' in first
    assert text(first).endswith("2")  # the latest version
    # No rank column unless searching.
    assert "<th>Rank</th>" not in html
    second = row(html, CHAR2)
    assert "(unnamed) …0CHAR2" in second
    assert text(second).split(" ")[-1] == "—"  # never published: no version
    assert "<img" not in html  # list rows have no thumbnail


def test_rank_shows_when_searching(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL, {"q": "knight"}).content.decode()
    assert "<th>Rank</th>" in html
    assert "<td>0.877</td>" in row(html, CHAR)
    assert "Searches published cards only" in html


def test_next_page_keeps_the_filters(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing(next_cursor="CURSOR-2"))
    html = operator_client.get(URL, {"q": "knight", "label": "fantasy"}).content.decode()
    assert '<a href="?q=knight&amp;label=fantasy&amp;cursor=CURSOR-2">Next page</a>' in html
    assert "First page" not in html
    engine.reset()
    engine.respond("GET", LIST, 200, json=listing())
    paged = operator_client.get(
        URL, {"q": "knight", "label": "fantasy", "cursor": "CURSOR-2"}
    ).content.decode()
    params = list_params(engine)
    assert ("cursor", "CURSOR-2") in params
    assert ("q", "knight") in params
    assert ("label", "fantasy") in params
    assert '<a href="?q=knight&amp;label=fantasy">First page</a>' in paged
    assert "Next page" not in paged


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({"label": "ok, bad!label"}, "Labels are 1 to 40 letters"),
        ({"label": ",".join(f"l{n}" for n in range(31))}, "At most 30 labels"),
        ({"q": "x" * 201}, "at most 200 characters"),
        ({"relation": "fan club"}, "A relation name is 1 to 64"),
        ({"relation_principal": "bob"}, "Enter a relation name"),
        ({"owner": "a\nb"}, "Principal contains a control character."),
    ],
)
def test_invalid_filters_make_no_list_call(
    operator_client: Client, engine: FakeEngine, query: dict[str, str], message: str
) -> None:
    answer_lookups(engine)
    html = operator_client.get(URL, query).content.decode()
    assert message in html
    assert calls(engine, "GET", LIST) == []


def test_system_is_a_valid_owner_filter(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    operator_client.get(URL, {"owner": "system"})
    assert ("owner", "system") in list_params(engine)


def test_rating_and_listing_lookups_fill_the_selects(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL).content.decode()
    assert f'<option value="{TEEN}">Teen</option>' in html
    assert '<option value="in_review">in_review</option>' in html
    (ratings_read,) = calls(engine, "GET", RATINGS)
    assert dict(ratings_read.url.params) == {"include_retired": "1"}


def test_a_failed_rating_read_keeps_the_filter(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", RATINGS, 503, json=error("unavailable", "database down"))
    answer_settings(engine)
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL, {"content_rating": TEEN}).content.decode()
    assert "Rating filter unavailable: 503 unavailable" in html
    assert f'<input type="hidden" name="content_rating" value="{TEEN}"' in html
    assert ("content_rating", TEEN) in list_params(engine)


def test_a_failed_settings_read_makes_listing_free_text(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    engine.respond("GET", SETTINGS, 503, json=error("unavailable", "database down"))
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL, {"listing_status": "featured"}).content.decode()
    assert "Listing values unavailable: 503 unavailable" in html
    assert '<input type="text" name="listing_status" value="featured"' in html
    assert ("listing_status", "featured") in list_params(engine)


def test_empty_states(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json={"items": [], "next_cursor": None})
    assert "No characters yet." in operator_client.get(URL).content.decode()
    filtered = operator_client.get(URL, {"label": "nothing"}).content.decode()
    assert "No characters match these filters." in filtered
    assert "No characters yet." not in filtered


def test_a_list_error_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 422, json=error("validation_failed", "bad cursor"))
    html = operator_client.get(URL, {"cursor": "nope"}).content.decode()
    assert "Engine error 422 validation_failed" in html


def test_the_nav_links_characters(operator_client: Client, engine: FakeEngine) -> None:
    answer_lookups(engine)
    engine.respond("GET", LIST, 200, json=listing())
    html = operator_client.get(URL).content.decode()
    assert '<a href="/catalog/characters/" class="active">Characters</a>' in html


# --- Detail --------------------------------------------------------------------------------------


def answer_detail(engine: FakeEngine, *answers: dict[str, object]) -> None:
    """``GET /catalog/characters/{id}`` answered in order (latest, then the draft read): a
    body, or ``{"status", "json"}`` for an error."""
    engine.respond_sequence(
        "GET", ITEM, [dict(a) if "json" in a else {"status": 200, "json": a} for a in answers]
    )


def test_the_detail_reads_full_and_shows_every_section(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(engine, full(), draft())
    html = operator_client.get(DETAIL).content.decode()
    reads = item_reads(engine)
    assert reads[0] == {"projection": "full"}
    assert reads[1] == {"projection": "full", "draft": "true"}
    page = text(html)
    assert "Version 2 (latest)" in page
    for section in ("card", "definition", "metadata", "visual-identity", "companion"):
        assert f'<section id="{section}">' in html
    order = [html.index(f'<section id="{s}">') for s in ("card", "definition", "metadata")]
    assert order == sorted(order)
    assert "A knight errant.<br>Sworn to no one." in html
    assert "Stoic but kind." in html
    assert "Changes the subject to the weather." in html
    # The five axes: setting + nuance, a missing one "not set".
    assert "threat response stands unless outnumbered" in page
    assert "honesty very honest" in page
    assert "caution not set" in page
    assert "Aldric, The Grey Knight" in page
    assert (
        f'<a href="/guardrails/ratings/{TEEN}/" title="01M5R0RTV00000000000000002">Teen</a>' in html
    )
    assert "&quot;lora&quot;: &quot;knight-v2&quot;" in html  # adapter params, pretty JSON
    # #74: the companion's card read names its link (and "Edit companion" on the latest view).
    assert f'href="/catalog/scenarios/{SCENARIO}/">Ada&#x27;s world</a>' in html
    assert f'href="/catalog/scenarios/{SCENARIO}/edit/">Edit companion</a>' in html
    assert "Draft revision" not in page  # only on draft views
    assert '<a href="/catalog/characters/">Back to list</a>' not in html  # class attr present
    assert 'class="back" href="/catalog/characters/"' in html
    assert f'<a href="/catalog/characters/{CHAR}/versions/">Versions</a>' in html


def test_images_go_through_the_proxy(operator_client: Client, engine: FakeEngine) -> None:
    answer_detail(engine, full(), draft())
    html = operator_client.get(DETAIL).content.decode()
    # #71: each image links to its library page, labelled by slot.
    assert (
        f'<a href="/media/{MED1}/"><img src="/media/{MED1}/content/?img=1" '
        'alt="Sir Aldric in armour"></a>'
    ) in html
    assert 'class="media-shown media-large"' in html  # the primary: up to 320 px
    # A video renders as <video> through the proxy, without ?img=1.
    assert f'<video controls preload="metadata" src="/media/{MED2}/content/">' in html
    assert f'src="/media/{MED2}/content/?img=1"' not in html
    # Reference images, labelled by audience, the restricted one marked.
    assert f'src="/media/{MED3}/content/?img=1" alt="Reference front"' in html
    assert "Reference — perceivable" in html
    restricted = html[html.index(f'id="media-reference-restricted-{MED4}"') :]
    assert '<strong class="restricted">restricted</strong>' in restricted[:400]
    assert f'src="/media/{MED4}/content/?img=1" alt=""' in restricted  # no alt text in the read
    assert "img-1" in html  # generation metadata
    assert "&quot;seed&quot;: 7" in html
    assert "512\u00d7768" in html
    assert "engine.test" not in html


def test_the_card_view_reads_card_and_hides_the_definition(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=card())
    html = operator_client.get(DETAIL, {"projection": "card"}).content.decode()
    assert item_reads(engine) == [{"projection": "card"}]
    assert "Public card view" in html
    assert '<a href="?">Back to full view</a>' in html
    # Complement: no definition field (nor visual identity, owner) on the card view.
    for private in (
        'id="definition"',
        "True age",
        "Stoic but kind",
        "Dispositions",
        "Safe fallback",
        'id="visual-identity"',
        "Owner",
    ):
        assert private not in html
    # #74: the companion link shows on every view; "Edit companion" not on the card view.
    assert f'href="/catalog/scenarios/{SCENARIO}/">Ada&#x27;s world</a>' in html
    assert "Edit companion" not in html
    assert "A knight errant." in html
    assert f'src="/media/{MED1}/content/?img=1"' in html


def test_the_card_view_keeps_the_current_view(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=card(version=1))
    operator_client.get(DETAIL, {"projection": "card", "version": "1"})
    assert item_reads(engine) == [{"projection": "card", "version": "1"}]


def test_a_never_published_entry_shows_its_draft(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(
        engine,
        {"status": 422, "json": error("not_published", "never published")},
        draft(version=None, current_version=None, published_at=None, status="draft"),
    )
    html = operator_client.get(DETAIL).content.decode()
    assert item_reads(engine) == [
        {"projection": "full"},
        {"projection": "full", "draft": "true"},
    ]
    assert "Draft — never published" in html
    assert "Draft revision" in text(html)
    assert "The draft differs" not in html


def test_a_never_published_card_view_falls_back_too(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(
        engine,
        {"status": 422, "json": error("not_published", "never published")},
        card(version=None, status="draft"),
    )
    html = operator_client.get(DETAIL, {"projection": "card"}).content.decode()
    assert item_reads(engine) == [
        {"projection": "card"},
        {"projection": "card", "draft": "true"},
    ]
    assert "Draft — never published (public card view)" in html


def test_version_n_requests_that_version(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=full(version=1))
    html = operator_client.get(DETAIL, {"version": "1"}).content.decode()
    assert item_reads(engine) == [{"projection": "full", "version": "1"}]
    heading = re.search(r'<h2 class="version-heading">(.*?)</h2>', html, re.S)
    assert heading
    assert text(heading.group(1)).startswith("Version 1 ·")
    assert "The draft differs" not in html


def test_version_n_that_is_current_says_latest(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=full())
    html = operator_client.get(DETAIL, {"version": "2"}).content.decode()
    assert "Version 2 (latest)" in html


def test_view_draft_wins_over_version(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=draft())
    html = operator_client.get(DETAIL, {"view": "draft", "version": "2"}).content.decode()
    assert item_reads(engine) == [{"projection": "full", "draft": "true"}]
    assert "Draft, revision 5" in html
    assert "Draft revision" in text(html)


def test_a_bad_version_shows_the_engines_refusal(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 422, json=error("validation_failed", "version must be an integer"))
    html = operator_client.get(DETAIL, {"version": "abc"}).content.decode()
    assert item_reads(engine) == [{"projection": "full", "version": "abc"}]
    assert "Engine error 422 validation_failed" in html
    assert 'class="back" href="/catalog/characters/"' in html


def test_unknown_view_and_projection_are_ignored(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(engine, full(), draft())
    html = operator_client.get(DETAIL, {"view": "weird", "projection": "huge"}).content.decode()
    assert item_reads(engine)[0] == {"projection": "full"}
    assert "Version 2 (latest)" in html


def test_draft_differs_note_when_content_differs(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(engine, full(), draft(name="Sir Aldric the Bold"))
    html = operator_client.get(DETAIL).content.decode()
    assert (
        'The draft differs from this published version — <a href="?view=draft">view the draft</a>'
    ) in html


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"aliases": ["Aldric", "The Grey Knight"], "draft_revision": 99},
        # null, "" and [] are equal, and a missing key equals them.
        {"public_description": "A knight errant.\nSworn to no one.", "status": "archived"},
    ],
)
def test_draft_differs_note_absent_when_equal(
    operator_client: Client, engine: FakeEngine, change: dict[str, object]
) -> None:
    latest = full()
    latest["definition"]["background_flavor"] = None
    latest["definition"]["interaction_style"]["mannerisms"] = []
    same = draft(**change)
    same["definition"]["background_flavor"] = ""
    del same["definition"]["interaction_style"]["mannerisms"]
    answer_detail(engine, latest, same)
    html = operator_client.get(DETAIL).content.decode()
    assert "The draft differs" not in html


def test_draft_differs_compares_images_by_media_id(
    operator_client: Client, engine: FakeEngine
) -> None:
    changed = draft()
    changed["images"]["primary"]["caption"] = "A new caption"  # not compared
    answer_detail(engine, full(), changed)
    assert "The draft differs" not in operator_client.get(DETAIL).content.decode()
    engine.reset()
    swapped = draft()
    swapped["images"]["gallery"] = []
    answer_detail(engine, full(), swapped)
    assert "The draft differs" in operator_client.get(DETAIL).content.decode()


def test_a_failed_draft_read_omits_the_note(operator_client: Client, engine: FakeEngine) -> None:
    answer_detail(engine, full(), {"status": 503, "json": error("unavailable", "down")})
    html = operator_client.get(DETAIL).content.decode()
    assert "The draft differs" not in html
    assert "Engine error" not in html
    assert "Version 2 (latest)" in html


def test_the_note_is_only_on_the_latest_full_view(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=card())
    operator_client.get(DETAIL, {"projection": "card"})
    assert len(item_reads(engine)) == 1  # no draft read on the card view


def test_back_to_list_carries_a_safe_back(operator_client: Client, engine: FakeEngine) -> None:
    answer_detail(engine, full(), draft())
    back = "/catalog/characters/?label=fantasy"
    html = operator_client.get(DETAIL, {"back": back}).content.decode()
    assert 'class="back" href="/catalog/characters/?label=fantasy"' in html
    for unsafe in ("https://evil.example/catalog/characters/", "/stories/", "//evil.example/"):
        engine.reset()
        answer_detail(engine, full(), draft())
        html = operator_client.get(DETAIL, {"back": unsafe}).content.decode()
        assert 'class="back" href="/catalog/characters/"' in html


def test_reads_write_no_operation_rows(operator_client: Client, engine: FakeEngine) -> None:
    answer_detail(engine, full(), draft())
    operator_client.get(DETAIL)
    assert not EngineOperation.objects.exists()


# --- Versions ------------------------------------------------------------------------------------


def test_the_versions_list(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", VERSIONS, 200, json=engine_fixture("list_catalog_character_versions"))
    engine.respond("GET", ITEM, 200, json=card())
    html = operator_client.get(f"/catalog/characters/{CHAR}/versions/").content.decode()
    assert item_reads(engine) == [{"projection": "card"}]
    assert "Versions: Sir Aldric" in html
    rows = re.findall(r'<tr id="version-(\d+)">(.*?)</tr>', html, re.S)
    assert [n for n, _ in rows] == ["2", "1"]  # newest first
    newest, oldest = (text(r) for _, r in rows)
    assert newest.startswith("Version 2 (latest) 2026-10-20 10:00:00 UTC alice")
    assert "(latest)" not in oldest
    assert f'<a href="/catalog/characters/{CHAR}/?version=1">Version 1</a>' in html
    assert f'href="/catalog/characters/{CHAR}/?view=draft">View the draft</a>' in html


def test_a_never_published_entry_has_no_versions(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", VERSIONS, 200, json={"items": []})
    engine.respond("GET", ITEM, 422, json=error("not_published", "never published"))
    html = operator_client.get(f"/catalog/characters/{CHAR}/versions/").content.decode()
    assert "Never published." in html
    assert "Engine error" not in html


def test_a_failed_card_read_marks_the_highest_version(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", VERSIONS, 200, json=engine_fixture("list_catalog_character_versions"))
    engine.respond("GET", ITEM, 503, json=error("unavailable", "down"))
    html = operator_client.get(f"/catalog/characters/{CHAR}/versions/").content.decode()
    assert f"Versions: <code>{CHAR}</code>" in html
    assert "Engine error 503 unavailable" in html
    assert html.index("Engine error 503") < html.index('<table class="versions">')
    assert "(latest)" in text(row_html(html, "2"))
    assert "(latest)" not in text(row_html(html, "1"))


def row_html(html: str, version: str) -> str:
    match = re.search(rf'<tr id="version-{version}">(.*?)</tr>', html, re.S)
    assert match
    return match.group(1)


def test_an_unknown_character_shows_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", VERSIONS, 404, json=error("not_found", "no such character"))
    html = operator_client.get(f"/catalog/characters/{CHAR}/versions/").content.decode()
    assert "Engine error 404 not_found" in html


def test_fixture_list_item_is_valid(engine: FakeEngine) -> None:
    """The fixtures validate against the recorded schema (#34) when served."""
    engine.respond("GET", LIST, 200, json=listing(list_item(rank=None)))
    engine.respond("GET", ITEM, 200, json=full())
    engine.respond("GET", ITEM, 200, json=card())


def test_the_detail_links_erase_for_the_catalog_scope(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#86: "Erase…" pre-fills the erasure form for this catalog character, catalog scope."""
    answer_detail(engine, full(), draft())
    html = operator_client.get(DETAIL).content.decode()
    assert (
        '<a class="button danger erase" href="/erasure/new/?kind=catalog_character&amp;'
        f'subject={CHAR}&amp;scope=catalog">Erase…</a>'
    ) in html


def test_the_detail_includes_the_ownership_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    """#79: the panel on the full view (a character with a companion says it moves along)."""
    answer_detail(engine, full(), draft())
    answer_one_relation(engine, "character", CHAR)
    html = operator_client.get(DETAIL).content.decode()
    assert shows_panel(html, "character", CHAR)
    assert '<input type="hidden" name="has_companion" value="1">' in html


def test_the_public_card_view_has_no_ownership_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=engine_fixture("get_catalog_character_card"))
    html = operator_client.get(f"{DETAIL}?projection=card").content.decode()
    assert 'id="ownership"' not in html
    assert not [r for r in engine.requests if r.url.path.endswith("/relations")]
