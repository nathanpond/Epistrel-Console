"""A paused story's banner and refusals by cause (#295, E10): the backfill causes word
``console.upgrades.backfill_pause_text``, link and poll the Backfill page (never a rating check),
and ``rating_check``/``reactivation_check`` render byte for byte as on main."""

from typing import Any

import pytest
from django.template.loader import render_to_string
from django.test import override_settings
from django.urls import NoReverseMatch
from django.urls import reverse as real_reverse

from console import story_pause
from console.engine.client import EngineError
from console.engine.models import PauseDetails, Story
from console.story_pause import PauseBanner, pause_banner, paused_refusal, story_paused_refusal
from console.upgrades import (
    BACKFILL_CONFLICTS_TEXT,
    BACKFILL_PAUSED_TEXT,
    backfill_pause_text,
    backfill_url,
)
from console.wording import write_refusal
from tests.play.helpers import story_json

S = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
J = "01JB2Z8Y5Q3W6K9M1N4P7R0JOB"
STUBS = "tests.urls_with_m7_pages"

# The banners as main rendered them before #295 (captured from main with the same values).
MAIN_RATING = "\n".join(
    [
        '<div id="pause-banner" class="banner banner-warning pause-banner" role="status" hx-get="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/?banner=1&amp;cause=rating_check" hx-trigger="every 2s" hx-swap="outerHTML">',  # noqa: E501 - main's markup, verbatim
        '  <strong><a class="pause-check" href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/">Paused: rating check in progress (3/9)</a></strong>',  # noqa: E501 - main's markup, verbatim
        '  <progress value="3" max="9">3/9</progress>',
        "  ",
        "  ",
        "  ",
        "  ",
        "</div>",
        "",
    ]
)

MAIN_RATING_ZERO = "\n".join(
    [
        '<div id="pause-banner" class="banner banner-warning pause-banner" role="status" hx-get="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/?banner=1&amp;cause=rating_check" hx-trigger="every 2s" hx-swap="outerHTML">',  # noqa: E501 - main's markup, verbatim
        '  <strong><a class="pause-check" href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/">Paused: rating check in progress (starting…)</a></strong>',  # noqa: E501 - main's markup, verbatim
        "  <progress></progress>",
        "  ",
        "  ",
        "  ",
        "  ",
        "</div>",
        "",
    ]
)

MAIN_REACTIVATION = "\n".join(
    [
        '<div id="pause-banner" class="banner banner-warning pause-banner" role="status" hx-get="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/?banner=1&amp;cause=reactivation_check&amp;op=rewind" hx-trigger="every 2s" hx-swap="outerHTML">',  # noqa: E501 - main's markup, verbatim
        '  <strong><a class="pause-check" href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/?op=rewind">Paused: guardrail re-check before an edit (2/5)</a></strong>',  # noqa: E501 - main's markup, verbatim
        '  <progress value="2" max="5">2/5</progress>',
        "  ",
        "  ",
        "  ",
        "  ",
        "</div>",
        "",
    ]
)

MAIN_RATING_NF = "\n".join(
    [
        '<div id="pause-banner" class="banner banner-warning pause-banner" role="status">',
        '  <strong><a class="pause-check" href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/rating/checks/01JB2Z8Y5Q3W6K9M1N4P7R0JOB/">Paused — check status unavailable</a></strong>',  # noqa: E501 - main's markup, verbatim
        "  ",
        "  ",
        '  <span class="pause-error">No such check, or it expired (kept 30 days)</span>',
        "  ",
        '  <a class="refresh" href="/stories/01JB2Z8Y5Q3W6K9M1N4P7R0T2V/">Refresh</a>',
        "</div>",
        "",
    ]
)


def banner_html(banner: PauseBanner) -> str:
    return render_to_string("console/stories/_pause_banner.html", {"pause_banner": banner})


@pytest.mark.parametrize(
    ("banner", "golden"),
    [
        (PauseBanner(S, "rating_check", J, 3, 9), MAIN_RATING),
        (PauseBanner(S, "rating_check", J, 0, 0), MAIN_RATING_ZERO),
        (PauseBanner(S, "reactivation_check", J, 2, 5, op="rewind"), MAIN_REACTIVATION),
        (PauseBanner(S, "rating_check", J, 0, 0, not_found=True), MAIN_RATING_NF),
    ],
)
def test_rating_and_reactivation_banners_render_as_on_main(
    banner: PauseBanner, golden: str
) -> None:
    """**Complement.**"""
    assert banner_html(banner) == golden


def _story(cause: str, progress: dict[str, Any], **pause: Any) -> Story:
    found = Story.from_json(
        story_json(
            id=S,
            status="paused",
            pause={"job_id": J, "cause": cause, "progress": progress, **pause},
        )
    )
    assert found is not None
    return found


def test_a_story_read_with_a_backfills_progress_parses_and_the_banner_counts_chunks() -> None:
    story = _story("backfill", {"phase": "extracting", "chunks_done": 2, "chunks_total": 7})
    assert story.pause is not None
    assert story.pause.progress.chunks_done == 2
    assert story.pause.progress.done is None
    banner = pause_banner(story)
    assert banner is not None
    assert banner.text == "Paused: backfill in progress (2/7 chunks)"
    html = banner_html(banner)
    assert '<progress value="2" max="7">2/7</progress>' in html
    assert "rating" not in html


def test_a_backfill_banner_without_counts_is_indeterminate() -> None:
    banner = pause_banner(_story("backfill", {"phase": "extracting"}))
    assert banner is not None
    assert banner.text == BACKFILL_PAUSED_TEXT
    assert "<progress></progress>" in banner_html(banner)
    zero = pause_banner(_story("backfill", {"chunks_done": 0, "chunks_total": 0}))
    assert zero is not None
    assert zero.text == BACKFILL_PAUSED_TEXT
    assert "0/0" not in banner_html(zero)


def test_the_banner_falls_back_to_done_and_total() -> None:
    banner = pause_banner(_story("backfill", {"done": 3, "total": 9}))
    assert banner is not None
    assert banner.text == "Paused: backfill in progress (3/9 chunks)"


def test_backfill_banners_neither_link_nor_poll_while_unregistered() -> None:
    for cause in ("backfill", "backfill_conflicts"):
        banner = pause_banner(_story(cause, {"chunks_done": 2, "chunks_total": 7}))
        assert banner is not None
        assert banner.check_url == ""
        assert banner.poll_url == ""
        assert not banner.polling
        html = banner_html(banner)
        assert "hx-trigger" not in html
        assert f'<a class="refresh" href="/stories/{S}/">Refresh</a>' in html


@override_settings(ROOT_URLCONF=STUBS)
@pytest.mark.parametrize("cause", ["backfill", "backfill_conflicts"])
def test_backfill_banners_poll_the_backfill_page_every_5s(cause: str) -> None:
    banner = pause_banner(_story(cause, {"chunks_done": 2, "chunks_total": 7}))
    assert banner is not None
    assert banner.check_url == f"/stories/{S}/backfill/"
    assert banner.poll_url == f"/stories/{S}/backfill/?banner=1&cause={cause}"
    assert banner.poll_trigger == "every 5s"
    html = banner_html(banner)
    assert (
        f'hx-get="/stories/{S}/backfill/?banner=1&amp;cause={cause}" hx-trigger="every 5s"' in html
    )
    assert f'<a class="pause-check" href="/stories/{S}/backfill/">' in html


def test_backfill_conflicts_banner_reads_its() -> None:
    banner = pause_banner(_story("backfill_conflicts", {"chunks_done": 7, "chunks_total": 7}))
    assert banner is not None
    assert banner.text == "Paused: the backfill is waiting for you to resolve its conflicts"


def test_backfill_failure_texts() -> None:
    gone = PauseBanner(S, "backfill", J, None, None, not_found=True)
    assert gone.text == "Paused — backfill status unavailable"
    assert '<span class="pause-error">No such backfill</span>' in banner_html(gone)


def _paused(cause: str, **details: Any) -> EngineError:
    return EngineError(
        "story_paused", "paused", {"story_id": S, "job_id": J, "cause": cause, **details}, 409
    )


def test_the_refusals_word_the_backfill_causes() -> None:
    refusal = paused_refusal(
        _paused("backfill", progress={"phase": "extracting", "chunks_done": 2, "chunks_total": 7})
    )
    assert refusal.text == "Paused: backfill in progress (2/7 chunks)"
    assert refusal.url == ""
    conflicts = paused_refusal(_paused("backfill_conflicts", conflicts_open=2, backfill_id="B"))
    assert conflicts.text == "Paused: the backfill is waiting for you to resolve 2 conflicts"
    one = paused_refusal(_paused("backfill_conflicts", conflicts_open=1))
    assert one.text == "Paused: the backfill is waiting for you to resolve 1 conflict"
    written = write_refusal(_paused("backfill", progress={}), S, "play a turn")
    assert written is not None
    assert written.text == BACKFILL_PAUSED_TEXT
    read = story_paused_refusal(_story("backfill", {"chunks_done": 1, "chunks_total": 4}))
    assert read.text == "Paused: backfill in progress (1/4 chunks)"
    assert read.url == ""


@override_settings(ROOT_URLCONF=STUBS)
def test_the_refusals_link_the_backfill_page() -> None:
    assert paused_refusal(_paused("backfill", progress={})).url == f"/stories/{S}/backfill/"
    assert story_paused_refusal(_story("backfill_conflicts", {})).url == f"/stories/{S}/backfill/"
    assert backfill_url(S) == f"/stories/{S}/backfill/"


def test_backfill_pause_text() -> None:
    assert BACKFILL_CONFLICTS_TEXT.startswith("Paused: the backfill is waiting for you to resolve")
    assert backfill_pause_text("backfill", None) == BACKFILL_PAUSED_TEXT
    assert backfill_pause_text("backfill_conflicts", None).endswith("resolve its conflicts")
    details = PauseDetails("backfill", J, {"chunks_done": 1200, "chunks_total": 3400}, None, None)
    assert (
        backfill_pause_text("backfill", details)
        == "Paused: backfill in progress (1,200/3,400 chunks)"
    )
    assert backfill_url("") is None
    assert backfill_url(S) is None  # unregistered on main


@pytest.mark.parametrize("cause", ["backfill", "backfill_conflicts"])
def test_no_rating_check_url_is_ever_built_for_the_backfill_causes(
    monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    """**Complement:** ``reverse`` refuses ``stories:rating-check`` here."""

    def guarded(name: str, *args: Any, **kwargs: Any) -> str:
        if name == "stories:rating-check":
            raise AssertionError("a rating-check URL was built for a backfill cause")
        return real_reverse(name, *args, **kwargs)

    monkeypatch.setattr(story_pause, "reverse", guarded)
    banner = pause_banner(_story(cause, {"chunks_done": 2, "chunks_total": 7}))
    assert banner is not None
    banner_html(banner)
    for attempt in (banner.check_url, banner.poll_url, banner.text):
        assert "rating" not in attempt
    paused_refusal(_paused(cause, progress={}))
    story_paused_refusal(_story(cause, {}))
    with pytest.raises(NoReverseMatch):  # the guard is live: a real miss still raises
        guarded("stories:no-such-name")
