"""E3's session view, E5's rewrite refusals, E6's URL helpers and E2's lines (#294):
``console/rewrites.py`` over #292's ``m7/`` fixtures.

The Demo's fixture rendering (E3's partial over ``m7/rewrite_session.json``)::

    uv run python manage.py shell -c "from django.template.loader import render_to_string; \\
    from console.engine.models import RewriteSession; from console.rewrites import session_view; \\
    from tests.fixtures import engine_fixture; \\
    s = RewriteSession.from_json(engine_fixture('m7/rewrite_session')); \\
    print(render_to_string('console/rewrites/_session.html', \\
    {'view': session_view(s, s.story_id, {})}))"
"""

import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
from django.template.loader import render_to_string
from django.test import override_settings

from console.engine.client import EngineError, EngineUnreachable
from console.engine.models import RewriteSession
from console.rewrites import (
    AMENDMENT_TEXT,
    AS_RECORDED_LINE,
    AUTHOR_FACT_LIMIT_TEXT,
    BASE_MISMATCH_TEXT,
    BEAT_REQUIRED_TEXT,
    CONFLICT_REASON_TEXT,
    DIRECTIVE_TEXT,
    INACTIVE_TEXT,
    KIND_TEXT,
    MERGE_INVALID_TEXT,
    NOT_EDITABLE_TEXT,
    OMNISCIENT_LINE,
    OMNISCIENT_REQUIRED_TEXT,
    OPEN_STATES,
    PIPELINE_CHANGED_TEXT,
    PREVIEW_PENDING,
    REACH_FAILED_TEXT,
    REACH_TRUNCATED_TEXT,
    STALE_COUNT_TEXT,
    STALE_TEXT,
    STATE_TEXT,
    SUPERSEDED_TEXT,
    TERMINAL_STATES,
    TRUNCATED_TEXT,
    SessionAction,
    amendment_url,
    amendments_url,
    expires_text,
    radius_rows,
    rewrite_refusal,
    rewrite_session_url,
    rewrites_url,
    session_view,
    stale_reason_refs,
)
from console.world import WorldRefusal, world_refusal
from tests.fixtures import engine_fixture
from tests.inspect.helpers import full_v4

STORY = "01JM7ST0000000000000000001"
SESSION = "01JM7RW0000000000000000001"
M7_PAGES = "tests.urls_with_m7_pages"
BARE = "tests.urls_bare"
NAMES = {
    "01JM7CH0000000000000000001": "Mara (player)",
    "01JM7CH0000000000000000002": "Alice",
    "01JM7CH0000000000000000003": "Bob",
}
VERB = "rewrite the story"


def parsed(body: Any) -> RewriteSession:
    found = RewriteSession.from_json(body)
    assert found is not None
    return found


def session(name: str, **changes: Any) -> RewriteSession:
    body: dict[str, Any] = engine_fixture(f"m7/{name}")
    body.update(changes)
    return parsed(body)


def view(name: str, **changes: Any) -> Any:
    return session_view(session(name, **changes), STORY, NAMES)


def refused(code: str, status: int = 409, message: str = "refused", **details: Any) -> Any:
    return rewrite_refusal(EngineError(code, message, details, status), STORY, VERB)


# --- the tables (E3) ----------------------------------------------------------------------------


def test_the_state_and_kind_tables() -> None:
    assert STATE_TEXT == {
        "awaiting_answers": "Waiting for your answers",
        "awaiting_approval": "Ready to apply",
        "applying": "Applying…",
        "applied": "Applied",
        "cancelled": "Cancelled",
        "expired": "Expired",
        "abandoned": "Abandoned",
    }
    assert frozenset({"applied", "cancelled", "expired", "abandoned"}) == TERMINAL_STATES
    assert frozenset({"awaiting_answers", "awaiting_approval"}) == OPEN_STATES
    assert KIND_TEXT == {
        "instruction": "Rewrite",
        "line_edit": "Line edit",
        "directive": "Directive",
        "directed_resolution": "Directed regenerate",
        "undo": "Undo",
        "redo": "Redo",
        "entity_merge": "Entity merge",
    }


def test_the_sentences_are_exact() -> None:
    assert CONFLICT_REASON_TEXT == {
        "dependent_amendment": "A later amendment relies on this",
        "state_presupposed": "A line presupposes the State's old value",
        "player_authored": "The player wrote this",
        "decision_effects": "A decision here committed external effects",
    }
    assert PREVIEW_PENDING == "The preview appears once the required questions are answered"
    assert TRUNCATED_TEXT == (
        "The Engine stopped counting what this touches — there may be more than shown"
    )
    assert REACH_TRUNCATED_TEXT == (
        "The search hit its call cap, so some lines may be missed — this session previews first"
    )
    assert STALE_COUNT_TEXT == "Recomputed {n} time(s) because the story changed"
    assert OMNISCIENT_LINE == (
        "Viewpoint: Omniscient — history rewrites read and change the whole story"
    )
    assert AS_RECORDED_LINE == (
        "Viewpoint: Omniscient — the history as originally recorded is read only as omniscient"
    )
    assert OMNISCIENT_REQUIRED_TEXT == (
        "The Engine refused a character viewpoint here — this page reads as omniscient only "
        "(a Console bug; report it)"
    )


# --- session_view (E3) --------------------------------------------------------------------------


def test_an_open_session_waits_for_answers() -> None:
    shown = view("rewrite_open")
    assert shown.state_text == "Waiting for your answers"
    assert shown.kind_text == "Rewrite"
    assert shown.preview is None
    (question,) = shown.questions
    assert question.severity == "High"
    assert question.required is True
    assert question.recommended_id == "o1"
    assert [(o.id, o.recommended) for o in question.options] == [("o1", True), ("o2", False)]
    assert question.options[0].label_text.endswith(" (recommended)")
    assert question.why_text.startswith("Why: Bob's line is his own sincere claim")
    assert question.answered is None
    base = f"/stories/{STORY}/rewrites/{SESSION}/"
    # The real urlconf: #298 registered Answer, #299 Cancel (its link carries the facts).
    cancel = f"{base}cancel/?" + urlencode(
        {
            "kind": "instruction",
            "state": "awaiting_answers",
            "instruction": "Bob never forged the ledger; he only copied it",
        }
    )
    assert shown.actions == (
        SessionAction("Answer", f"{base}answers/", "post"),
        SessionAction("Cancel", cancel, "get"),
    )
    with override_settings(ROOT_URLCONF=M7_PAGES):
        linked = view("rewrite_open")
    assert linked.actions == shown.actions
    assert linked.answer_url == f"{base}answers/"
    assert [a.label for a in linked.footer_actions] == ["Cancel"]


def test_a_planned_session_is_ready_to_apply() -> None:
    shown = view("rewrite_session")
    assert shown.state_text == "Ready to apply"
    preview = shown.preview
    assert preview is not None
    assert preview.base_text == "Planned at story revision 20"
    assert len(preview.changes) == 2
    assert preview.changes[0].summary == "Bob copied the ledger (was: forged it)"
    (conflict,) = preview.conflicts
    assert conflict.severity == "High"
    assert conflict.reason_text == "A later amendment relies on this"
    assert conflict.options_text == "Rewrite, Drop, Keep, Cascade undo"
    assert conflict.excerpt == "Bob confesses the forgery"
    (cascaded,) = preview.cascaded
    assert (cascaded.action_text, cascaded.severity) == ("Re evaluate", "Low")
    assert len(preview.incidental) == 1
    assert preview.truncated is True
    (refusal,) = preview.refused
    assert refusal.kind_text == "External owned"
    (notice,) = preview.notifications
    assert notice.kind_text == "External effect"
    assert notice.summary == "The bank was already told about the forged ledger"
    assert [heading for heading, _ in preview.extras] == ["Relabels"]
    assert preview.reach is None
    answered = shown.questions[0].answered
    assert answered is not None
    assert answered.text == "Rewrite Bob's line: he only copied the ledger (recommended)"
    assert (answered.by, answered.at) == ("console:nate", "2026-11-05T10:01:00Z")
    with override_settings(ROOT_URLCONF=M7_PAGES):
        linked = view("rewrite_session")
    assert [a.label for a in linked.actions] == ["Apply", "Cancel"]
    assert linked.actions[0].method == "post"
    assert "Answer" not in [a.label for a in linked.actions]
    assert linked.answer_url is None


def test_an_applied_session_has_no_actions_and_links_its_amendment() -> None:
    shown = view("rewrite_session_applied")
    assert shown.state_text == "Applied"
    assert shown.actions == ()
    assert shown.result_amendment_id == "01JM7AM0000000000000000003"
    # #302 registered ``stories:amendment``: linked under the real urlconf too.
    assert shown.result_amendment_url == f"/stories/{STORY}/amendments/01JM7AM0000000000000000003/"
    assert shown.report_more is not None
    assert shown.report_more[0] == "1 resolution"  # ``applied`` is a map, not a list
    assert '"resolutions"' in shown.report_more[1]
    with override_settings(ROOT_URLCONF=M7_PAGES):
        linked = view("rewrite_session_applied")
    assert linked.actions == ()
    assert linked.result_amendment_url == (
        f"/stories/{STORY}/amendments/01JM7AM0000000000000000003/"
    )


def test_the_report_counts_lists() -> None:
    report = {"applied": [{"k": 1}, {"k": 2}], "resolutions": [{"r": 1}]}
    shown = view("rewrite_session_applied", report=report)
    assert shown.report_more is not None
    assert shown.report_more[0] == "2 changes applied · 1 resolution"


def test_a_lite_session_shows_its_reach() -> None:
    preview = view("rewrite_session_lite").preview
    assert preview is not None
    assert preview.reach is not None
    assert preview.reach.text == "Searched turns 1–4 (Model searched)"  # noqa: RUF001
    assert preview.reach.truncated_text == REACH_TRUNCATED_TEXT


def test_a_reach_without_searched_turns_reads_dashes() -> None:
    body = engine_fixture("m7/rewrite_session_lite")
    body["preview"]["reach"] = {"method": "model_searched", "searched": None, "truncated": False}
    preview = session_view(parsed(body), STORY, {}).preview
    assert preview is not None
    assert preview.reach is not None
    assert preview.reach.text == "Searched turns —–— (Model searched)"  # noqa: RUF001
    assert preview.reach.truncated_text == ""


def test_a_directed_regenerate_holds_its_reply() -> None:
    shown = view("rewrite_session_directed")
    assert shown.kind_text == "Directed regenerate"
    assert shown.held_render is not None
    assert shown.held_render.message_text == 'Bob lowers his eyes. "I forged it, Alice."'
    assert shown.held_render.calls_text == "3 model calls to replay"
    one = view(
        "rewrite_session_directed", held_render={"message_text": "x", "model_calls": 1}
    ).held_render
    assert one is not None
    assert one.calls_text == "1 model call to replay"


def test_an_undo_names_its_target() -> None:
    shown = view("undo_session")
    assert shown.kind_text == "Undo"
    assert shown.target_label == "Undoes"
    assert shown.target_amendment_id == "01JM7AM0000000000000000001"
    assert view("rewrite_open", target_amendment_id="01JM7AM0000000000000000001").target_label == (
        "Target amendment"
    )


def test_an_unknown_state_is_humanised_with_no_actions() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        shown = view("rewrite_open", state="later_state")
    assert shown.state_text == "Later state"
    assert shown.state == "later_state"  # the partial's ``title``
    assert "later_state" not in TERMINAL_STATES | OPEN_STATES
    assert shown.actions == ()
    assert shown.expires_text == ""


def test_the_stale_count_and_last_error() -> None:
    assert view("rewrite_open").stale_text == ""
    assert view("rewrite_open", stale_count=1).stale_text == (
        "Recomputed 1 time because the story changed"
    )
    assert view("rewrite_open", stale_count=3).stale_text == (
        "Recomputed 3 times because the story changed"
    )
    error = {"code": "model_timeout", "message": "The planner timed out"}
    shown = view("rewrite_open", last_error=error)
    assert shown.last_error is not None
    assert shown.last_error[:2] == ("Model timeout", "The planner timed out")
    assert '"model_timeout"' in shown.last_error[2]


def state_html(shown: Any) -> str:
    return render_to_string("console/rewrites/_state.html", {"view": shown})


STALE_ERROR_REASONS = [
    {"kind": "record_changed", "refs": [{"table": "events", "id": "01JM7EV0000000000000000001"}]},
    {"kind": "state_changed", "refs": []},
    {"kind": "record_changed", "refs": []},
]


def test_a_message_less_last_error_reads_its_reasons() -> None:
    """#350: the Engine's stale apply stores ``{code, reasons, at}`` with no message; the line
    reads the reasons as E5 does (humanised, de-duplicated, Engine order), never ": —"."""
    error = {"code": "rewrite_stale", "reasons": STALE_ERROR_REASONS, "at": "2026-11-07"}
    shown = view("rewrite_open", last_error=error)
    assert shown.last_error is not None
    assert shown.last_error[:2] == ("Rewrite stale", "Record changed; State changed")
    assert '"record_changed"' in shown.last_error[2]
    html = state_html(shown)
    assert '<p class="warning">Rewrite stale: Record changed; State changed</p>' in html
    assert "Rewrite stale: —" not in html


def test_a_message_less_last_error_without_reasons_reads_the_code_alone() -> None:
    """#350's complement: no message and no reasons is the humanised code, with no dash."""
    for error in (
        {"code": "rewrite_stale"},
        {"code": "rewrite_stale", "reasons": []},
        {"code": "rewrite_stale", "reasons": "not a list"},
    ):
        shown = view("rewrite_open", last_error=error)
        assert shown.last_error is not None
        assert shown.last_error[:2] == ("Rewrite stale", "")
        html = state_html(shown)
        assert '<p class="warning">Rewrite stale</p>' in html
        assert "Rewrite stale:" not in html


def test_a_last_error_with_a_message_is_unchanged() -> None:
    """#350: a message wins over reasons, and renders "<code>: <message>" as before."""
    error = {"code": "provider_timeout", "message": "the model timed out", "reasons": ["x"]}
    shown = view("rewrite_open", last_error=error)
    assert shown.last_error is not None
    assert shown.last_error[:2] == ("Provider timeout", "the model timed out")
    assert '<p class="warning">Provider timeout: the model timed out</p>' in state_html(shown)


def test_the_modes_read_back() -> None:
    shown = view(
        "rewrite_open", resolution_mode={"threshold": "low"}, preview_mode="apply_directly"
    )
    assert shown.resolution_text == "Ask about high- and low-severity conflicts"
    assert shown.preview_mode_text == "Apply directly"
    assert view("rewrite_open").pipeline_text == "Structured"


@pytest.mark.parametrize(
    ("minutes", "text"),
    [(0.5, "expires in <1m"), (42, "expires in 42m"), (185, "expires in 3h 5m"), (-1, "")],
)
def test_expires_text(minutes: float, text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
    monkeypatch.setattr("console.rewrites.timezone.now", lambda: now)
    at = now.timestamp() + minutes * 60
    stamp = datetime.fromtimestamp(at, UTC).isoformat().replace("+00:00", "Z")
    assert expires_text(stamp, "awaiting_answers") == text
    assert expires_text(stamp, "applied") == ""
    assert expires_text("not a time", "awaiting_answers") == ""


def test_a_question_that_does_not_parse_is_kept_raw() -> None:
    body = engine_fixture("m7/rewrite_open")
    body["questions"].append({"id": "01JM7QN0000000000000000009"})
    shown = session_view(parsed(body), STORY, NAMES)
    assert len(shown.questions) == 1
    assert shown.rejected_questions == ('{"id":"01JM7QN0000000000000000009"}',)


def test_answered_question_variants() -> None:
    body = engine_fixture("m7/rewrite_open")
    question = body["questions"][0]
    question.update(answer_text="He copied it", answered_by="console:nate")
    shown = session_view(parsed(body), STORY, {})
    answered = shown.questions[0].answered
    assert answered is not None
    assert (answered.text, answered.quoted) == ("He copied it", True)
    question.update(answer_text=None, answer_option_id="o2", accepted_recommendation=False)
    answered = session_view(parsed(body), STORY, {}).questions[0].answered
    assert answered is not None
    assert answered.text == "Keep the line: Bob lied to Alice"
    question.update(answer_option_id=None, accepted_recommendation=None)
    answered = session_view(parsed(body), STORY, {}).questions[0].answered
    assert answered is not None
    assert answered.text == "—"
    assert answered.more is not None


# --- radius_rows / change_rows (E3, E23) --------------------------------------------------------


def test_malformed_radius_entries_never_raise() -> None:
    rows = radius_rows([{}, "x", {"record": 3}], "irreducible", {})
    assert len(rows) == 3
    assert all(row.record_text == "—" for row in rows)
    assert [row.more for row in rows] == ["{}", '"x"', '{"record":3}']


def test_radius_records_positions_and_order() -> None:
    entries = [
        {"record": {"table": "events", "id": "01JM7EV0000000000000000001"}, "severity": "low"},
        {"record": {"table": "characters", "id": "01JM7CH0000000000000000003"}, "severity": "x"},
        {
            "record": {"table": "facts", "id": "01JM7FA0000000000000000001"},
            "severity": "high",
            "position": [12, 2, 0, 3, 0],
        },
        {"record": {"table": "events", "id": "01JM7EV0000000000000000002"}, "severity": "low"},
    ]
    rows = radius_rows(entries, "irreducible", NAMES)
    assert [r.severity for r in rows] == ["High", "Low", "Low", "X"]
    assert rows[0].position_text == "turn 12 · v2 · beat 3"
    assert rows[0].record_text == "Facts …00000001"
    assert rows[1].record_text == "Events …00000001"
    assert rows[2].record_text == "Events …00000002"
    assert rows[3].record_text == "Characters Bob"
    kept = radius_rows(entries, "cascaded", NAMES)
    assert [r.severity for r in kept] == ["Low", "X", "High", "Low"]  # only conflicts sort
    bad = radius_rows([{"position": [1, 2, 3, True, 0]}, {"position": [1, 2]}], "cascaded", {})
    assert [r.position_text for r in bad] == ["—", "—"]
    merged = radius_rows(
        [{"record": {"table": "entities", "id": "01JM7EN0000000000000000001"}}],
        "incidental",
        NAMES,
        entity_names={"01JM7EN0000000000000000001": "The ledger"},
    )
    assert merged[0].record_text == "Entities The ledger"


# --- rewrite_refusal (E5) -----------------------------------------------------------------------

RECORD_REF = {"table": "events", "id": "01JM7EV0000000000000000001"}
STALE_REASONS = [
    {"kind": "record_changed", "refs": [RECORD_REF]},
    {"kind": "new_dependent", "refs": []},
    {"kind": "record_changed", "refs": []},
]


def test_rewrite_stale_names_its_reason_kinds() -> None:
    refusal = refused("rewrite_stale", reasons=STALE_REASONS)
    assert refusal == WorldRefusal(f"{STALE_TEXT} (Record changed; New dependent)")
    assert RECORD_REF["id"] not in refusal.text
    refs = stale_reason_refs({"reasons": STALE_REASONS})
    assert refs[0] == ("Record changed", [RECORD_REF])
    assert [kind for kind, _ in refs] == ["Record changed", "New dependent"]


@pytest.mark.parametrize(
    ("reasons", "suffix"),
    [(["keys_changed"], " (Keys changed)"), ([{}], ""), ([], ""), (None, "")],
)
def test_rewrite_stale_complements(reasons: Any, suffix: str) -> None:
    assert refused("rewrite_stale", reasons=reasons).text == f"{STALE_TEXT}{suffix}"


def test_rewrite_stale_by_reason() -> None:
    assert refused("rewrite_stale", reason="base_mismatch").text == BASE_MISMATCH_TEXT
    assert BASE_MISMATCH_TEXT == (
        "This preview is no longer the session's current plan — review the new preview"
    )
    assert refused("rewrite_stale", reason="pipeline_changed").text == PIPELINE_CHANGED_TEXT
    assert PIPELINE_CHANGED_TEXT == (
        "The story's pipeline changed, so the Engine abandoned this session — open a new rewrite"
    )


FIXED_ROWS: list[tuple[str, int, dict[str, Any], str, str | None]] = [
    (
        "rewrite_session_closed",
        409,
        {"state": "applied"},
        "This session has already ended (Applied)",
        None,
    ),
    ("rewrite_session_closed", 409, {}, "This session has already ended (closed)", None),
    (
        "rewrite_session_closed",
        409,
        {"state": "later"},
        "This session has already ended (Later)",
        None,
    ),
    ("rewrite_session_not_found", 404, {}, "No such rewrite session for this story", None),
    ("rewrite_questions_pending", 409, {}, "Answer the required questions before applying", None),
    (
        "rewrite_apply_in_progress",
        409,
        {},
        "This session is already being applied — reload in a moment",
        None,
    ),
    (
        "record_in_flight",
        409,
        {},
        "A turn is writing to what this plan touches — try again once it ends",
        None,
    ),
    (
        "rewrite_apply_failed",
        503,
        {},
        "Applying failed and nothing was written — the session is ready to apply again",
        None,
    ),
    (
        "rewrite_unparseable",
        422,
        {},
        "The Engine couldn't read that as a change to the story — rephrase it",
        "instruction",
    ),
    ("rewrite_noop", 422, {}, "That wouldn't change anything in the story", "instruction"),
    (
        "base_revision_required",
        422,
        {},
        "This apply named no plan revision — reload the session",
        None,
    ),
    (
        "steer_budget_exceeded",
        409,
        {},
        "The queued steering for the next reply is already at its limit (500 tokens) — play a "
        "turn first",
        None,
    ),
    ("rewrite_reach_failed", 503, {}, REACH_FAILED_TEXT, None),
    ("rewrite_question_superseded", 409, {}, SUPERSEDED_TEXT, None),
    ("beat_required", 422, {"beats": [1, 2]}, BEAT_REQUIRED_TEXT, None),
    (
        "line_not_editable",
        422,
        {"event_kind": "state_change"},
        "That isn't an editable line (it's a State change, not a message of the transcript)",
        None,
    ),
    (
        "line_not_editable",
        422,
        {},
        "That isn't an editable line (not a message of the transcript)",
        None,
    ),
    (
        "author_fact_limit",
        409,
        {"max": 20},
        "This story already keeps 20 lasting author facts, the most it can — undo one (rewind "
        "past it) or nudge instead",
        None,
    ),
    (
        "merge_invalid",
        422,
        {"reason": "too_few"},
        "Pick at least two entities to merge",
        "entity_ids",
    ),
    (
        "merge_invalid",
        422,
        {"reason": "not_in_story"},
        "One of those entities isn't in this story",
        "entity_ids",
    ),
    (
        "merge_invalid",
        422,
        {"reason": "type_mismatch"},
        "Only entities of the same type can be merged",
        "entity_ids",
    ),
    (
        "merge_invalid",
        422,
        {"reason": "already_same"},
        "Those entities are already one referent",
        "entity_ids",
    ),
    ("amendment_not_found", 404, {}, "No such amendment for this story", None),
    ("amendment_already_reverted", 409, {}, "This amendment is already undone", None),
    ("amendment_not_reverted", 409, {}, "Only an undone amendment can be redone", None),
    ("amendment_already_redone", 409, {}, "This amendment has already been redone", None),
    ("use_redo", 409, {}, "This is an undo — redo the amendment it undid instead", None),
    (
        "directive_unparseable",
        422,
        {},
        "The Engine couldn't read that as a direction — rephrase it",
        "text",
    ),
    ("directive_no_changes", 422, {}, "That direction changes nothing", "text"),
    (
        "directive_split_required",
        422,
        {},
        "That mixes a change to now with a change to the past — send them as two directions",
        "text",
    ),
    ("directive_ambiguous", 422, {}, "That direction is ambiguous — be more specific", "text"),
]


@pytest.mark.parametrize(("code", "status", "details", "text", "field"), FIXED_ROWS)
def test_each_e5_line(
    code: str, status: int, details: dict[str, Any], text: str, field: str | None
) -> None:
    assert refused(code, status, **details) == WorldRefusal(text, "", field)


def test_the_exported_constants() -> None:
    assert REACH_FAILED_TEXT == (
        "The Engine couldn't search the story's lines for this rewrite (every search call "
        "failed) — try again"
    )
    assert SUPERSEDED_TEXT == (
        "The plan was recomputed and this question was replaced — answer the new one below"
    )
    assert BEAT_REQUIRED_TEXT == "That turn has several lines — choose one:"
    assert "{kind}" in NOT_EDITABLE_TEXT
    assert "{membership}" in INACTIVE_TEXT
    assert "{max}" in AUTHOR_FACT_LIMIT_TEXT
    assert set(MERGE_INVALID_TEXT) == {"too_few", "not_in_story", "type_mismatch", "already_same"}
    assert len(AMENDMENT_TEXT) == 5
    assert len(DIRECTIVE_TEXT) == 4


def test_record_inactive_names_the_dormant_variant() -> None:
    swap = refused(
        "record_inactive", membership="dormant", dormant_variant={"turn": 2, "variant": 1}
    )
    assert swap == WorldRefusal(
        "That line isn't on the active timeline any more (Dormant) — it's on turn 2's variant 1; "
        "swap to it first",
        f"/stories/{STORY}/history/#variants",
    )
    plain = refused("record_inactive", membership="dormant")
    assert plain.text == "That line isn't on the active timeline any more (Dormant)"
    assert not plain.url


def test_guardrail_violation_names_policies_and_field() -> None:
    violations = [
        {"name": "No real names", "policy_id": "p1", "locator": "body.instruction"},
        {"name": None, "policy_id": "p2", "locators": ["body.text"]},
        {"name": "No real names", "policy_id": "p1"},
    ]
    text = "That text breaks the story's input guardrails (No real names, p2) — nothing was written"
    assert refused("guardrail_violation", 422, violations=violations) == WorldRefusal(
        text, field="instruction"
    )
    on_text = [{"name": "P", "locators": ["body.text"]}]
    assert refused("guardrail_violation", 422, violations=on_text).field == "text"
    answers = [{"name": "P", "locator": "body.answers[0].text"}]
    assert refused("guardrail_violation", 422, violations=answers).field == "body.answers[0].text"
    assert refused("guardrail_violation", 422, violations=[{"name": "P"}]).field is None
    directive = [{"name": "P", "locator": "body.directive.text"}]
    assert refused("guardrail_violation", 422, violations=directive).field == "directive"
    edit = [{"name": "P", "locator": "body.line_edit.new_text"}]
    assert refused("guardrail_violation", 422, violations=edit).field == "new_text"


def test_invalid_answer_names_its_question() -> None:
    named = refused(
        "invalid_answer", 422, "No such option", question_id="01JM7QN0000000000000000001"
    )
    assert named == WorldRefusal("No such option", "", "choice_01JM7QN0000000000000000001")
    assert refused("invalid_answer", 422, "Bad").field is None


def test_lite_rewrites_reads_like_a_gate() -> None:
    error = EngineError("module_off", "off", {"module": "lite_rewrites"}, 409)
    refusal = rewrite_refusal(error, STORY, VERB, full_v4())
    assert refusal == WorldRefusal(
        "History rewrites need a planner for this story's pipeline (`lite_rewrites` is off)",
        f"/stories/{STORY}/config/",
    )


def test_the_session_limit_links_the_rewrite_page() -> None:
    limit = refused("rewrite_session_limit", limit=5)
    assert limit.text == (
        "This story already has the most open rewrite sessions allowed (5) — finish or cancel "
        "one first"
    )
    assert limit.url == f"/stories/{STORY}/rewrites/"  # #297 registered the Rewrite page
    assert refused("rewrite_session_limit").text.endswith(
        "allowed (the limit) — finish or cancel one first"
    )


def test_omniscient_required_is_a_logged_console_bug(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="console.engine"):
        refusal = refused("omniscient_required", 403)
    assert refusal == WorldRefusal(OMNISCIENT_REQUIRED_TEXT)
    assert [r.getMessage() for r in caplog.records] == [
        f"engine error 403 omniscient_required story {STORY}"
    ]


@pytest.mark.parametrize(
    ("code", "details"),
    [
        ("story_busy", {"reason": "in_flight"}),
        ("module_off", {"module": "states"}),
        ("revision_conflict", {"current_revision": 21}),
    ],
)
def test_other_codes_fall_through_to_world_refusal(code: str, details: dict[str, Any]) -> None:
    error = EngineError(code, "refused", details, 409)
    effective = full_v4()
    expected = world_refusal(error, STORY, VERB, effective)
    assert expected is not None
    assert rewrite_refusal(error, STORY, VERB, effective) == expected


def test_unknown_codes_and_transport_errors_give_none() -> None:
    assert refused("later_code") is None
    assert rewrite_refusal(EngineUnreachable("timeout"), STORY, VERB) is None


# --- URL helpers (E6) ---------------------------------------------------------------------------

AMENDMENT = "01JM7AM0000000000000000001"


@pytest.mark.parametrize("bad", ["", None, 7])
def test_url_helpers_refuse_blank_or_non_string_ids(bad: Any) -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        assert rewrites_url(bad) is None
        assert amendments_url(bad) is None
        assert rewrite_session_url(STORY, bad) is None
        assert rewrite_session_url(bad, SESSION) is None
        assert amendment_url(STORY, bad) is None


def test_the_amendment_helpers_link_under_the_real_urlconf() -> None:
    """#302 registered ``stories:amendments`` and ``stories:amendment`` (they were ``None``
    while unregistered, D19)."""
    assert amendments_url(STORY) == f"/stories/{STORY}/amendments/"
    assert amendment_url(STORY, AMENDMENT) == f"/stories/{STORY}/amendments/{AMENDMENT}/"


def test_the_rewrite_page_helpers_link_under_the_real_urlconf() -> None:
    """#297 registered ``stories:rewrites`` and ``stories:rewrite``."""
    assert rewrites_url(STORY) == f"/stories/{STORY}/rewrites/"
    assert rewrite_session_url(STORY, SESSION) == f"/stories/{STORY}/rewrites/{SESSION}/"


def test_url_helpers_link_under_the_stub_urlconf() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        assert rewrites_url(STORY) == f"/stories/{STORY}/rewrites/"
        assert rewrite_session_url(STORY, SESSION) == f"/stories/{STORY}/rewrites/{SESSION}/"
        assert amendments_url(STORY) == f"/stories/{STORY}/amendments/"
        assert amendment_url(STORY, AMENDMENT) == f"/stories/{STORY}/amendments/{AMENDMENT}/"


@override_settings(ROOT_URLCONF=BARE)
def test_unregistered_names_leave_actions_and_links_out() -> None:
    """**Complement (D19, #341):** with no rewrite or amendment route registered, an open or a
    planned session offers no action, and the URL helpers are ``None`` (no ``NoReverseMatch``)."""
    for name in ("rewrite_open", "rewrite_session"):
        shown = view(name)
        assert shown.actions == ()
        assert shown.footer_actions == ()
        assert shown.answer_url is None
    assert view("rewrite_session_applied").result_amendment_url is None
    assert rewrites_url(STORY) is None
    assert rewrite_session_url(STORY, SESSION) is None
    assert amendments_url(STORY) is None
    assert amendment_url(STORY, AMENDMENT) is None


def test_a_read_only_caller_drops_the_actions() -> None:
    with override_settings(ROOT_URLCONF=M7_PAGES):
        shown = view("rewrite_open")
    assert dataclasses.replace(shown, actions=()).answer_url is None
