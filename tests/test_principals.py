"""Look up everything linked to a principal (#76)."""

import copy
import re
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, unquote

import httpx
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.utils import timezone

from console.engine.client import EngineClient
from console.engine.models import PrincipalLinks
from console.engine_access import engine_client_for
from console.models import ActingPrincipal, KnownPrincipal
from console.templatetags.principals import principal_link
from console.views import principals
from console.views.principals import cursor_query, principal_url
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture
from tests.ratings.helpers import error, text

pytestmark = pytest.mark.django_db

LOOKUP = "/principals/{principal_id}"
INDEX = "/principals/"
ALICE = "/principals/alice/"


def ulid(suffix: str) -> str:
    return f"01JP0000000000000000{suffix}"


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine the principal pages talk to, through the real recording client."""
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(principals, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def lookup(name: str = "principals_lookup", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture(name))
    return {**body, **changes}


def lookups(engine: FakeEngine) -> list[httpx.Request]:
    return [r for r in engine.requests if r.url.raw_path.startswith(b"/principals/")]


def params(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params.multi_items())


def raw_path(request: httpx.Request) -> str:
    return request.url.raw_path.decode().split("?", 1)[0]


def linked_principals(html: str) -> list[str]:
    """The principals the page links to, in order (their URLs unquoted twice, as built)."""
    found = re.findall(r'<a href="/principals/([^"]+)/"><code>', html)
    return [unquote(unquote(p)) for p in found]


def section(html: str, anchor: str) -> str:
    """The HTML from the table heading ``anchor`` to the next heading."""
    start = html.index(f'id="{anchor}"')
    end = min(i for i in (html.find("<h3", start), html.find("<h2", start)) if i != -1)
    return html[start:end]


def href_of(html: str, css_class: str) -> str:
    match = re.search(rf'class="{css_class}" href="([^"]*)"', html)
    assert match, f"no {css_class} link"
    return match.group(1).replace("&amp;", "&")


# --- principal_url and principal_link -------------------------------------------------------------


def test_principal_url_quotes_the_principal_whole() -> None:
    assert principal_url("alice") == ALICE
    assert principal_url("team/alice") == "/principals/team%252Falice/"
    assert principal_link("system") == '<a href="/principals/system/"><code>system</code></a>'


def test_a_principal_with_a_slash_round_trips(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup(principal="team/alice"))
    response = operator_client.get(principal_url("team/alice"))
    assert response.status_code == 200
    (sent,) = lookups(engine)
    assert raw_path(sent) == "/principals/team%2Falice"
    html = response.content.decode()
    assert "Principal <code>team/alice</code>" in html
    assert 'name="other" value="team/alice"' in html


def test_a_principal_with_a_percent_round_trips(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup(principal="50%off"))
    assert operator_client.get(principal_url("50%off")).status_code == 200
    (sent,) = lookups(engine)
    assert raw_path(sent) == "/principals/50%25off"


def test_the_lookup_sends_the_acting_principal_and_limit(
    operator_client: Client, engine: FakeEngine, operator: Any
) -> None:
    ActingPrincipal.objects.create(user=operator, principal="bob")
    engine.respond("GET", LOOKUP, 200, json=lookup())
    operator_client.get(ALICE)
    (sent,) = lookups(engine)
    assert sent.headers["X-Epistrel-Principal"] == "bob"
    assert params(sent) == {"limit": "50"}


# --- /principals/ ---------------------------------------------------------------------------------


def test_the_lookup_form_defaults_to_the_acting_principal(
    operator_client: Client, operator: Any
) -> None:
    ActingPrincipal.objects.create(user=operator, principal="bob")
    html = operator_client.get(INDEX).content.decode()
    assert re.search(r'name="principal" value="bob"', html)
    assert 'list="dl-id_principal"' in html  # PrincipalField's datalist


def test_a_blank_lookup_is_no_lookup(operator_client: Client, engine: FakeEngine) -> None:
    for query in ("", "?principal=", "?principal=%20%09"):
        response = operator_client.get(INDEX + query)
        assert response.status_code == 200
        assert '<ul class="errorlist' not in response.content.decode()
    assert engine.requests == []


def test_a_lookup_is_trimmed_remembered_and_redirects(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(INDEX, {"principal": "  alice\t"})
    assert response.status_code == 302
    assert response["Location"] == ALICE
    assert KnownPrincipal.objects.filter(principal="alice").exists()
    assert engine.requests == []


def test_a_lookup_with_a_slash_redirects_to_the_quoted_page(operator_client: Client) -> None:
    response = operator_client.get(INDEX, {"principal": "team/alice"})
    assert response["Location"] == "/principals/team%252Falice/"


def test_looking_up_system_is_allowed_but_not_remembered(operator_client: Client) -> None:
    response = operator_client.get(INDEX, {"principal": "system"})
    assert response["Location"] == "/principals/system/"
    assert not KnownPrincipal.objects.filter(principal="system").exists()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("a\tb", "Principal contains a control character."),
        ("x" * 257, "Principal is longer than 256 characters."),
        ("a\nb", "Principal contains a control character."),
    ],
)
def test_an_invalid_lookup_shows_the_error_with_zero_calls(
    operator_client: Client, engine: FakeEngine, value: str, message: str
) -> None:
    for n in range(55):
        KnownPrincipal.objects.create(principal=f"p{n:02d}")
    response = operator_client.get(INDEX, {"principal": value, "page": "2"})
    assert response.status_code == 200
    html = response.content.decode()
    assert message in text(html)
    assert "Page 2 of 2" in html  # the known list keeps its page
    assert engine.requests == []
    assert KnownPrincipal.objects.count() == 55


def test_known_principals_order_own_first_then_recent_then_alphabetical(
    operator_client: Client,
) -> None:
    now = timezone.now()
    KnownPrincipal.objects.create(principal="old", last_used_at=now - timedelta(days=3))
    KnownPrincipal.objects.create(principal="b-recent", last_used_at=now)
    KnownPrincipal.objects.create(principal="a-recent", last_used_at=now)
    KnownPrincipal.objects.create(principal="B-recent", last_used_at=now)
    html = operator_client.get(INDEX).content.decode()
    table = html[html.index('class="known-principals"') :]
    order = linked_principals(table)
    assert order == ["console:nate", "B-recent", "a-recent", "b-recent", "old"]
    own_row = table[table.index('class="own"') :].split("</tr>")[0]
    assert "—" in own_row  # never used: no KnownPrincipal row for it


def test_own_principal_is_pinned_on_page_one_only(operator_client: Client) -> None:
    now = timezone.now()
    KnownPrincipal.objects.create(principal="console:nate", last_used_at=now)
    for n in range(51):
        KnownPrincipal.objects.create(
            principal=f"p{n:02d}", last_used_at=now - timedelta(minutes=n + 1)
        )
    first = operator_client.get(INDEX).content.decode()
    first_rows = linked_principals(first)
    assert first_rows[0] == "console:nate"
    assert len(first_rows) == 51  # own plus a page of 50
    assert "<time" in first[first.index('class="own"') :].split("</tr>")[0]
    second = operator_client.get(INDEX, {"page": "2"}).content.decode()
    assert linked_principals(second) == ["p50"]


# --- /principals/<p>/ -----------------------------------------------------------------------------


def test_opening_a_page_directly_remembers_nothing(
    operator_client: Client, engine: FakeEngine
) -> None:
    then = timezone.now() - timedelta(days=1)
    KnownPrincipal.objects.create(principal="alice", last_used_at=then)
    engine.respond("GET", LOOKUP, 200, json=lookup())
    operator_client.get(ALICE)
    operator_client.get(principal_url("carol"))
    assert KnownPrincipal.objects.get(principal="alice").last_used_at == then
    assert not KnownPrincipal.objects.filter(principal="carol").exists()


def test_surrounding_spaces_redirect_permanently_to_the_trimmed_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    response = operator_client.get(principal_url(" alice\t") + "?cursor%5Bstories%5D=c1")
    assert response.status_code == 301
    assert response["Location"] == f"{ALICE}?cursor%5Bstories%5D=c1"
    assert engine.requests == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("a\x01b", "Principal contains a control character."),
        ("x" * 257, "Principal is longer than 256 characters."),
    ],
)
def test_an_invalid_page_principal_shows_the_error_with_zero_calls(
    operator_client: Client, engine: FakeEngine, value: str, message: str
) -> None:
    response = operator_client.get(principal_url(value))
    assert response.status_code == 200
    assert message in text(response.content.decode())
    assert engine.requests == []


def test_owned_and_related_tables_in_order_with_counts(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()
    assert "Principal <code>alice</code>" in html
    assert "Owned (8)" in html
    assert "Related (4)" in html
    anchors = re.findall(r'<h3 id="([^"]+)"', html)
    kinds = ["stories", "characters", "scenarios", "personas", "media", "engine_profiles"]
    assert anchors == [f"owned-{k}" for k in kinds] + [f"related-{k}" for k in kinds]
    assert "Stories (3)" in section(html, "owned-stories")
    assert "None." in section(html, "related-characters")
    assert "nothing-linked" not in html


def test_links_per_type(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()
    owned = {
        "owned-stories": f'<a href="/stories/{ulid("STRY01")}/">The Lighthouse</a>',
        "owned-characters": f'<a href="/catalog/characters/{ulid("CHAR01")}/">Mara Voss</a>',
        "owned-scenarios": f'<a href="/catalog/scenarios/{ulid("SCEN01")}/">Harbour Night</a>',
        "owned-personas": f'<a href="/personas/{ulid("PERSA1")}/">Ari</a>',
        "owned-media": f'<a href="/media/{ulid("MEDIA1")}/">Harbour at dusk</a>',
    }
    for anchor, link in owned.items():
        assert link in section(html, anchor), anchor
    # Engine profiles have no Console page yet: the name and the id, unlinked.
    profiles = section(html, "owned-engine_profiles")
    assert f"Fast local <code>{ulid('PROF01')}</code>" in profiles
    assert "<a " not in profiles.split("</thead>")[1]
    related = {
        "related-stories": f'<a href="/stories/{ulid("STRY05")}/"><code>{ulid("STRY05")}</code>',
        "related-scenarios": (
            f'<a href="/catalog/scenarios/{ulid("SCEN02")}/"><code>{ulid("SCEN02")}</code>'
        ),
        "related-media": f'<a href="/media/{ulid("MEDIA2")}/"><code>{ulid("MEDIA2")}</code>',
    }
    for anchor, link in related.items():
        assert link in section(html, anchor), anchor
    assert "player, co_author" in section(html, "related-stories")
    assert "editor" in section(html, "related-scenarios")


def test_columns_per_type(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()

    def headers(anchor: str) -> list[str]:
        return re.findall(r"<th>([^<]+)</th>", section(html, anchor))

    assert headers("owned-stories") == ["Name", "Status"]
    assert headers("owned-characters") == ["Name", "Status", "Version", "Listing status"]
    assert headers("owned-scenarios") == ["Name", "Status", "Version", "Listing status"]
    assert headers("owned-personas") == ["Name"]
    assert headers("owned-media") == ["Name", "Status"]
    assert headers("owned-engine_profiles") == ["Name", "Status"]
    assert headers("related-media") == ["Id", "Relations"]
    characters = text(section(html, "owned-characters"))
    assert "Mara Voss published 2 listed" in characters
    assert "Harbour Night draft — unlisted" in text(section(html, "owned-scenarios"))
    assert "blocked_by_erasure" in section(html, "owned-stories")  # raw status text


def test_a_character_s_companion_is_nested_under_it(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()
    characters = section(html, "owned-characters")
    row = characters.index(f'id="item-{ulid("CHAR01")}"')
    companion = characters.index('class="companion"')
    assert row < companion
    scenario = ulid("SCEN09")
    assert (
        f'Companion: <a href="/catalog/scenarios/{scenario}/"><code>{scenario}</code></a>'
        in characters
    )


def test_a_held_item_shows_held_and_is_unlinked(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()
    stories = section(html, "owned-stories")
    held = ulid("STRY02")
    row = stories[stories.index(f'id="item-{held}"') :].split("</tr>")[0]
    assert f"(held — erasure pending) <code>{held}</code>" in row
    assert "<a " not in row
    assert f"/stories/{held}/" not in html


def test_an_unknown_principal_shows_empty_tables_and_the_message(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup("principals_lookup_empty"))
    html = operator_client.get(principal_url("nobody")).content.decode()
    assert "The Engine has nothing linked to <code>nobody</code>" in html
    assert html.count("None.") == 12
    assert "Owned (0)" in html
    assert "Related (0)" in html


def test_a_type_the_engine_left_out_says_so(operator_client: Client, engine: FakeEngine) -> None:
    body = lookup("principals_lookup_empty")
    del body["related"]["media"]
    body["owned"]["gadgets"] = {"count": 1}
    body["owned"]["stories"]["items"] = [
        {
            "id": ulid("STRY01"),
            "name": "Kept",
            "status": "active",
            "version": None,
            "listing_status": None,
            "shelf": "top",
        }
    ]
    body["owned"]["stories"]["count"] = 1
    body["audit"] = {"seen": True}
    engine.side_effect("GET", LOOKUP, lambda request: httpx.Response(200, json=body))
    html = operator_client.get(principal_url("nobody")).content.decode()
    assert "Not returned by the Engine." in section(html, "related-media")
    assert "nothing-linked" not in html
    more = html[html.index('<details class="more">') :]
    for key in ("owned.gadgets", "audit", f"owned.stories.{ulid('STRY01')}", "shelf"):
        assert key in more


def test_principal_links_parses_the_lookup() -> None:
    links = PrincipalLinks.from_json(lookup())
    assert links is not None
    assert set(links.owned) == set(links.related) == set(principals.ENGINE_LOOKUP_KEYS)
    character = links.owned["characters"].items[0]
    assert character.companion is not None
    assert character.companion.id == ulid("SCEN09")
    assert links.owned["stories"].items[1].name is None
    assert links.more == {}
    assert PrincipalLinks.from_json({"principal": "a", "owned": {}, "related": []}) is None
    bad = lookup()
    bad["owned"]["stories"]["count"] = "three"
    assert PrincipalLinks.from_json(bad) is None


def test_system_may_be_looked_up_without_act_as_or_offboard(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup("principals_lookup_empty", principal="system"))
    with override_settings(ROOT_URLCONF="tests.urls_m3"):
        html = operator_client.get("/principals/system/").content.decode()
    assert "Principal <code>system</code>" in html
    assert "Act as this principal" not in html
    assert "Offboard…" not in html
    assert len(lookups(engine)) == 1


def test_act_as_and_offboard(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    with override_settings(ROOT_URLCONF="tests.urls_m3"):
        html = operator_client.get(ALICE).content.decode()
        assert href_of(html, "button danger offboard") == "/offboarding/new/?principal=alice"
        assert href_of(html, "offboarding") == "/offboarding/"
    form = html[html.index('<form method="post" action="/acting-as/" class="act-as">') :]
    form = form.split("</form>")[0]
    assert 'name="other" value="alice"' in form
    assert f'name="next" value="{ALICE}"' in form
    response = operator_client.post("/acting-as/", {"other": "alice", "next": ALICE})
    assert response["Location"] == ALICE
    assert ActingPrincipal.objects.get().principal == "alice"
    html = operator_client.get(ALICE).content.decode()
    assert "Act as this principal" not in html  # already acting as them


def test_offboard_is_hidden_while_its_page_does_not_exist(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()
    assert "Act as this principal" in html
    assert "Offboard…" not in html


# --- cursors --------------------------------------------------------------------------------------


def test_each_table_pages_on_its_own_cursor(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    html = operator_client.get(ALICE).content.decode()
    stories_next = href_of(section(html, "owned-stories"), "next-page")
    media_next = href_of(section(html, "related-media"), "next-page")
    assert stories_next == "?cursor%5Bstories%5D=c-stories-2#owned-stories"
    assert media_next == "?cursor%5Brelated.media%5D=c-rmedia-2#related-media"
    assert "first-page" not in html  # every table is on its first page


def test_paging_one_table_keeps_the_others_cursor(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup("principals_lookup_stories_p2"))
    query = {"cursor[related.media]": "c-rmedia-2", "cursor[stories]": "c-stories-2"}
    html = operator_client.get(ALICE, query).content.decode()
    (sent,) = lookups(engine)
    assert params(sent) == {"limit": "50", **query}
    assert "Second Harbour" in section(html, "owned-stories")
    # Stories' "First page" drops only its own cursor; media's keeps the stories position.
    first = href_of(section(html, "owned-stories"), "first-page")
    assert first == "?cursor%5Brelated.media%5D=c-rmedia-2#owned-stories"
    media_first = href_of(section(html, "related-media"), "first-page")
    assert dict(parse_qsl(media_first[1:].split("#")[0])) == {"cursor[stories]": "c-stories-2"}
    # Media's "Next" (the fixture still offers one) keeps the stories cursor too.
    media_next = href_of(section(html, "related-media"), "next-page")
    assert dict(parse_qsl(media_next[1:].split("#")[0])) == query
    assert "next-page" not in section(html, "owned-stories")  # last page of stories


def test_cursor_query_helper() -> None:
    current = {"stories": "s", "related.media": "m", "bogus": "x", "media": ""}
    assert cursor_query(current, "stories", None) == "?cursor%5Brelated.media%5D=m#owned-stories"
    assert cursor_query(current, "media", "n") == (
        "?cursor%5Bstories%5D=s&cursor%5Bmedia%5D=n&cursor%5Brelated.media%5D=m#owned-media"
    )
    assert cursor_query({}, "related.engine_profiles", None) == "?#related-engine_profiles"


def test_only_known_non_blank_cursors_are_forwarded(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    query = {"cursor[media]": "", "cursor[gadgets]": "g", "cursor": "c", "cursor[personas]": "p"}
    html = operator_client.get(ALICE, query).content.decode()
    (sent,) = lookups(engine)
    assert params(sent) == {"limit": "50", "cursor[personas]": "p"}
    assert "stale-cursor" not in html


def test_an_over_long_cursor_is_dropped_locally_with_the_notice(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 200, json=lookup())
    query = {"cursor[stories]": "x" * 257, "cursor[media]": "m"}
    html = operator_client.get(ALICE, query).content.decode()
    (sent,) = lookups(engine)
    assert params(sent) == {"limit": "50", "cursor[media]": "m"}
    assert 'class="banner stale-cursor" role="status"' in html
    assert "A page link was stale — showing first pages." in html


def test_a_stale_cursor_falls_back_to_first_pages(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond_sequence(
        "GET",
        LOOKUP,
        [
            {"status": 422, "json": engine_fixture("error_cursor_invalid")},
            {"status": 200, "json": lookup()},
        ],
    )
    html = operator_client.get(
        ALICE, {"cursor[stories]": "stale", "cursor[related.media]": "m"}
    ).content.decode()
    first, retry = lookups(engine)
    assert params(first) == {
        "limit": "50",
        "cursor[stories]": "stale",
        "cursor[related.media]": "m",
    }
    assert params(retry) == {"limit": "50"}
    assert "A page link was stale — showing first pages." in html
    assert "The Lighthouse" in html
    assert "first-page" not in html  # the links start from first pages too


def test_a_second_stale_cursor_failure_shows_the_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", LOOKUP, 422, json=engine_fixture("error_cursor_invalid"))
    html = operator_client.get(ALICE, {"cursor[stories]": "stale"}).content.decode()
    assert len(lookups(engine)) == 2
    assert "Engine error 422 cursor_invalid" in html
    assert "showing first pages" not in html


@pytest.mark.parametrize(
    ("status", "body"),
    [(422, error("validation_error", "limit too large")), (404, error("not_found"))],
)
def test_other_errors_show_the_panel_without_a_retry(
    operator_client: Client, engine: FakeEngine, status: int, body: dict[str, Any]
) -> None:
    engine.respond("GET", LOOKUP, status, json=body)
    html = operator_client.get(ALICE, {"cursor[stories]": "c"}).content.decode()
    assert len(lookups(engine)) == 1
    assert f"Engine error {status} {body['error']['code']}" in html
