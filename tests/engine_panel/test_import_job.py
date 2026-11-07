"""#321: follow an import to its new story and read its validation report (``engine:import-job``;
Engine #250/#251; owner answer 12; E7, E11, E23), over #292's ``m7/import_job_running.json``,
``import_job_ready.json`` and ``import_job_refused.json`` with inline bodies where states
differ."""

import copy
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from django.contrib.messages import get_messages
from django.http import HttpRequest
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, override_settings
from django.utils.html import escape

from console import job_poll
from console.engine.client import EngineClient
from console.engine.models import ImportJob
from console.engine_access import engine_client_for
from console.held import HELD_TEXT
from console.imports_status import (
    BACKFILL_STARTED_NOTE,
    IMPORT_ERROR_TEXT,
    IMPORT_STAGES,
    NO_ERROR_DETAILS_TEXT,
    RATING_CHECK_REASON_TEXT,
    REPORT_UNREADABLE_TEXT,
    STAGE_TEXT,
    import_report_rows,
    outbox_text,
    parked_text,
    position_html,
    violation_rows,
)
from console.job_poll import POLL_TEMPLATE, RETRYING_TEXT
from console.models import EngineOperation
from console.portability import import_job_url
from console.views import imports as import_views
from console.views import story_pages
from tests.engine_panel.test_import import IMPORT, PROFILES, RATINGS, form_data, import_row
from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

pytestmark = pytest.mark.django_db

JOB = "01JM7JB0000000000000000007"
NEW_STORY = "01JM7ST0000000000000000003"
STATUS = "/admin/stories/imports/{job_id}"
STATUS_PATH = f"/admin/stories/imports/{JOB}"
STORY = "/stories/{story_id}"
URL = f"/engine/import/{JOB}/"
PARTIAL = f"{URL}?partial=1"
NAME = "Harbour Nights"
TRIGGER = 'hx-trigger="every 2s"'


def m7(name: str) -> Any:
    return copy.deepcopy(engine_fixture(f"m7/{name}"))


def job(name: str = "import_job_running", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7(name)
    body.update(changes)
    return body


def new_story(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = m7("story")
    body.update({"id": NEW_STORY, "name": NAME, **changes})
    return body


def violation(turn: int, beat: int | None, policy: str, name: str | None) -> dict[str, Any]:
    """One violation in the Engine's real shape (``HistoryViolation.as_json``): no text."""
    return {
        "position": {"turn": turn, "variant": 0, "beat": beat},
        "policy_id": policy,
        "policy_name": name,
    }


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch, operator: Any, client: Client) -> Iterator[FakeEngine]:
    """The fake behind the frame and the partial; ``client`` signed in as the operator."""
    client.force_login(operator)
    fake = FakeEngine()

    def client_with_fake(request: HttpRequest) -> EngineClient:
        found = engine_client_for(request)
        found._transport = fake.transport
        return found

    monkeypatch.setattr(story_pages, "engine_client_for", client_with_fake)
    monkeypatch.setattr(import_views, "engine_client_for", client_with_fake)
    with override_settings(ENGINE_URL="http://engine.test"):
        yield fake


def serve(fake: FakeEngine, body: dict[str, Any]) -> None:
    """Answer the job read with ``body`` unvalidated (open-enum statuses, odd reports)."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    fake.side_effect("GET", STATUS, answer)


def story_reads(fake: FakeEngine) -> list[str]:
    return [r.url.path for r in fake.requests if r.url.path.startswith("/stories/")]


def status_reads(fake: FakeEngine) -> int:
    return sum(1 for r in fake.requests if r.url.path == STATUS_PATH)


def page(client: Client, url: str = URL) -> str:
    return client.get(url).content.decode()


def block(html: str) -> str:
    return html[html.index('id="job-poll"') :]


# --- running --------------------------------------------------------------------------------------


def test_running_shows_stage_and_progress(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job(progress={"done": 3, "total": 9}))
    response = client.get(URL)
    html = response.content.decode()
    assert response.status_code == 200
    assert "<title>Import job …00000007 · Engine</title>" in html
    assert '<a href="/engine/import/" aria-current="page">Import a bundle</a>' in html
    assert (
        f'<strong class="job-status-text">Importing — {STAGE_TEXT["guardrails"]} (3/9)</strong>'
    ) in html
    assert '<p class="import-stage">Stage 5 of 10</p>' in html
    assert '<progress value="3" max="9">' in html
    assert status_reads(engine) == 1
    partial = page(client, PARTIAL)
    assert partial.lstrip().startswith('<div id="job-poll"')
    assert f'hx-get="{PARTIAL}" {TRIGGER}' in partial
    assert "Stage 5 of 10" in partial
    assert "<title>" not in partial
    # complement: no story read while running, and no report or error yet
    assert story_reads(engine) == []
    assert "import-report" not in html
    assert "No story was created" not in html


@pytest.mark.parametrize("stage", IMPORT_STAGES)
def test_every_stage_has_its_text_and_count(client: Client, engine: FakeEngine, stage: str) -> None:
    engine.respond("GET", STATUS, 200, json=job(stage=stage))
    html = page(client, PARTIAL)
    assert f"Importing — {escape(STAGE_TEXT[stage])}" in html
    assert f"Stage {IMPORT_STAGES.index(stage) + 1} of 10" in html


def test_unknown_stage_is_humanised(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job(stage="later_stage"))
    html = page(client, PARTIAL)
    assert "Importing — Later stage (3/8)" in html
    assert "of 10" not in html
    assert TRIGGER in html


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"stage": "queued"}, id="queued-stage"),
        pytest.param({"stage": None}, id="no-stage"),
        pytest.param({"stage": "guardrails", "progress": {"done": 0, "total": 0}}, id="zero"),
    ],
)
def test_a_running_job_without_a_stage_reads_importing(
    client: Client, engine: FakeEngine, changes: dict[str, Any]
) -> None:
    engine.respond("GET", STATUS, 200, json=job(**changes))
    html = page(client, PARTIAL)
    if changes.get("stage") == "guardrails":  # a stage with no usable total: no count
        assert f"Importing — {STAGE_TEXT['guardrails']}</strong>" in html
        assert "<progress></progress>" in html
        return
    assert '<strong class="job-status-text">Importing</strong>' in html
    assert "<progress></progress>" in html  # indeterminate
    assert "import-stage" not in html
    assert "(3/8)" not in html


def test_a_queued_job_shows_no_bar(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job(status="queued", stage="queued"))
    html = page(client, PARTIAL)
    assert '<strong class="job-status-text">Queued</strong>' in html
    assert "<progress" not in html
    assert "import-stage" not in html
    assert TRIGGER in html


def test_an_unknown_status_keeps_polling(client: Client, engine: FakeEngine) -> None:
    serve(engine, job(status="verifying"))
    html = page(client, PARTIAL)
    assert '<strong class="job-status-text" title="verifying">Verifying</strong>' in html
    assert TRIGGER in html
    assert "import-report" not in html


# --- ready ----------------------------------------------------------------------------------------


def settled_counts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Spy on ``settle_job``: what each call settled (0 or 1 rows)."""
    found: list[int] = []
    real: Callable[..., int] = job_poll.settle_job

    def spy(job_id: str, *, failed: bool, error_code: str = "") -> int:
        settled = real(job_id, failed=failed, error_code=error_code)
        found.append(settled)
        return settled

    monkeypatch.setattr(import_views, "settle_job", spy)
    return found


def test_ready_links_the_new_story(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = import_row(JOB, NEW_STORY)
    settled = settled_counts(monkeypatch)
    engine.respond("GET", STATUS, 200, json=job("import_job_ready"))
    engine.respond("GET", STORY, 200, json=new_story())
    html = page(client)
    assert story_reads(engine) == [f"/stories/{NEW_STORY}"]
    assert (
        f'<p class="import-ready"><a class="import-story" href="/stories/{NEW_STORY}/">'
        f"Imported as {NAME} — open it</a></p>"
    ) in html
    assert '<strong class="job-status-text">Imported</strong>' in html
    assert "Errors (0)" in html
    assert "Warnings (1)" in html
    assert "Imported unsettled" in html
    assert '<tr class="report-count"><th scope="row">Events</th><td>42</td></tr>' in html
    # complement: no trigger, no error, and the #44 row settled ok exactly once over two renders
    assert "hx-trigger" not in html
    assert "No story was created" not in html
    assert "try-again" not in html
    partial = page(client, PARTIAL)
    assert "hx-trigger" not in partial
    assert f"Imported as {NAME} — open it" in partial
    assert len(story_reads(engine)) == 2  # one read per terminal render, nothing cached
    row.refresh_from_db()
    assert row.outcome == EngineOperation.Outcome.OK
    assert sum(settled) == 1
    assert len(settled) == 2


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("fails", id="read-fails"),
        pytest.param("blank", id="blank-name"),
    ],
)
def test_ready_with_a_failed_story_read_shows_the_id(
    client: Client, engine: FakeEngine, answer: str
) -> None:
    engine.respond("GET", STATUS, 200, json=job("import_job_ready"))
    if answer == "fails":
        engine.fail("GET", STORY, httpx.ConnectError("down"))
    else:
        engine.respond("GET", STORY, 200, json=new_story(name=" "))
    html = page(client)
    assert (
        f'<a class="import-story" href="/stories/{NEW_STORY}/">'
        f"Imported as <code>{NEW_STORY}</code> — open it</a>"
    ) in html
    assert "Warnings (1)" in html  # the report still shows
    assert 'class="engine-error' not in html


def test_ready_without_a_story_id_reads_no_story(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job("import_job_ready", story_id=None))
    html = page(client)
    assert '<p class="import-ready">Imported as —</p>' in html
    assert story_reads(engine) == []


def paused_story() -> dict[str, Any]:
    pause = {"job_id": "01JM7JB0000000000000000001", "cause": "backfill", "progress": {}}
    return new_story(status="paused", pause=pause)


def notes(html: str) -> list[str]:
    return re.findall(r'<p class="notice import-note">(.*?)</p>', html)


def test_backfill_and_parked_notes(client: Client, engine: FakeEngine) -> None:
    ready = job("import_job_ready")
    ready["report"]["outbox"] = {"closed_staged_turns": 0, "parked": 2, "reservation_timers": 1}
    engine.respond("GET", STATUS, 200, json=ready)
    engine.side_effect("GET", STORY, lambda r: httpx.Response(200, json=paused_story()))
    html = page(client)
    assert notes(html) == [
        f'<a href="/stories/{NEW_STORY}/backfill/">{BACKFILL_STARTED_NOTE}</a>',
        f'<a href="/stories/{NEW_STORY}/inspect/effects/reconciliation/">2 external effects were'
        " parked unsettled — resolve each in Effects › Reconciliation</a>",  # noqa: RUF001
    ]
    assert "0 staged turns closed, 2 effects parked, 1 reservation timer" in html
    # an active story whose report says a backfill started (it may already have finished)
    ready = job("import_job_ready")
    ready["report"]["profile"] = {"profile_id": "x", "gaps": [], "upgrade_mode": "backfill"}
    engine.respond("GET", STATUS, 200, json=ready)
    engine.respond("GET", STORY, 200, json=new_story())
    assert notes(page(client)) == [
        f'<a href="/stories/{NEW_STORY}/backfill/">{BACKFILL_STARTED_NOTE}</a>'
    ]
    # and with the story read failed, the report alone still says so
    engine.fail("GET", STORY, httpx.ConnectError("down"))
    assert len(notes(page(client))) == 1


def test_the_notes_are_plain_while_unregistered(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D19: ``backfill_url`` answering ``None`` stands in for a build without #308."""
    monkeypatch.setattr(import_views, "backfill_url", lambda story_id: None)
    engine.respond("GET", STATUS, 200, json=job("import_job_ready"))
    engine.side_effect("GET", STORY, lambda r: httpx.Response(200, json=paused_story()))
    assert notes(page(client)) == [BACKFILL_STARTED_NOTE]


@pytest.mark.parametrize(
    "outbox",
    [
        pytest.param(None, id="no-outbox"),
        pytest.param({"parked": 0}, id="zero"),
        pytest.param({"parked": True}, id="bool"),
        pytest.param({"parked": "2"}, id="string"),
    ],
)
def test_a_ready_job_with_neither_shows_neither_note(
    client: Client, engine: FakeEngine, outbox: Any
) -> None:
    """**Complement.**"""
    ready = job("import_job_ready")
    if outbox is not None:
        ready["report"]["outbox"] = outbox
    engine.respond("GET", STATUS, 200, json=ready)
    engine.respond("GET", STORY, 200, json=new_story())
    assert notes(page(client)) == []


def test_the_parked_note_agrees_with_its_count() -> None:
    assert parked_text(1).startswith("1 external effect was parked unsettled")
    assert parked_text(2).startswith("2 external effects were parked unsettled")
    assert parked_text(1200).startswith("1,200 external effects were")


# --- the validation report ------------------------------------------------------------------------

REPORT: dict[str, Any] = {
    "errors": [{"code": "rating_check_failed", "message": "breaks", "path": {"turn": 1}}],
    "warnings": [
        {"code": "provider_unregistered", "provider": "acme"},
        {"code": "imported_unsettled", "outbox_id": "01JM7XB0000000000000000005"},
    ],
    "counts": {"rows10": 1, "rows2": 1200, "events": 42, "facts": "many"},
    "outbox": {"closed_staged_turns": 1, "parked": 2},
    "profile": {"upgrade_mode": None},
    "erasure": {"applied": 0},
}


def test_report_rows() -> None:
    rows = import_report_rows(REPORT)
    assert not rows.unreadable
    assert [(e.code, e.message) for e in rows.errors] == [("Rating check failed", "breaks")]
    assert '"path"' in rows.errors[0].more  # the raw entry in More
    assert [(e.code, e.message) for e in rows.warnings] == [
        ("Provider unregistered", "—"),
        ("Imported unsettled", "—"),
    ]
    assert '"provider": "acme"' in rows.warnings[0].more
    assert rows.counts == [
        ("Events", "42"),
        ("Facts", "many"),
        ("Rows2", "1,200"),
        ("Rows10", "1"),
    ]
    assert rows.outbox == "1 staged turn closed, 2 effects parked, — reservation timers"
    assert [label for label, _ in rows.others] == ["Erasure", "Profile"]


def test_report_rows_complements() -> None:
    """**Complement:** a non-object report is the couldn't-be-read row; an empty one has no
    rows; a non-object outbox is an "other" row."""
    unreadable = import_report_rows("x")
    assert unreadable.unreadable
    assert unreadable.unreadable_text == REPORT_UNREADABLE_TEXT
    assert unreadable.more == '"x"'
    empty = import_report_rows({})
    assert (empty.errors, empty.warnings, empty.counts, empty.outbox) == ([], [], [], None)
    odd = import_report_rows({"outbox": "parked"})
    assert odd.outbox is None
    assert odd.others == [("Outbox", '"parked"')]
    assert (
        outbox_text({"parked": "3"})
        == "— staged turns closed, 3 effects parked, — reservation timers"
    )


def test_the_report_renders_in_order(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job("import_job_ready", report=REPORT))
    engine.respond("GET", STORY, 200, json=new_story())
    html = page(client)
    report = html[html.index('<section class="import-report">') :]
    assert report.index("Errors (1)") < report.index("Warnings (2)") < report.index("Counts")
    assert report.index("Counts") < report.index("Outbox") < report.index("<strong>Erasure")
    counts = re.findall(r'<tr class="report-count"><th scope="row">(.*?)</th>', report)
    assert counts == ["Events", "Facts", "Rows2", "Rows10"]


def test_an_empty_report_shows_every_heading(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job("import_job_ready", report={}))
    engine.respond("GET", STORY, 200, json=new_story())
    html = page(client)
    assert "Errors (0)" in html
    assert "Warnings (0)" in html
    assert '<p class="none report-counts">None</p>' in html


def render_block(body: ImportJob) -> str:
    """The poll block for a job the client's model can't carry (its ``report``/``error`` are
    objects in the Engine's schema, so a non-object answer is #43's ``bad_response`` panel):
    the lenient rendering, straight from the view's block context."""
    request = RequestFactory().get(URL)
    client = EngineClient("http://engine.test", "key")
    context = import_views.import_job_block(request, client, JOB, body)
    return render_to_string(POLL_TEMPLATE, context, request=request)


def test_an_unreadable_report_renders_its_row(client: Client, engine: FakeEngine) -> None:
    odd: Any = "x"  # a non-object report, as the lenient render must take it
    html = render_block(ImportJob(job_id=JOB, status="refused", story_id=None, report=odd))
    assert f"<td>{escape(REPORT_UNREADABLE_TEXT)}</td>" in html
    assert "<pre>&quot;x&quot;</pre>" in html
    assert "Errors (" not in html
    # through the client, a non-object report is #43's panel (the schema says object)
    serve(engine, job("import_job_ready", report="x"))
    assert 'class="engine-error' in page(client)


def test_no_report_renders_no_section(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 200, json=job("import_job_ready", report=None))
    engine.respond("GET", STORY, 200, json=new_story())
    assert "import-report" not in page(client)


# --- refused or failed ----------------------------------------------------------------------------


def refused(**error: Any) -> dict[str, Any]:
    body = job("import_job_refused")
    body["error"] = error
    return body


def test_refused_rating_check_lists_violations(
    client: Client, engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = import_row(JOB, NEW_STORY)
    violations = [
        violation(3, 2, "01JM7PO0000000000000000001", "No graphic violence"),
        violation(7, None, "01JM7PO0000000000000000002", None),
    ]
    body = job("import_job_refused")
    body["error"]["details"] = {"violations": violations}
    body["report"] = {
        "errors": [{"code": "rating_check_failed", "path": v["position"], **v} for v in violations],
        "warnings": [],
        "counts": {"events": 12},
    }
    engine.respond("GET", STATUS, 200, json=body)
    html = page(client)
    assert '<strong class="job-status-text">Refused (no story was created)</strong>' in html
    error = html[html.index('<div class="import-error">') :]
    assert error.index("No story was created") < error.index("Rating check failed")
    assert escape(IMPORT_ERROR_TEXT["rating_check_failed"]) in error
    assert '<p class="error-message">turn 3 breaks the Teen rating</p>' in error
    assert "<caption>Guardrail violations (2)</caption>" in error
    cells = re.findall(
        r'<tr class="violation"><td class="policy">(.*?)</td><td class="position">(.*?)</td>',
        error,
    )
    assert cells == [
        ("No graphic violence", "turn 3 · beat 2"),
        ("<code>01JM7PO0000000000000000002</code>", "turn 7"),
    ]
    assert error.index("Guardrail violations") < error.index('class="error-details"')
    assert error.index('class="error-details"') < error.index('class="import-report"')
    assert "Errors (2)" in html
    assert '<p class="try-again"><a href="/engine/import/">Try again</a></p>' in html
    # complement: no excerpt column, no link to any story and no story read
    assert "<th>Excerpt</th>" not in html
    assert 'href="/stories/' not in block(html)
    assert story_reads(engine) == []
    assert "hx-trigger" not in html
    row.refresh_from_db()
    assert row.outcome == EngineOperation.Outcome.ERROR
    assert row.error_code == "rating_check_failed"


def test_violation_rows_are_lenient() -> None:
    table = violation_rows(
        [
            {
                "position": {"kind": "story_image", "id": "01JM7MD0000000000000000001"},
                "policy_id": "p1",
                "policy_name": "Images",
                "code": "image_unchecked",
            },
            {"position": [4, 1, 0, 3, 0], "policy_name": "Words", "excerpt": "a line"},
            "odd",
            {"position": "somewhere"},
        ]
    )
    assert table is not None
    assert table.has_excerpt
    assert [(str(r.policy), str(r.position), r.excerpt) for r in table.rows] == [
        ("Images · Image unchecked", "Story image <code>01JM7MD0000000000000000001</code>", None),
        ("Words", "turn 4 · v1 · beat 3", "a line"),
        ("—", "—", None),
        ("—", "—", None),
    ]
    assert table.rows[2].more == '"odd"'
    assert violation_rows([]) is None
    assert violation_rows({"x": 1}) is None
    assert str(position_html([2, 1])) == "turn 2 · v1"


def test_an_excerpt_shows_in_its_column(client: Client, engine: FakeEngine) -> None:
    body = refused(
        code="rating_check_failed",
        message="m",
        details={"violations": [{**violation(1, 0, "p", "P"), "excerpt": "too far"}]},
    )
    engine.respond("GET", STATUS, 200, json=body)
    html = page(client)
    assert "<th>Excerpt</th>" in html
    assert '<td class="excerpt"><q>too far</q></td>' in html


@pytest.mark.parametrize(
    ("details", "reason"),
    [
        pytest.param({"violations": [], "reason": "no_models"}, "no_models", id="no-models"),
        pytest.param({"violations": [], "reason": "new_reason"}, "New reason", id="unknown"),
        pytest.param({"violations": []}, None, id="empty"),
    ],
)
def test_a_rating_check_without_violations(
    client: Client, engine: FakeEngine, details: dict[str, Any], reason: str | None
) -> None:
    engine.respond(
        "GET",
        STATUS,
        200,
        json=refused(code="rating_check_failed", message="m", details=details),
    )
    html = page(client)
    assert '<table class="violations">' not in html
    if reason is None:
        assert "error-reason" not in html
    else:
        shown = escape(RATING_CHECK_REASON_TEXT.get(reason, reason))
        assert f'<p class="error-reason">{shown}</p>' in html


@pytest.mark.parametrize(
    ("status", "code"),
    [*[("refused", code) for code in IMPORT_ERROR_TEXT], ("failed", "brand_new_code")],
)
def test_failed_codes_are_plain(client: Client, engine: FakeEngine, status: str, code: str) -> None:
    body = refused(code=code, message="the Engine's words", details={"value": 1})
    body["status"] = status
    engine.respond("GET", STATUS, 200, json=body)
    html = page(client)
    words = "Refused" if status == "refused" else "Failed"
    assert f"{words} (no story was created)</strong>" in html
    assert '<p class="warning no-story">No story was created</p>' in html
    humanised = code.replace("_", " ").capitalize()
    assert f'<strong title="{code}">{humanised}</strong>' in html
    if code in IMPORT_ERROR_TEXT:
        text = escape(IMPORT_ERROR_TEXT[code])
        assert f'<p class="error-text">{text}</p>' in html
    else:
        assert "error-text" not in html
    assert '<p class="error-message">the Engine&#x27;s words</p>' in html
    assert "&quot;value&quot;: 1" in html  # details in More
    assert "Try again" in html


def test_a_corrupt_bundle_names_its_reason(client: Client, engine: FakeEngine) -> None:
    for reason, shown in (
        ("checksum_mismatch", "a member doesn&#x27;t match its checksum"),
        ("odd_thing", "odd thing"),
    ):
        body = refused(code="corrupt_bundle", message="bad", details={"reason": reason})
        engine.respond("GET", STATUS, 200, json=body)
        html = page(client)
        assert (
            '<p class="error-text">The bundle is damaged or isn&#x27;t an Epistrel bundle: '
            f"{shown}</p>"
        ) in html


@pytest.mark.parametrize("error", [None, "boom", {"message": "no code"}])
def test_an_error_without_details(client: Client, engine: FakeEngine, error: Any) -> None:
    odd: Any = error
    if error == "boom":  # a bare string: the client's model carries only an object or null
        html = render_block(ImportJob(job_id=JOB, status="failed", story_id=None, error=odd))
    else:
        body = job("import_job_refused")
        body["error"] = error
        serve(engine, body)
        html = page(client)
    assert '<p class="warning no-story">No story was created</p>' in html
    assert f'<p class="error-text">{NO_ERROR_DETAILS_TEXT}</p>' in html
    assert ('class="error-details"' in html) is (error is not None)


# --- errors stay in the frame ---------------------------------------------------------------------

NOT_FOUND = {"error": {"code": "job_not_found", "message": "no such job", "details": {}}}


def test_unknown_job_is_not_found(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 404, json=NOT_FOUND)
    response = client.get(URL)
    html = response.content.decode()
    assert response.status_code == 404
    assert '<p class="not-found">Not found (no such import)</p>' in html
    assert "<title>Import job …00000007 · Engine</title>" in html
    partial = client.get(PARTIAL)
    assert partial.status_code == 200
    assert "Not found (no such import)" in partial.content.decode()
    assert "hx-trigger" not in partial.content.decode()


def test_a_failed_poll_read_follows_poll_failure(client: Client, engine: FakeEngine) -> None:
    """M3's ``poll_failure`` unchanged: no answer or a 503 keeps polling with
    ``RETRYING_TEXT``; anything else shows the panel with no trigger."""
    engine.fail("GET", STATUS, httpx.ConnectError("down"))
    html = page(client, PARTIAL)
    assert RETRYING_TEXT in html
    assert TRIGGER in html


def test_a_503_poll_read_keeps_polling(client: Client, engine: FakeEngine) -> None:
    engine.respond(
        "GET",
        STATUS,
        503,
        json={"error": {"code": "engine_unavailable", "message": "busy", "details": {}}},
    )
    html = page(client, PARTIAL)
    assert RETRYING_TEXT in html
    assert TRIGGER in html


def test_any_other_failed_poll_read_shows_the_panel(client: Client, engine: FakeEngine) -> None:
    engine.respond_undeclared(
        "GET", STATUS, 500, json={"error": {"code": "internal", "message": "x", "details": {}}}
    )
    html = page(client, PARTIAL)
    assert 'class="engine-error' in html
    assert "hx-trigger" not in html
    assert RETRYING_TEXT not in html
    response = client.get(URL)  # the full page: #43's panel inside the frame
    assert 'class="engine-error' in response.content.decode()
    assert "<title>Import job …00000007 · Engine</title>" in response.content.decode()


HELD = {"error": {"code": "erasure_pending", "message": "held", "details": {}}}


def test_erasure_pending_is_the_held_fragment(client: Client, engine: FakeEngine) -> None:
    engine.respond("GET", STATUS, 503, json=HELD)
    partial = page(client, PARTIAL)
    assert partial.lstrip().startswith('<div id="job-poll"')
    assert HELD_TEXT in partial
    assert "hx-trigger" not in partial
    assert RETRYING_TEXT not in partial
    response = client.get(URL)
    assert response.status_code == 503
    assert HELD_TEXT in response.content.decode()


# --- registration ---------------------------------------------------------------------------------


def test_linked_once_registered(
    client: Client, engine: FakeEngine, settings: Any, tmp_path: Path
) -> None:
    """#320's 202 now redirects here with its flash, and E6's ``import_job_url`` (which #295's
    importing page and the recent-imports list use) resolves under the real urlconf."""
    settings.DATA_DIR = tmp_path
    engine.respond("GET", RATINGS, 200, json=engine_fixture("list_content_ratings"))
    engine.respond("GET", PROFILES, 200, json=m7("presets"))
    engine.respond("GET", PROFILES, 200, json=m7("presets"), query={"preset": "true"})
    engine.respond("POST", IMPORT, 202, json=m7("import_started"))
    response = client.post("/engine/import/", form_data())
    assert response.status_code == 303
    assert response["Location"] == URL
    flashes = [str(m) for m in get_messages(response.wsgi_request)]
    assert flashes == [
        f"Import started: job <code>{JOB}</code> — idempotency key "
        "<code>01JM7KY0000000000000000001</code>"
    ]
    assert import_job_url(JOB) == URL
