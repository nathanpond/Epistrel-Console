"""The story's Backfill page (#308): the facts, the polled status block, the conflicts, the
report and the pending change, and the pause banner's ``?banner=1`` poll."""

import re
from typing import Any

import httpx
import pytest
from django.test import Client

from console.backfill import (
    CONFLICT_UNREADABLE,
    NEW_CONFIG_TEXT,
    NO_BACKFILL,
    OMNISCIENT_NOTE,
    RECOMMENDED_LAST,
    backfill_view,
    pending_change,
    report_groups,
    status_fact,
)
from console.engine.models import StoryBackfill
from console.job_poll import RETRYING_TEXT
from console.views import backfill as backfill_views
from tests.backfill.helpers import (
    BACKFILL_PATH,
    BACKFILL_READ,
    CONFIG_PATH,
    CONFIG_READ,
    PAGE,
    STORY,
    STORY_PATH,
    STORY_READ,
    config,
    m7,
    paths,
)
from tests.fakes.engine import FakeEngine
from tests.play.helpers import error_json

pytestmark = pytest.mark.django_db

PARTIAL = f"{PAGE}?partial=1"
TRIGGER = 'hx-trigger="every 5s"'


def answer(engine: FakeEngine, body: Any) -> None:
    engine.respond("GET", BACKFILL_PATH, 200, json=body)


def extracting(done: int = 2, total: int = 7) -> dict[str, Any]:
    body = m7("backfill_extracting")
    body["progress"].update(chunks_done=done, chunks_total=total)
    return body


def html_of(client: Client, url: str = PAGE) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def sent_query(engine: FakeEngine) -> list[dict[str, str]]:
    return [dict(r.url.params) for r in engine.requests if r.url.path == BACKFILL_READ]


# --- the facts -----------------------------------------------------------------------------------


def test_the_facts(operator_client: Client, engine: FakeEngine) -> None:
    html = html_of(operator_client)
    assert "<title>Backfill · Harbour Ledger</title>" in html
    assert "<h2>Backfill</h2>" in html
    assert "Kind: <span>Gap fill</span>" in html
    assert "Capabilities: <code>episodic</code>, <code>commitment</code>" in html
    turns = re.search(r'<p class="backfill-range">(.*?)</p>', html)
    assert turns is not None
    assert turns.group(1) == (
        f'Turns <a href="/stories/{STORY}/history/?before_turn=2#turn-1-0">1</a>–'  # noqa: RUF001
        f'<a href="/stories/{STORY}/history/?before_turn=4#turn-3-0">3</a>'
    )
    assert (
        'Requested by <a href="/principals/console%253Anate/"><code>console:nate</code></a> · <time'
        in html
    )
    assert "(you)" not in html
    assert "Finished — " not in html  # "Finished —" with no time
    assert re.search(r"Finished —</p>", html)
    assert "Conflicts: 0 open · 0 resolved · 0 uncertain" in html
    assert f'<p class="note">{OMNISCIENT_NOTE}</p>' in html


def test_the_nav_marks_backfill(operator_client: Client, engine: FakeEngine) -> None:
    html = html_of(operator_client)
    assert f'<a href="{PAGE}" aria-current="page">Backfill</a>' in html


# --- status and polling ------------------------------------------------------------------------


def test_extracting_page_polls_slowly(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, extracting())
    html = html_of(operator_client)
    assert "Extracting (2/7 chunks)" in html
    assert '<progress value="2" max="7">' in html
    assert f'hx-get="{PARTIAL}" {TRIGGER}' in html
    assert 'Phase: <span title="extracting">Extracting</span>' in html
    assert "Cancel the backfill…" in html


def test_the_completed_page_renders_no_trigger(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** the completed fixture renders no ``hx-trigger``."""
    answer(engine, m7("backfill_completed"))
    html = html_of(operator_client)
    assert "Completed" in html
    assert "hx-trigger" not in html
    assert "<progress" not in html


def test_reconciling_progress_counts_turns(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("backfill_extracting")
    body["status"] = "reconciling"
    body["progress"].update(phase="reconciling", reconciled_through_turn=4, last_turn=9)
    answer(engine, body)
    html = html_of(operator_client)
    assert "Reconciling (through turn 4 of 9)" in html
    assert '<progress value="4" max="9">' in html


def test_progress_is_indeterminate_without_a_total(
    operator_client: Client, engine: FakeEngine
) -> None:
    answer(engine, extracting(0, 0))
    html = html_of(operator_client)
    assert "<progress></progress>" in html
    assert "Extracting (" not in html


def test_paused_for_conflicts_waits(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, m7("backfill_paused"))
    html = html_of(operator_client)
    assert "Waiting for you: 2 conflicts to resolve" in html
    assert "hx-trigger" not in html
    assert "<progress" not in html


def test_partial_answers_only_the_block(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, extracting())
    response = operator_client.get(PARTIAL)
    html = response.content.decode()
    assert response.status_code == 200
    assert '<nav class="story-pages"' not in html
    assert 'class="story-pages"' not in html
    assert 'class="story-header"' not in html
    assert html.lstrip().startswith('<div id="job-poll"')
    assert "Extracting (2/7 chunks)" in html
    assert TRIGGER in html
    assert "HX-Refresh" not in response.headers


def test_partial_makes_one_read(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** exactly one Engine read (``GET …/backfill``), no story, no config."""
    operator_client.get(PARTIAL)
    assert paths(engine) == [f"GET {BACKFILL_READ}"]
    assert sent_query(engine) == [{"conflicts": "open", "limit": "50"}]


@pytest.mark.parametrize("fixture", ["backfill_completed", "backfill_paused"])
def test_partial_refreshes_once_it_stops(
    operator_client: Client, engine: FakeEngine, fixture: str
) -> None:
    answer(engine, m7(fixture))
    response = operator_client.get(PARTIAL)
    assert response.headers["HX-Refresh"] == "true"
    assert "hx-trigger" not in response.content.decode()


def test_partial_retries_on_503(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 503, json=error_json("engine_unavailable"))
    html = html_of(operator_client, PARTIAL)
    assert RETRYING_TEXT in html
    assert f'hx-get="{PARTIAL}" {TRIGGER}' in html


def test_partial_retries_when_unreachable(operator_client: Client, engine: FakeEngine) -> None:
    engine.fail("GET", BACKFILL_PATH, httpx.ConnectError("down"))
    html = html_of(operator_client, PARTIAL)
    assert RETRYING_TEXT in html
    assert TRIGGER in html


def test_partial_404_says_no_backfill(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** a 404 ``backfill_not_found`` → ``NO_BACKFILL`` with no trigger."""
    engine.respond("GET", BACKFILL_PATH, 404, json=error_json("backfill_not_found"))
    html = html_of(operator_client, PARTIAL)
    assert NO_BACKFILL in html
    assert "hx-trigger" not in html


def test_partial_other_404_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 404, json=error_json("not_found"))
    html = html_of(operator_client, PARTIAL)
    assert 'class="engine-error' in html or "engine-error" in html
    assert NO_BACKFILL not in html
    assert "hx-trigger" not in html


def test_partial_held_is_the_held_fragment(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        BACKFILL_PATH,
        503,
        json=error_json("erasure_pending"),
        headers={"Retry-After": "30"},
    )
    html = html_of(operator_client, PARTIAL)
    assert "Held until the erasure ledger is re-applied — retry in 30 s" in html
    assert "hx-trigger" not in html
    assert RETRYING_TEXT not in html


# --- the page's reads ---------------------------------------------------------------------------


def test_the_page_reads_story_config_backfill(operator_client: Client, engine: FakeEngine) -> None:
    operator_client.get(PAGE)
    assert paths(engine) == [f"GET {STORY_READ}", f"GET {CONFIG_READ}", f"GET {BACKFILL_READ}"]


def test_conflicts_all_toggle(operator_client: Client, engine: FakeEngine) -> None:
    html = html_of(operator_client, f"{PAGE}?conflicts=all")
    assert sent_query(engine) == [{"conflicts": "all", "limit": "50"}]
    assert f'<a href="{PAGE}">Show open conflicts only</a>' in html
    assert f'hx-get="{PARTIAL}"' in html  # the poll never carries conflicts=all


def test_the_bare_page_sends_open(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** the bare page sends ``conflicts=open``."""
    html = html_of(operator_client)
    assert sent_query(engine) == [{"conflicts": "open", "limit": "50"}]
    assert f'<a href="{PAGE}?conflicts=all">Show resolved conflicts</a>' in html


def test_no_backfill_says_so(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 404, json=error_json("backfill_not_found"))
    response = operator_client.get(PAGE)
    html = response.content.decode()
    assert response.status_code == 200
    assert NO_BACKFILL in html
    assert 'id="job-poll"' not in html
    assert OMNISCIENT_NOTE not in html


def test_another_read_failure_is_the_panel(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", BACKFILL_PATH, 422, json=error_json("validation_error"))
    response = operator_client.get(PAGE)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<h2>Backfill</h2>" in html
    assert "engine-error" in html
    assert 'id="job-poll"' not in html


# --- conflicts ---------------------------------------------------------------------------------


def _sections(html: str) -> dict[str, str]:
    return dict(
        re.findall(r'<section class="conflict" id="(conflict-[^"]+)">(.*?)</section>', html, re.S)
    )


def test_conflicts_quote_and_recommend(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, m7("backfill_paused"))
    html = html_of(operator_client)
    sections = _sections(html)
    assert list(sections) == [
        "conflict-01JM7CF0000000000000000001",
        "conflict-01JM7CF0000000000000000002",
    ]
    first, second = sections.values()
    assert "<q>I&#x27;m fifty this winter.</q>" in first
    assert "<q>Bob&#x27;s fifty-two, whatever he tells you.</q>" in first
    assert "<q>I grew up in Port Vey.</q>" in second
    assert "<q>You told me you were from the hills.</q>" in second
    assert "<td>Alice</td>" in first
    assert "<td>Bob</td>" in first
    assert "<td>Mara (player)</td>" in second
    assert f'<a href="/stories/{STORY}/history/?before_turn=4#turn-3-0">Turn 3</a>' in first
    assert ">Open</span>" in first
    # The marker sits on recommended.choose's statement only: the second row of each.
    for section in (first, second):
        rows = re.findall(r'<tr class="statement( recommended)?">(.*?)</tr>', section, re.S)
        assert [bool(marked) for marked, _ in rows] == [False, True]
        assert section.count(RECOMMENDED_LAST) == 1
        assert RECOMMENDED_LAST in rows[1][1]


def test_bad_conflict_entry_is_one_row(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("backfill_paused")
    body["conflict_list"][1] = {"id": "01JM7CF0000000000000000009", "status": "open"}
    engine.side_effect("GET", BACKFILL_PATH, lambda request: httpx.Response(200, json=body))
    html = html_of(operator_client)
    assert html.count(CONFLICT_UNREADABLE) == 1
    assert 'id="conflict-01JM7CF0000000000000000009"' in html
    assert "1 conflict couldn't be read" in html
    assert "<q>I&#x27;m fifty this winter.</q>" in html


def test_no_resolve_controls_while_unregistered(
    operator_client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D19: with ``stories:backfill-conflict`` unregistered (#309 registers it; this stands in
    for a build without it) a conflict section holds no form."""
    monkeypatch.setattr(backfill_views, "conflict_url", lambda story_id, conflict_id: None)
    answer(engine, m7("backfill_paused"))
    html = html_of(operator_client)
    for section in _sections(html).values():
        assert "<form" not in section


def test_open_conflicts_get_resolve_controls_once_registered(
    operator_client: Client, engine: FakeEngine
) -> None:
    """The complement under the real urlconf (#309): each open conflict's section holds one
    resolve form."""
    answer(engine, m7("backfill_paused"))
    html = html_of(operator_client)
    for section in _sections(html).values():
        assert section.count("<form") == 1


def test_a_resolved_conflict_reads_its_resolution(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m7("backfill_paused")
    conflict = body["conflict_list"][0]
    conflict.update(
        status="resolved",
        recommended=None,
        resolution={
            "choice": {"statement_id": "01JM7SM0000000000000000002"},
            "via": "choose",
            "by": "console:nate",
        },
        resolved_by="console:nate",
        resolved_at="2026-11-05T11:03:00Z",
    )
    answer(engine, body)
    html = html_of(operator_client, f"{PAGE}?conflicts=all")
    first = _sections(html)["conflict-01JM7CF0000000000000000001"]
    assert ">Resolved</span>" in first
    assert (
        'Resolved by <a href="/principals/console%253Anate/"><code>console:nate</code></a> · <time'
        in first
    )
    assert ": Choose Turn 3 “Bob&#x27;s fifty-two, whatever he tells you.”</p>" in first
    assert RECOMMENDED_LAST not in first


def test_showing_the_first_of_more(operator_client: Client, engine: FakeEngine) -> None:
    body = m7("backfill_paused")
    body["conflicts"]["open"] = 60
    answer(engine, body)
    html = html_of(operator_client)
    assert "Showing the first 2 of 60 conflicts" in html


# --- report and pending change --------------------------------------------------------------------


def test_report_and_pending_change(operator_client: Client, engine: FakeEngine) -> None:
    answer(engine, m7("backfill_completed"))
    html = html_of(operator_client)
    assert "<h3>Report</h3>" in html
    assert '<span class="report-label">Facts</span>: 9' in html
    assert '<span class="report-label">State changes</span>: 1' in html
    assert '<span class="report-label">Counts · Episodic</span>: 2' in html
    assert '<span class="report-label">By resolution · Last mention</span>: 2' in html
    headings = re.findall(r'<dt class="report-group">(.*?)</dt>', html)
    assert headings == ["Counts", "Materialization (deferred genesis content)", "Conflicts"]
    assert f'<a href="/stories/{STORY}/config/">{NEW_CONFIG_TEXT}</a>' in html
    assert "Waiting on this backfill" not in html


def test_a_pending_backfill_line(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** a config with ``pending_backfill`` set → the "Waiting on" line."""
    engine.respond(
        "GET", CONFIG_PATH, 200, json=config(pending_backfill={"id": "01JM7BF0000000000000000001"})
    )
    html = html_of(operator_client)
    assert (
        '<p class="pending-backfill">Waiting on this backfill: a configuration change</p>' in html
    )
    assert "&quot;id&quot;: &quot;01JM7BF0000000000000000001&quot;" in html
    assert NEW_CONFIG_TEXT not in html


def test_the_pending_line_shows_without_a_backfill(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", BACKFILL_PATH, 404, json=error_json("backfill_not_found"))
    engine.respond("GET", CONFIG_PATH, 200, json=config(pending_backfill={"id": "x"}))
    html = html_of(operator_client)
    assert NO_BACKFILL in html
    assert "Waiting on this backfill: a configuration change" in html


def test_a_failed_config_read_leaves_the_line_out(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.respond("GET", CONFIG_PATH, 422, json=error_json("validation_error"))
    html = html_of(operator_client)
    assert "Waiting on this backfill" not in html
    assert "Extracting" in html


def test_a_failed_backfill_shows_the_error_alone(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m7("backfill_completed")
    body.update(status="failed", error="ExtractionError")
    answer(engine, body)
    html = html_of(operator_client)
    assert html.count("ExtractionError") == 1
    assert '<p class="warning"><strong>ExtractionError</strong>' in html
    assert "<h3>Report</h3>" not in html
    assert NEW_CONFIG_TEXT not in html


def test_a_cancelled_backfill_shows_its_raw_report_only(
    operator_client: Client, engine: FakeEngine
) -> None:
    body = m7("backfill_cancelled")["backfill"]
    body["report"] = {"facts": 2}
    answer(engine, body)
    html = html_of(operator_client)
    assert "Cancelled" in html
    assert "<h3>Report</h3>" not in html
    assert "&quot;facts&quot;: 2" in html
    assert "Cancel the backfill…" not in html


def test_report_groups_skip_bad_sections() -> None:
    assert report_groups("nope") == ()
    groups = report_groups(
        {"counts": {"facts": 3, "flag": True}, "by_outcome": [], "materialization": "x"}
    )
    assert [(g.heading, g.rows) for g in groups] == [("Counts", (("Facts", "3"),))]


def test_pending_change_names_kind_and_capabilities() -> None:
    found = pending_change({"kind": "upgrade", "capabilities": ["episodic"]})
    assert found is not None
    assert found.text == "Waiting on this backfill: Upgrade (episodic)"
    assert pending_change(None) is None


def test_the_view_tolerates_a_bare_backfill() -> None:
    body = m7("backfill_extracting")
    body["status"] = "queued"
    parsed = StoryBackfill.from_json(body)
    assert parsed is not None
    view = backfill_view(parsed, STORY, {})
    assert view.state.text == "Queued"
    assert not view.state.running


def test_status_fact() -> None:
    assert status_fact("extracting") == "Extracting"
    assert status_fact("paused_for_conflicts") == "Waiting for you"
    assert status_fact("odd_state") == "Odd state"
    assert status_fact("") == "—"


# --- the banner poll ------------------------------------------------------------------------------


def paused_story(cause: str = "backfill") -> dict[str, Any]:
    story = m7("story")
    story["status"] = "paused"
    story["pause"] = {
        "job_id": "01JM7JB0000000000000000009",
        "cause": cause,
        "progress": {"done": 2, "total": 4},  # the schema's shape (Engine #783)
    }
    return story


def test_banner_poll_while_paused(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 200, json=paused_story())
    html = html_of(operator_client, f"{PAGE}?banner=1&cause=backfill")
    assert html.lstrip().startswith('<div id="pause-banner"')
    assert "Paused: backfill in progress (2/4 chunks)" in html
    assert f'hx-get="{PAGE}?banner=1&amp;cause=backfill" {TRIGGER}' in html
    assert paths(engine) == [f"GET {STORY_READ}"]


def test_banner_poll_when_unpaused(operator_client: Client, engine: FakeEngine) -> None:
    """**Complement:** an unpaused story → an empty 200 with no trigger, and neither the
    backfill nor any rating check was read."""
    response = operator_client.get(f"{PAGE}?banner=1&cause=backfill")
    assert response.status_code == 200
    assert response.content == b""
    requested = [r.url.path for r in engine.requests]
    assert requested == [STORY_READ]
    assert not any(p.endswith("/backfill") or "/rating/checks/" in p for p in requested)


def test_banner_for_a_rating_check_is_unchanged(
    operator_client: Client, engine: FakeEngine
) -> None:
    story = paused_story("rating_check")
    story["pause"]["progress"] = {"done": 1, "total": 3}
    engine.respond("GET", STORY_PATH, 200, json=story)
    html = html_of(operator_client, f"{PAGE}?banner=1&cause=rating_check")
    assert "Paused: rating check in progress (1/3)" in html
    assert "/rating/checks/01JM7JB0000000000000000009/" in html


def test_banner_retries_when_the_engine_is_down(
    operator_client: Client, engine: FakeEngine
) -> None:
    engine.fail("GET", STORY_PATH, httpx.ConnectError("down"))
    html = html_of(operator_client, f"{PAGE}?banner=1&cause=backfill_conflicts")
    assert RETRYING_TEXT in html
    assert TRIGGER in html


@pytest.mark.parametrize("status", [500, 502])
def test_banner_keeps_polling_on_a_5xx(
    operator_client: Client, engine: FakeEngine, status: int
) -> None:
    """#353: the story read 5xx-ing while a backfill pauses the story (Engine #783) keeps the
    banner and its poll, with neutral paused text; still no backfill read."""
    # Undeclared in the story read's schema: the live Engine's 500 is a bug (D18).
    engine.respond_undeclared("GET", STORY_PATH, status, json=error_json("internal", "boom"))
    html = html_of(operator_client, f"{PAGE}?banner=1&cause=backfill")
    assert html.lstrip().startswith('<div id="pause-banner"')
    assert "Paused: backfill in progress" in html
    assert "chunks" not in html
    assert RETRYING_TEXT in html
    assert f'hx-get="{PAGE}?banner=1&amp;cause=backfill" {TRIGGER}' in html
    assert paths(engine) == [f"GET {STORY_READ}"]


def test_banner_on_another_4xx_is_empty(operator_client: Client, engine: FakeEngine) -> None:
    """#353's complement: only a 5xx keeps polling; another error still empties the banner."""
    engine.respond("GET", STORY_PATH, 422, json=error_json("validation_error"))
    response = operator_client.get(f"{PAGE}?banner=1&cause=backfill")
    assert response.status_code == 200
    assert response.content == b""


def test_banner_on_another_error_is_empty(operator_client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STORY_PATH, 404, json=error_json("story_not_found"))
    response = operator_client.get(f"{PAGE}?banner=1")
    assert response.status_code == 200
    assert response.content == b""


def test_banner_wins_over_partial(operator_client: Client, engine: FakeEngine) -> None:
    operator_client.get(f"{PAGE}?banner=1&partial=1")
    assert paths(engine) == [f"GET {STORY_READ}"]
