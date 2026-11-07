"""M7's job helpers (#295, E7/E8): ``job_state`` over E7's table, ``job_poll_context``,
``_job_status.html``, ``recent_jobs`` over the #44 log and ``report_rows``."""

import json
import re
from datetime import timedelta
from typing import Any

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from console.jobs import (
    JOB_KINDS,
    PARTIAL_PARAM,
    SLOW_POLL_TRIGGER,
    TWOSTAGE_KINDS,
    JobKind,
    ReportEntry,
    job_poll_context,
    job_state,
    recent_jobs,
    report_rows,
    settle_error_code,
)
from console.models import EngineOperation
from tests.fixtures import engine_fixture

STORY = "01JM7ST0000000000000000001"
OTHER = "01JM7ST0000000000000000002"
JOB = "01JM7JB0000000000000000006"

# E7's table: (kind, status, text, terminal, failed, waiting).
TABLE = [
    ("export", "queued", "Queued", False, False, False),
    ("export", "running", "Exporting…", False, False, False),
    ("export", "ready", "Ready to download", True, False, False),
    ("export", "failed", "Failed", True, True, False),
    ("export", "expired", "Expired (download no longer available)", True, False, False),
    ("import", "queued", "Queued", False, False, False),
    ("import", "running", "Importing", False, False, False),
    ("import", "ready", "Imported", True, False, False),
    ("import", "refused", "Refused (no story was created)", True, True, False),
    ("import", "failed", "Failed (no story was created)", True, True, False),
    ("backfill", "queued", "Queued", False, False, False),
    ("backfill", "extracting", "Extracting", False, False, False),
    ("backfill", "reconciling", "Reconciling", False, False, False),
    ("backfill", "completed", "Completed", True, False, False),
    ("backfill", "cancelled", "Cancelled", True, False, False),
    ("backfill", "failed", "Failed", True, True, False),
    ("backfill", "paused_for_conflicts", "Waiting for you: its conflicts to resolve", False,
     False, True),
    ("certification_run", "queued", "Queued", False, False, False),
    ("certification_run", "running", "Running", False, False, False),
    ("certification_run", "completed", "Completed", True, False, False),
    ("certification_run", "failed", "Failed", True, True, False),
]  # fmt: skip
TRIGGERS = {
    "export": "every 2s",
    "import": "every 2s",
    "backfill": "every 5s",
    "certification_run": "every 5s",
}


@pytest.mark.parametrize(("kind", "status", "text", "terminal", "failed", "waiting"), TABLE)
def test_every_row_of_e7s_table(
    kind: str, status: str, text: str, terminal: bool, failed: bool, waiting: bool
) -> None:
    state = job_state(kind, status)
    assert (state.text, state.terminal, state.failed, state.waiting) == (
        text,
        terminal,
        failed,
        waiting,
    )
    assert state.trigger == TRIGGERS[kind]
    assert state.raw == ""  # a known status has no raw title


def test_the_table_covers_every_status() -> None:
    assert {(k, s) for k, s, *_ in TABLE} == {
        (kind.value, status) for kind, spec in JOB_KINDS.items() for status in spec.statuses
    }
    assert [k.value for k in JobKind] == ["export", "import", "backfill", "certification_run"]
    assert SLOW_POLL_TRIGGER == "every 5s"
    assert PARTIAL_PARAM == "partial"


def test_backfill_counts() -> None:
    extracting = job_state("backfill", "extracting", progress={"chunks_done": 2, "chunks_total": 7})
    assert extracting.text == "Extracting (2/7 chunks)"
    assert (extracting.done, extracting.total) == (2, 7)
    reconciling = job_state(
        "backfill", "reconciling", progress={"reconciled_through_turn": 1200, "last_turn": 3400}
    )
    assert reconciling.text == "Reconciling (through turn 1,200 of 3,400)"
    bare = job_state("backfill", "reconciling", progress={"reconciled_through_turn": None})
    assert bare.text == "Reconciling"
    paused = job_state("backfill", "paused_for_conflicts", progress={"conflicts_open": 2})
    assert paused.text == "Waiting for you: 2 conflicts to resolve"
    assert paused.waiting
    assert not paused.terminal
    one = job_state("backfill", "paused_for_conflicts", progress={"conflicts_open": 1})
    assert one.text == "Waiting for you: 1 conflict to resolve"


def test_import_running_names_its_stage_and_count() -> None:
    state = job_state("import", "running", progress={"stage": "guardrails", "done": 3, "total": 9})
    assert state.text == "Importing — Guardrails (3/9)"
    assert state.text_with_stage("Checking guardrails") == "Importing — Checking guardrails (3/9)"
    assert (
        job_state("import", "running", progress={"done": 3, "total": 9}).text == "Importing (3/9)"
    )


@pytest.mark.parametrize(
    "progress",
    [
        {"chunks_done": 0, "chunks_total": 0},
        {"chunks_done": 2},
        {"chunks_done": 2, "chunks_total": None},
        {"chunks_done": True, "chunks_total": 7},
        None,
    ],
)
def test_a_missing_or_zero_total_leaves_the_count_out(progress: dict[str, Any] | None) -> None:
    """**Complement:** never "—/—" or "0/0"; the bar is indeterminate."""
    state = job_state("backfill", "extracting", progress=progress)
    assert state.text == "Extracting"
    assert state.indeterminate
    html = render(state)
    assert "<progress></progress>" in html
    assert "0/0" not in html
    assert "—/—" not in html


def test_an_unknown_status_keeps_polling_humanised_with_its_raw_value() -> None:
    """**Complement.**"""
    state = job_state("export", "compressing_bundle")
    assert state.text == "Compressing bundle"
    assert not state.terminal
    assert state.raw == "compressing_bundle"
    assert job_poll_context(
        "export", JOB, "compressing_bundle", poll_url="/p/", refresh_url="/r/", body_template="t"
    )["polling"]
    assert '<strong class="job-status-text" title="compressing_bundle">Compressing bundle' in (
        render(state)
    )


@pytest.mark.parametrize(
    ("kind", "status", "polling", "trigger"),
    [
        ("export", "running", True, "every 2s"),
        ("export", "ready", False, "every 2s"),
        ("import", "queued", True, "every 2s"),
        ("import", "refused", False, "every 2s"),
        ("backfill", "extracting", True, "every 5s"),
        ("backfill", "paused_for_conflicts", False, "every 5s"),
        ("backfill", "completed", False, "every 5s"),
        ("certification_run", "running", True, "every 5s"),
        ("certification_run", "failed", False, "every 5s"),
    ],
)
def test_job_poll_context(kind: str, status: str, polling: bool, trigger: str) -> None:
    context = job_poll_context(
        kind,
        JOB,
        status,
        poll_url="/job/?partial=1",
        refresh_url="/job/",
        body_template="console/_job_status.html",
    )
    assert context["polling"] is polling
    assert context["poll_trigger"] == trigger
    assert context["job"] == job_state(kind, status)
    assert context["job_id"] == JOB
    html = render_to_string("console/_job_poll.html", context)
    assert (f'hx-trigger="{trigger}"' in html) is polling


def render(state: Any, **context: Any) -> str:
    return render_to_string("console/_job_status.html", {"job": state, "job_id": JOB, **context})


def test_queued_renders_no_progress_and_running_does() -> None:
    assert "<progress" not in render(job_state("export", "queued"))
    running = render(job_state("import", "running", progress={"done": 3, "total": 9}))
    assert '<progress value="3" max="9">3/9</progress>' in running
    assert f"Job <code>{JOB}</code>" in running
    assert "<progress" not in render(job_state("backfill", "paused_for_conflicts"))


def test_a_failed_export_renders_its_error_block_and_no_facts() -> None:
    error = {"code": "bundle_write_failed", "message": "disk full", "details": {"free": 0}}
    html = render(job_state("export", "failed"), error=error, facts=[("Size", "1 KB")])
    assert '<p class="warning"><strong>Bundle write failed</strong>: disk full</p>' in html
    assert "<summary>More</summary>" in html
    assert "&quot;free&quot;: 0" in html
    assert "job-facts" not in html


def test_terminal_success_shows_the_facts_and_no_error() -> None:
    html = render(job_state("export", "ready"), facts=[("Size", "1 KB")], error=None)
    assert '<dl class="job-facts">' in html
    assert "<dt>Size</dt><dd>1 KB</dd>" in html
    assert "job-error" not in html
    assert "job-facts" not in render(job_state("export", "running"), facts=[("Size", "1 KB")])


def test_a_certification_runs_bare_string_error() -> None:
    html = render(job_state("certification_run", "failed"), error="TimeoutError")
    assert "<strong>TimeoutError</strong>" in html
    assert settle_error_code("TimeoutError") == "TimeoutError"
    assert settle_error_code({"code": "x" * 80}) == "x" * 64
    assert settle_error_code(None) == ""


def test_the_stage_text_replaces_the_humanised_stage() -> None:
    state = job_state("import", "running", progress={"stage": "guardrails", "done": 3, "total": 9})
    html = render(state, stage_text="Re-checking guardrails")
    assert ">Importing — Re-checking guardrails (3/9)</strong>" in html


def test_twostage_kinds() -> None:
    assert TWOSTAGE_KINDS == (
        "prune",
        "import",
        "backfill-start",
        "backfill-resume",
        "upgrade-pinned",
        "certify-profile",
        "certify-story",
    )


# --- recent_jobs ----------------------------------------------------------------------------------


def _row(kind: str, minutes: int, **fields: Any) -> EngineOperation:
    return EngineOperation.objects.create(
        created_at=timezone.now() - timedelta(minutes=minutes),
        operator_username="op",
        principal=fields.pop("principal", "console:op"),
        kind=kind,
        http_method="POST",
        path="/admin/stories/x/export",
        outcome=fields.pop("outcome", "queued"),
        **fields,
    )


@pytest.mark.django_db
def test_recent_jobs_newest_first_this_story_only() -> None:
    old = _row("export_story", 30, story_id=STORY, job_id="01JM7JB0000000000000000001")
    new = _row(
        "export_story",
        5,
        story_id=STORY,
        response={"job_id": "01JM7JB0000000000000000002", "story_id": STORY},
        outcome="ok",
    )
    _row("export_story", 4, story_id=STORY)  # no job id at all: skipped
    _row("export_story", 3, story_id=STORY, job_id="01JM7JB0000000000000000002", replayed=True)
    _row("export_story", 2, story_id=OTHER, job_id="01JM7JB0000000000000000003")
    _row("import_story", 1, story_id=STORY, job_id="01JM7JB0000000000000000004")
    found = recent_jobs(JobKind.EXPORT, story_id=STORY)
    assert [j.job_id for j in found] == ["01JM7JB0000000000000000002", "01JM7JB0000000000000000001"]
    assert found[0].started_at == new.created_at
    assert found[0].outcome == "ok"
    assert found[1].started_at == old.created_at
    assert found[1].principal == "console:op"
    assert "01JM7JB0000000000000000003" not in {j.job_id for j in found}  # **Complement**


@pytest.mark.django_db
def test_recent_imports_fall_back_to_the_stored_story_and_limit_after_dedup() -> None:
    for n in range(3):
        _row(
            "import_story",
            10 - n,
            response={"job_id": f"01JM7JB000000000000000000{n}", "story_id": STORY},
        )
    _row("import_story", 1, job_id="01JM7JB0000000000000000002")  # the same job again
    assert [j.job_id for j in recent_jobs("import", story_id=STORY, limit=2)] == [
        "01JM7JB0000000000000000002",
        "01JM7JB0000000000000000001",
    ]
    assert len(recent_jobs("import")) == 3


def test_recent_jobs_refuses_another_kind() -> None:
    with pytest.raises(ValueError, match="no recent-jobs list"):
        recent_jobs("backfill")
    with pytest.raises(ValueError, match="no recent-jobs list"):
        recent_jobs("nope")


# --- report_rows ----------------------------------------------------------------------------------


def test_report_rows_over_the_ready_import() -> None:
    job = engine_fixture("m7/import_job_ready")
    rows = report_rows(job["report"])
    assert rows.errors == []
    assert rows.warnings == [
        ReportEntry("Imported unsettled", "—", {"outbox_id": "01JM7XB0000000000000000005"})
    ]
    assert rows.warnings[0] == (
        "Imported unsettled",
        "—",
        {"outbox_id": "01JM7XB0000000000000000005"},
    )
    assert rows.counts == [("Events", "42"), ("Facts", "9"), ("Outbox", "2")]


def test_report_rows_tolerates_odd_entries() -> None:
    rows = report_rows(
        {
            "errors": [{"code": "bad_bundle", "message": "no manifest"}, "oops"],
            "warnings": {"code": "lonely"},
            "counts": {"turn10": 1200, "turn2": 3, "events": "many"},
        }
    )
    assert rows.errors[0] == ("Bad bundle", "no manifest", None)
    assert rows.errors[1] == ("Unreadable", "This entry couldn't be read", {"entry": "'oops'"})
    assert rows.warnings == [("Lonely", "—", None)]
    assert rows.counts == [("Events", "many"), ("Turn2", "3"), ("Turn10", "1,200")]


def test_a_non_object_report_is_one_unreadable_error() -> None:
    """**Complement.**"""
    rows = report_rows("x")
    assert rows.errors == [
        ("Unreadable", "The validation report couldn't be read", {"report": "'x'"})
    ]
    assert rows.warnings == []
    assert rows.counts == []
    assert json.dumps(rows.errors[0].more) == '{"report": "\'x\'"}'


def test_the_docstring_states_e8s_mechanics() -> None:
    import console.jobs

    doc = re.sub(r"\s+", " ", console.jobs.__doc__ or "")
    for phrase in (
        "twostage.sign(kind, target_id, body, base)",
        "ignore_warnings: true",
        "EXPIRED_TEXT",
    ):
        assert phrase in doc
