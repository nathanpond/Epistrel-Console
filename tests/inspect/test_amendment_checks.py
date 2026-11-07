"""A story's pending amendment support checks (#331) on Inspect's Overview and the Play page:
:func:`console.views.inspect.amendment_checks_line`'s wording (E23 plurals), the "Answer them"
link to the rewrite session (E6, D19), the omniscient suffix when the read names no session,
main's renders when nothing is pending (E24 goldens captured before the change), and no Engine
read beyond main's. The story read is #292's ``m7/story.json`` with ``amendment_checks_pending``
replaced inline."""

import copy
import dataclasses
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, include, path, reverse

from console.engine.client import EngineClient
from console.engine.models import AmendmentChecks, Story
from console.engine_access import engine_client_for
from console.views import inspect as inspect_views
from console.views import play as play_views
from console.views.inspect import (
    CHECKS_OMNISCIENT_SUFFIX,
    CHECKS_TEXT,
    ChecksLine,
    amendment_checks_line,
)
from epistrel_console import urls as real
from tests.fakes.engine import FakeEngine
from tests.fakes.ownership import answer_relations
from tests.fakes.story_config import answer_effective_config
from tests.fixtures import engine_fixture
from tests.golden import normalise_html
from tests.play.helpers import answer_page

pytestmark = pytest.mark.django_db

M7_URLS = "tests.urls_with_m7_pages"
WITHOUT_REWRITE = __name__  # this module's urlpatterns: the real ones less stories:rewrite
GOLDEN = Path(__file__).parent / "golden"
ABSENT = object()  # the field left out of the story read altogether
M7_STORY: dict[str, Any] = engine_fixture("m7/story")
STORY_ID: str = M7_STORY["id"]
SESSION = "01JM7RW0000000000000000001"
OVERVIEW_URL = f"/stories/{STORY_ID}/inspect/"
PLAY_URL = f"/stories/{STORY_ID}/"
PAGES = {"overview": OVERVIEW_URL, "play": PLAY_URL}
TWO = (
    "2 amendment support checks waiting for your answer — earlier amendments changed something "
    "later lines relied on"
)
ANSWER = "Answer them"
# A signed value's timestamp and signature (the Play page's snapshot and cast forms): they change
# with the clock, not with the page.
SIGNED = re.compile(r":[0-9A-Za-z]{6}:[0-9A-Za-z_-]{43}\b")

story_patterns = (
    [p for p in real.story_patterns[0] if getattr(p, "name", None) != "rewrite"],
    "stories",
)
urlpatterns: list[URLPattern | URLResolver] = [
    path("stories/", include(story_patterns)) if str(p.pattern) == "stories/" else p
    for p in real.urlpatterns
    if isinstance(p, URLPattern | URLResolver)
]


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any) -> Iterator[FakeEngine]:
    """The fake Engine behind both the Inspect and the Play views."""
    fake = FakeEngine()
    answer_relations(fake)
    answer_effective_config(fake)

    def client_with_fake(request: HttpRequest) -> EngineClient:
        client = engine_client_for(request)
        client._transport = fake.transport
        return client

    monkeypatch.setattr(inspect_views, "engine_client_for", client_with_fake)
    monkeypatch.setattr(play_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def story_with(checks: Any = ABSENT) -> dict[str, Any]:
    """``m7/story.json`` with ``amendment_checks_pending`` set to ``checks`` (left out when
    :data:`ABSENT`)."""
    story = copy.deepcopy(M7_STORY)
    if checks is ABSENT:
        story.pop("amendment_checks_pending", None)
    else:
        story["amendment_checks_pending"] = checks
    return story


def render(client: Client, engine: FakeEngine, url: str, checks: Any = ABSENT) -> str:
    """The page at ``url`` over a story read whose ``amendment_checks_pending`` is ``checks``."""
    answer_page(engine, story=story_with(checks))
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def checks_p(html: str) -> str:
    """The page's one pending-checks paragraph."""
    assert html.count('class="amendment-checks') == 1
    return (
        '<p class="amendment-checks'
        + html.split('<p class="amendment-checks')[1].split("</p>")[0]
        + "</p>"
    )


def session_link() -> str:
    href = reverse("stories:rewrite", args=[STORY_ID, SESSION])
    return f' · <a class="answer-checks" href="{href}">{ANSWER}</a>'


def parsed(checks: Any) -> Story:
    story = Story.from_json(story_with(checks))
    assert story is not None
    return story


# --- the wording ---------------------------------------------------------------------------------


def test_the_line_wording_and_plurals() -> None:
    assert CHECKS_TEXT.format(count="2", checks="checks") == TWO
    with override_settings(ROOT_URLCONF=M7_URLS):
        two = amendment_checks_line(parsed({"count": 2, "session_id": SESSION}), STORY_ID)
        url = reverse("stories:rewrite", args=[STORY_ID, SESSION])
    assert two == ChecksLine(TWO, url)
    one = amendment_checks_line(parsed({"count": 1, "session_id": None}), STORY_ID)
    assert one is not None
    assert one.text.startswith("1 amendment support check waiting for your answer — ")
    assert one.text.endswith(CHECKS_OMNISCIENT_SUFFIX)
    many = amendment_checks_line(parsed({"count": 1200}), STORY_ID)
    assert many is not None
    assert many.text.startswith("1,200 amendment support checks waiting")


def test_the_line_is_none_without_a_positive_int_count() -> None:
    assert amendment_checks_line(None, STORY_ID) is None
    assert amendment_checks_line(parsed(ABSENT), STORY_ID) is None
    assert amendment_checks_line(parsed(None), STORY_ID) is None
    assert amendment_checks_line(parsed({"count": 0, "session_id": SESSION}), STORY_ID) is None
    base = parsed(ABSENT)
    for count in (-1, True, "2", 2.0):
        story = dataclasses.replace(
            base, amendment_checks_pending=AmendmentChecks(cast(Any, count))
        )
        assert amendment_checks_line(story, STORY_ID) is None, count


def test_a_blank_or_non_string_session_counts_as_absent() -> None:
    base = parsed(ABSENT)
    for session_id in ("", "  ", 7):
        checks = AmendmentChecks(2, cast(Any, session_id))
        line = amendment_checks_line(
            dataclasses.replace(base, amendment_checks_pending=checks), STORY_ID
        )
        assert line == ChecksLine(TWO + CHECKS_OMNISCIENT_SUFFIX, ""), session_id


# --- the pages -----------------------------------------------------------------------------------


@pytest.mark.parametrize("urlconf", [M7_URLS, None])
def test_overview_line_and_link(
    operator_client: Client, engine: FakeEngine, urlconf: str | None
) -> None:
    with override_settings(**({"ROOT_URLCONF": urlconf} if urlconf else {})):
        html = render(operator_client, engine, OVERVIEW_URL, {"count": 2, "session_id": SESSION})
        link = session_link()
    expected = f'<p class="amendment-checks warning" role="status">{TWO}{link}</p>'
    assert checks_p(html) == expected
    assert CHECKS_OMNISCIENT_SUFFIX not in html
    # Right after the header line, before the pipeline line and the capabilities.
    after_header = html.split('<p class="overview-header">')[1].split("</p>", 1)[1]
    assert after_header.startswith("\n" + expected + "\n")
    assert html.index(expected) < html.index("<h2>Capabilities</h2>")


def test_overview_without_the_session_page_has_no_answer_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    """Complement: under a urlconf without ``stories:rewrite`` the text remains and "Answer
    them" is absent altogether (D19)."""
    with override_settings(ROOT_URLCONF=WITHOUT_REWRITE):
        html = render(operator_client, engine, OVERVIEW_URL, {"count": 2, "session_id": SESSION})
    assert checks_p(html) == f'<p class="amendment-checks warning" role="status">{TWO}</p>'
    assert ANSWER not in html


@pytest.mark.parametrize("urlconf", [M7_URLS, None])
def test_play_line(operator_client: Client, engine: FakeEngine, urlconf: str | None) -> None:
    with override_settings(**({"ROOT_URLCONF": urlconf} if urlconf else {})):
        html = render(operator_client, engine, PLAY_URL, {"count": 2, "session_id": SESSION})
        link = session_link()
    expected = f'<p class="amendment-checks warning" role="status">{TWO}{link}</p>'
    assert checks_p(html) == expected
    # At the top of the page content, before the story header.
    assert html.index(expected) < html.index('<dl class="story-header">')
    assert html.index("<h1>") < html.index(expected)


def test_play_without_the_session_page_has_no_answer_link(
    operator_client: Client, engine: FakeEngine
) -> None:
    with override_settings(ROOT_URLCONF=WITHOUT_REWRITE):
        html = render(operator_client, engine, PLAY_URL, {"count": 2, "session_id": SESSION})
    assert checks_p(html) == f'<p class="amendment-checks warning" role="status">{TWO}</p>'
    assert ANSWER not in html


@pytest.mark.parametrize("page", PAGES)
def test_no_session_id_names_omniscient(
    operator_client: Client, engine: FakeEngine, page: str
) -> None:
    with override_settings(ROOT_URLCONF=M7_URLS):
        html = render(operator_client, engine, PAGES[page], {"count": 2, "session_id": None})
    text = TWO + " (open the story as omniscient to see where)"
    assert checks_p(html) == f'<p class="amendment-checks warning" role="status">{text}</p>'
    assert ANSWER not in html


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize(
    "checks", [ABSENT, None, {"count": 0}, {"count": 0, "session_id": SESSION}]
)
def test_absent_or_zero_renders_main(
    operator_client: Client, engine: FakeEngine, page: str, checks: Any
) -> None:
    """E24: main's render (captured before #331) with the field absent, null or ``count: 0``."""
    html = render(operator_client, engine, PAGES[page], checks)
    assert "amendment-checks" not in html
    golden = (GOLDEN / f"amendment_checks_{page}_main.html").read_text().rstrip("\n")
    assert SIGNED.sub(":TS:SIG", normalise_html(html)) == SIGNED.sub(":TS:SIG", golden)


def _reads(engine: FakeEngine) -> list[tuple[str, str, str]]:
    return [(r.method, r.url.path, str(r.url.params)) for r in engine.requests]


@pytest.mark.parametrize("page", PAGES)
def test_no_extra_reads(operator_client: Client, engine: FakeEngine, page: str) -> None:
    """The line reads the page's existing story read: the ``count: 2`` render makes exactly the
    field-absent render's Engine calls."""
    render(operator_client, engine, PAGES[page])
    absent = _reads(engine)
    engine.reset()
    render(operator_client, engine, PAGES[page], {"count": 2, "session_id": SESSION})
    assert absent
    assert _reads(engine) == absent
