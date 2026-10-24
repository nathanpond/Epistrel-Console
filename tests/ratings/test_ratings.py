"""/guardrails/ratings/ (#49): the list, a rating's detail and its versions."""

import re

import pytest
from django.test import Client

from tests.fakes.engine import FakeEngine
from tests.ratings.helpers import (
    EVERYONE,
    EXPLICIT,
    EXPLICIT_POLICY,
    ITEM,
    LIST,
    MATURE,
    MIXED,
    SETTINGS,
    TEEN,
    TEEN_POLICY,
    URL,
    VERSIONS,
    answer_policies,
    answer_ratings,
    answer_settings,
    answer_versions,
    calls,
    detail,
    error,
    rating,
    rating_policy,
    text,
)

pytestmark = pytest.mark.django_db


def row(html: str, rating_id: str) -> str:
    match = re.search(rf'<tr id="rating-{re.escape(rating_id)}">(.*?)</tr>', html, re.S)
    assert match, f"no row for {rating_id}"
    return text(match.group(1))


def names(html: str) -> list[str]:
    return re.findall(r'<tr id="rating-[^"]+">\s*<td>[^<]*</td>\s*<td><a [^>]+>([^<]+)</a>', html)


# --- List ---------------------------------------------------------------------------------------


def test_list_in_display_order_with_the_default_marked(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine, Everyone={"display_order": 50})
    answer_settings(engine, default=TEEN)
    html = operator_client.get(URL).content.decode()
    # The Engine's order (display order) is kept; Everyone's 50 is the Engine's to sort.
    assert names(html) == ["Everyone", "Teen", "Mature", "Explicit"]
    assert "default" in row(html, TEEN)
    assert row(html, TEEN).startswith("20 Teen default Mild violence")
    # Complement: only the default is marked.
    for other in (EVERYONE, MATURE, EXPLICIT):
        assert " default " not in f" {row(html, other)} "
    assert "Mature" in row(html, TEEN)  # inherits from
    assert row(html, EXPLICIT).split(" Adult")[0]  # sanity: the row renders
    assert "— 1 live" in row(html, EXPLICIT)  # Explicit inherits nothing
    (listed,) = calls(engine, "GET", LIST)
    assert dict(listed.url.params) == {"include_retired": "1"}
    assert len(calls(engine, "GET", SETTINGS)) == 1


def test_retired_ratings_are_hidden_unless_show_retired(
    operator_client: Client, engine: FakeEngine
) -> None:
    retired = {"retired_at": "2026-10-24T22:00:00.000000Z"}
    answer_ratings(engine, Mature=retired)
    answer_settings(engine)
    html = operator_client.get(URL).content.decode()
    assert names(html) == ["Everyone", "Teen", "Explicit"]
    # A retired parent still shows by name.
    assert "Mature (retired)" in row(html, TEEN)
    shown = operator_client.get(f"{URL}?retired=1").content.decode()
    assert names(shown) == ["Everyone", "Teen", "Mature", "Explicit"]
    assert "retired 2026-10-24 22:00:00 UTC" in row(shown, MATURE)
    assert 'name="retired" value="1" checked' in shown
    # Retired ratings have no Edit or Retire.
    assert "Edit" not in row(shown, MATURE)
    assert "Retire" not in row(shown, MATURE)
    assert "Edit Retire" in row(shown, TEEN)


def test_settings_failure_shows_the_panel_and_no_marker(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_ratings(engine)
    engine.respond("GET", SETTINGS, 503, json=error("unavailable", "database down"))
    html = operator_client.get(URL).content.decode()
    assert names(html) == ["Everyone", "Teen", "Mature", "Explicit"]
    assert "Engine error 503 unavailable" in html
    assert "the default rating is not marked" in html
    assert 'class="default"' not in html
    assert html.index("Engine error 503") < html.index('id="rating-')


def test_list_links_and_nav(operator_client: Client, engine: FakeEngine) -> None:
    answer_ratings(engine)
    answer_settings(engine)
    html = operator_client.get(URL).content.decode()
    assert '<a href="/guardrails/policies/">Guardrail policies</a>' in html
    assert '<a href="/guardrails/ratings/new/">New rating</a>' in html
    assert '<a href="/guardrails/ratings/" class="active">Guardrails</a>' in html
    assert (
        f'href="/guardrails/ratings/{TEEN}/retire/?name=Teen&amp;next=%2Fguardrails%2Fratings%2F"'
        in html
    )


# --- Detail -------------------------------------------------------------------------------------


def answer_detail(engine: FakeEngine, body: dict[str, object]) -> None:
    engine.respond("GET", ITEM, 200, json=body)
    answer_ratings(engine)
    answer_policies(engine)
    answer_versions(engine)


def guardrail_rows(html: str) -> list[tuple[str, str]]:
    found = re.findall(r'<tr class="(own|inherited)">(.*?)</tr>', html, re.S)
    return [(kind, text(cells)) for kind, cells in found]


def test_detail_separates_own_and_inherited_guardrails(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = detail(
        "Mature",
        [("own_one", MATURE), (EXPLICIT_POLICY, EXPLICIT), ("gone", EXPLICIT)],
        policy_ids=["own_one"],
    )
    engine.respond("GET", ITEM, 200, json=body)
    answer_ratings(engine)
    answer_policies(
        engine, rating_policy("own_one", MATURE, "No knives", retired_at="2026-10-24T22:00:00Z")
    )
    answer_versions(engine)
    html = operator_client.get(f"{URL}{MATURE}/").content.decode()
    assert guardrail_rows(html) == [
        ("own", "own_one No knives (retired) Mature own"),
        ("inherited", f"{EXPLICIT_POLICY} No dragons Explicit inherited"),
        ("inherited", "gone gone (unknown policy) Explicit inherited"),
    ]
    (policies,) = calls(engine, "GET", "/admin/policies")
    assert dict(policies.url.params) == {"scope": "rating", "include_retired": "1"}
    assert "Policies No knives (retired)" in text(html)


def test_demo_everyone_lists_explicits_policy_as_inherited(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(engine, detail("Everyone", [(EXPLICIT_POLICY, EXPLICIT)]))
    html = operator_client.get(f"{URL}{EVERYONE}/").content.decode()
    assert guardrail_rows(html) == [
        ("inherited", f"{EXPLICIT_POLICY} No dragons Explicit inherited")
    ]
    assert '<p class="chain">Everyone → Teen → Mature → Explicit</p>' in html


def test_detail_global_note_and_guidance(operator_client: Client, engine: FakeEngine) -> None:
    answer_detail(engine, detail("Teen", image_guidance="", prompt_guidance="Line one\nLine two"))
    html = operator_client.get(f"{URL}{TEEN}/").content.decode()
    assert (
        "Global policies (e.g. <code>minors_no_sexual_content</code>) apply to every rating — "
        'see <a href="/guardrails/policies/?scope=global">Policies</a>.'
    ) in html
    assert '<p class="note">Guidance is not inherited</p>' in html
    assert (
        '<pre class="prompt-guidance" style="white-space: pre-wrap">Line one\nLine two</pre>'
    ) in html
    assert "<dt>Image guidance</dt>\n  <dd>(none)</dd>" in html
    assert "No rating policies apply — only the global ones." in html
    # Every field of the latest version; version_id is not shown.
    page = text(html)
    for shown in ("Display order 20", "Version 1", "by system", TEEN, "Inherits from Mature"):
        assert shown in page
    assert "RTV002" not in html


def test_inheritance_tree_nests_several_parents(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 200, json=detail("Mixed"))
    answer_ratings(engine, rating("Mixed"))
    answer_policies(engine)
    answer_versions(engine)
    html = operator_client.get(f"{URL}{MIXED}/").content.decode()
    assert 'class="chain"' not in html
    tree = re.search(r'<ul class="inheritance-tree">(.*)</ul>\s*<h2>Version', html, re.S)
    assert tree
    drawn = re.sub(r"\s+", "", tree.group(1))
    assert drawn == (
        "<li>Mixed<ul>"
        "<li>Teen<ul><li>Mature<ul><li>Explicit</li></ul></li></ul></li>"
        "<li>Mature<ul><li>Explicit</li></ul></li>"
        "</ul></li>"
    )


def test_chain_marks_retired_and_unknown_links(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=detail("Everyone"))
    answer_ratings(
        engine,
        Teen={"retired_at": "2026-10-24T22:00:00Z"},
        Mature={"inherits_from": ["01MISSING0000000000000000Z"]},
    )
    answer_policies(engine)
    answer_versions(engine)
    html = operator_client.get(f"{URL}{EVERYONE}/").content.decode()
    assert (
        '<p class="chain">Everyone → Teen (retired) → Mature → 01MISSING0000000000000000Z</p>'
        in html
    )


def test_versions_list_newest_first_and_each_viewable(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(engine, detail("Mature", version=2))
    html = operator_client.get(f"{URL}{MATURE}/").content.decode()
    versions = re.search(r'<table class="versions">(.*?)</table>', html, re.S)
    assert versions
    assert re.findall(r"<tr><td><a [^>]+>(\d+)</a>", versions.group(1)) == ["2", "1"]
    assert f'href="/guardrails/ratings/{MATURE}/versions/1/"' in html
    assert "2026-10-24 22:20:00 UTC" in html
    assert "console:nate" in html
    assert "All versions" not in html
    page = operator_client.get(f"{URL}{MATURE}/versions/2/").content.decode()
    assert "Mature — version 2 (latest)" in page
    assert "Strong violence and mature themes; keep it non-explicit." in page
    assert "Inherits from Explicit" in text(page)
    first = operator_client.get(f"{URL}{MATURE}/versions/1/").content.decode()
    assert "Mature — version 1</h1>" in first or "Mature — version 1 " in text(first)
    assert "(latest)" not in first
    assert operator_client.get(f"{URL}{MATURE}/versions/7/").status_code == 404


def test_more_than_twenty_versions_link_the_versions_page(
    operator_client: Client, engine: FakeEngine
) -> None:
    many = [rating("Mature", version=n) for n in range(1, 56)]
    engine.respond("GET", ITEM, 200, json=detail("Mature", version=55))
    answer_ratings(engine)
    answer_policies(engine)
    answer_versions(engine, many)
    html = operator_client.get(f"{URL}{MATURE}/").content.decode()
    shown = re.findall(r'/versions/(\d+)/"', html)
    assert shown == [str(n) for n in range(55, 35, -1)]
    assert f'<a href="/guardrails/ratings/{MATURE}/versions/">All versions (55) →</a>' in html
    page = operator_client.get(f"{URL}{MATURE}/versions/").content.decode()
    assert len(re.findall(r'/versions/\d+/"', page)) == 50
    assert "Page 1 of 2" in page
    second = operator_client.get(f"{URL}{MATURE}/versions/?page=2").content.decode()
    assert re.findall(r'/versions/(\d+)/"', second) == ["5", "4", "3", "2", "1"]


def test_retired_rating_banner_and_no_actions(operator_client: Client, engine: FakeEngine) -> None:
    answer_detail(engine, detail("Mature", retired_at="2026-10-24T22:00:00.000000Z"))
    html = operator_client.get(f"{URL}{MATURE}/").content.decode()
    assert (
        "Retired 2026-10-24 22:00:00 UTC — stories using it keep their version; new stories "
        "cannot choose it."
    ) in text(html)
    assert "/edit/" not in html
    assert "/retire/" not in html
    assert "/versions/1/" in html  # history stays viewable


def test_live_rating_detail_has_edit_and_retire(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer_detail(engine, detail("Teen"))
    html = operator_client.get(f"{URL}{TEEN}/").content.decode()
    assert f'href="/guardrails/ratings/{TEEN}/edit/"' in html
    assert f'href="/guardrails/ratings/{TEEN}/retire/?name=Teen&amp;next=' in html


def test_detail_degrades_per_section(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", ITEM, 200, json=detail("Everyone", [(TEEN_POLICY, TEEN)]))
    engine.respond("GET", LIST, 503, json=error("unavailable"))
    engine.respond("GET", "/admin/policies", 503, json=error("unavailable"))
    engine.respond("GET", VERSIONS, 503, json=error("unavailable", "versions down"))
    html = operator_client.get(f"{URL}{EVERYONE}/").content.decode()
    assert "The content ratings could not be read" in html
    assert "The policies could not be read" in html
    assert guardrail_rows(html) == [("inherited", f"{TEEN_POLICY} {TEEN_POLICY} {TEEN} inherited")]
    assert f'<p class="chain">Everyone → {TEEN}</p>' in html
    assert "versions down" in html


def test_unknown_rating_renders_the_error_panel(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", ITEM, 404, json=error("not_found", "content rating X does not exist"))
    html = operator_client.get(f"{URL}X/").content.decode()
    assert "Engine error 404 not_found" in html
