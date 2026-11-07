"""Answering a rewrite session's questions (#298; FR-REWRITE-4,5; E3 to E5, E19, E21).

``stories:rewrite-answers`` (``…/rewrites/<ulid:session_id>/answers/``, POST only) is the action
of the session page's questions form (#294's ``AnswersForm`` in E3's ``_questions.html``). It
renders through #297's :func:`console.views.rewrites.render_session_page`, so every re-render
is the session page itself. Answering never changes the story, and the Engine allows it on a
paused story.

**The order of a POST.** After the frame's story and effective-config reads:

1. One ``get_rewrite`` re-read. A 404 is the session page's 404; a transport failure, a 5xx or
   an unreadable answer is #43's panel with the posted key kept; any other refusal is its
   ``rewrite_refusal`` text. None of them sends anything. A session no longer
   ``awaiting_answers`` re-renders as it stands, under :data:`NOT_AWAITING_TEXT`.
2. The stale-page guards, over the qids the POST carries (the ``<qid>`` of every ``choice_`` /
   ``text_`` key; every rendered unanswered question posts its pre-selected radio):

   - a posted qid that is no longer an unanswered question → ``SUPERSEDED_TEXT``;
   - an unanswered required question the POST doesn't carry, or "Accept every recommendation"
     over a different set of questions → :data:`NEW_QUESTION_TEXT`.

   Either one re-renders an unbound form over the fresh questions, with the posted answers
   copied into its ``initial`` for the questions that survive.
3. ``AnswersForm(questions, data=POST)``. An invalid form re-renders bound, with its field
   errors.
4. More than :data:`MAX_ANSWERS` answers → :data:`TOO_MANY_ANSWERS_TEXT`. No answers at all
   (only optional questions, all untouched) → :data:`NOTHING_ANSWERED`.
5. One ``answer_rewrite(story_id, session_id, form.answers(), idempotency_key=…)``
   (``turn_timeout(None)``, no expected revision, E19).

Every re-render in steps 1 to 4 sent nothing, so it keeps the posted key (the key is still
unused). A question in ``unparsed["questions"]`` is display-only (:data:`REJECTED_QUESTION`): it
takes no part in the guards, the cap or the count.

**After the answer** (303 to ``stories:rewrite``): E21's state-driven flash through
:func:`console.rewrites.session_write_flash`, with this page's texts. ``apply`` set →
:data:`APPLIED_TEXT`; ``awaiting_answers`` → :data:`MORE_QUESTIONS_TEXT` with the count of
unanswered questions ("0 more questions" included); ``awaiting_approval`` →
:data:`PLANNED_TEXT`; any other state → "Answers recorded — the session is now <STATE_TEXT>".

**Refusals** re-render (200) from one fresh ``get_rewrite``, with an unbound form whose
``initial`` is copied from the POST and a fresh key. The Engine's errors are carried in the
``answer_errors`` context (``above``, ``groups`` and ``texts``):

- ``invalid_answer``: the message on ``details.question_id``'s group, else above the form;
- ``guardrail_violation``: E5's text on the ``text_<qid>`` box of each flagged answer, through
  ``body.answers[n].text``. Here ``n`` counts only the free-text answers this POST sent, in
  order. A locator that doesn't map puts the text above the form;
- ``erased_subject_referenced``: on the one text box when exactly one text was sent, else
  above;
- ``validation_error`` ``body.answers.<n>…``: on the group of the ``n``-th answer sent; anything
  unmapped is #43's panel;
- ``rewrite_question_superseded``: ``SUPERSEDED_TEXT`` above the form, over the fresh questions.

These codes are notices above the partial: ``rewrite_session_closed``,
``rewrite_apply_in_progress``, any other worded refusal, and, on an ``apply_directly`` session,
an apply refusal (``rewrite_stale``, ``record_in_flight``, ``rewrite_apply_failed``,
``story_paused``) prefixed :data:`APPLY_FAILED_PREFIX` (the answers were recorded). A
``rewrite_session_not_found`` answer is the 404 page. An unknown outcome (transport, timeout,
``bad_response``, an unworded 5xx) re-renders the bound form with #43's panel and the posted
key, so a resubmit replays. A failed fresh read is its 404, or its panel under the refusal's
text.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_POST

from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError
from console.engine.models import RewriteSession
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import engine_field_errors
from console.forms.rewrites import ACCEPT_ALL, APPLY_DIRECTLY, AnswersForm
from console.held import is_held
from console.ids import new_ulid
from console.raw import plural
from console.rewrites import (
    AWAITING_ANSWERS,
    NOT_FOUND_TEXT,
    SUPERSEDED_TEXT,
    QuestionView,
    guardrail_answer_indexes,
    question_view,
    rewrite_refusal,
    rewrite_session_url,
    session_write_flash,
    state_text,
    unanswered_count,
)
from console.views.rewrites import read_session, render_session_page, session_context
from console.views.story_pages import StoryPageContext

NOT_AWAITING_TEXT = (
    "This session isn't waiting for answers any more ({state}) — here it is as it stands now"
)
NOTHING_ANSWERED = "Pick an option or write an answer for at least one question"
PLANNED_TEXT = "Answers recorded — the plan is ready to review"
MORE_QUESTIONS_TEXT = "Answers recorded — the Engine has {n} more question(s)"
APPLIED_TEXT = "Answers recorded and applied: amendment {amendment} · story revision now {revision}"
ANSWERED_VERB = "Answers recorded"
REJECTED_QUESTION = "A question couldn't be read — reload, or answer the others"
NEW_QUESTION_TEXT = "The Engine asked a new question since this page loaded — review it below"
TOO_MANY_ANSWERS_TEXT = "The Engine allows at most 50 answers at once — answer some first"
APPLY_FAILED_PREFIX = "Answers recorded, but applying failed: "
MAX_ANSWERS = 50  # the Engine's cap on one POST's ``answers``
VERB_PHRASE = "answer the session's questions"
BAD_RESPONSE = "bad_response"
NOT_FOUND_CODE = "rewrite_session_not_found"
# An ``apply_directly`` session applies once nothing more needs answering: these refuse that
# apply after the Engine has recorded the answers.
APPLY_CODES = frozenset(
    {"rewrite_stale", "record_in_flight", "rewrite_apply_failed", "story_paused"}
)
POSTED_ANSWER = re.compile(r"^(?:choice|text)_(.+)$")
ANSWER_PATH = re.compile(r"^(?:body\.)?answers(?:\[(\d+)\]|\.(\d+))(?:\.|$)")


def _effective(ctx: StoryPageContext) -> Mapping[str, Any] | None:
    return ctx.config.effective if ctx.config is not None else None


def posted_question_ids(data: Mapping[str, Any]) -> set[str]:
    """The ``<qid>`` of every ``choice_<qid>`` / ``text_<qid>`` key the POST carries."""
    found = set()
    for name in data:
        match = POSTED_ANSWER.match(name)
        if match:
            found.add(match.group(1))
    return found


def _initial(data: Mapping[str, Any], kept: set[str]) -> dict[str, Any]:
    """The posted choices and texts of the questions in ``kept``, as a form's ``initial``."""
    initial = {}
    for name in data:
        match = POSTED_ANSWER.match(name)
        if match and match.group(1) in kept:
            initial[name] = data[name]
    return initial


def _questions(session: RewriteSession) -> tuple[QuestionView, ...]:
    return tuple(question_view(q) for q in session.questions)


def _unanswered_ids(questions: Sequence[QuestionView]) -> set[str]:
    return {q.id for q in questions if q.answered is None}


class AnswerErrors:
    """The Engine's answer errors as ``_questions.html`` renders them: ``above`` the form, per
    question ``groups`` (``{qid: [message]}``) and per ``text_<qid>`` box (``texts``). Each
    message is added once per place."""

    def __init__(self, fresh_ids: set[str]) -> None:
        self.fresh_ids = fresh_ids
        self.above: list[str] = []
        self.groups: dict[str, list[str]] = {}
        self.texts: dict[str, list[str]] = {}

    def _add(self, messages_at: list[str], message: str) -> None:
        if message not in messages_at:
            messages_at.append(message)

    def add(self, message: str, *, group: object = None, text: object = None) -> None:
        """``message`` on ``group``'s or ``text``'s question when it is a fresh unanswered one,
        else above the form."""
        if isinstance(text, str) and text in self.fresh_ids:
            self._add(self.texts.setdefault(text, []), message)
        elif isinstance(group, str) and group in self.fresh_ids:
            self._add(self.groups.setdefault(group, []), message)
        else:
            self._add(self.above, message)

    def as_context(self) -> dict[str, Any]:
        return {"above": self.above, "groups": self.groups, "texts": self.texts}


def _guarded(
    ctx: StoryPageContext, session: RewriteSession, key: str
) -> Mapping[str, Any] | AnswersForm:
    """Steps 2 to 4: a re-render's context, or the valid form to send."""
    data = ctx.request.POST
    questions = _questions(session)
    fresh_ids = _unanswered_ids(questions)
    posted = posted_question_ids(data)
    stale = ""
    if posted - fresh_ids:
        stale = SUPERSEDED_TEXT
    elif any(q.required and q.id not in posted for q in questions if q.id in fresh_ids) or (
        data.get(ACCEPT_ALL) and posted != fresh_ids
    ):
        stale = NEW_QUESTION_TEXT
    if stale:
        errors = AnswerErrors(fresh_ids)
        errors.add(stale)
        form = AnswersForm(questions, initial=_initial(data, fresh_ids))
        return session_context(
            ctx, session, answers_form=form, key=key, answer_errors=errors.as_context()
        )
    form = AnswersForm(questions, data=data)
    if form.is_valid():
        sent = form.answers()
        if len(sent) > MAX_ANSWERS:
            form.add_error(None, TOO_MANY_ANSWERS_TEXT)
        elif not sent:
            form.add_error(None, NOTHING_ANSWERED)
        else:
            return form
    return session_context(ctx, session, answers_form=form, key=key)


def _place(
    exc: EngineError, text: str, sent: Sequence[Mapping[str, Any]], errors: AnswerErrors
) -> str | None:
    """Put a refusal on the form (``errors``); a notice for above the partial, or ``None``."""
    code = exc.code
    details: Mapping[str, Any] = exc.details if isinstance(exc.details, Mapping) else {}
    text_ids = [a["question_id"] for a in sent if "text" in a]
    if code == "invalid_answer":
        errors.add(text, group=details.get("question_id"))
    elif code == "guardrail_violation":
        indexes = guardrail_answer_indexes(details) or [None]
        for n in indexes:
            errors.add(text, text=text_ids[n] if n is not None and n < len(text_ids) else None)
    elif code == "erased_subject_referenced":
        errors.add(text, text=text_ids[0] if len(text_ids) == 1 else None)
    elif code == "rewrite_question_superseded":
        errors.add(text)
    else:
        return text
    return None


def _validation(exc: EngineError, sent: Sequence[Mapping[str, Any]], errors: AnswerErrors) -> bool:
    """A ``validation_error``'s ``body.answers.<n>`` paths on the groups of the answers sent;
    ``False`` when any failure doesn't map (#43's panel shows the error)."""
    failures = engine_field_errors(exc)
    mapped = []
    for failure in failures:
        match = ANSWER_PATH.match(failure.path)
        index = int(match.group(1) or match.group(2)) if match else None
        if (
            index is None
            or index >= len(sent)
            or sent[index]["question_id"] not in errors.fresh_ids
        ):
            return False
        mapped.append((sent[index]["question_id"], failure.message or exc.message))
    for question_id, message in mapped:
        errors.add(message, group=question_id)
    return bool(mapped)


def _refused(
    ctx: StoryPageContext,
    session_id: str,
    session: RewriteSession,
    form: AnswersForm,
    sent: Sequence[Mapping[str, Any]],
    exc: EngineClientError,
    key: str,
) -> Mapping[str, Any]:
    """The refused answer's re-render: from one fresh read, with the refusal placed."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    apply_failed = (
        isinstance(exc, EngineError)
        and session.preview_mode == APPLY_DIRECTLY
        and exc.code in APPLY_CODES
    )
    refusal = rewrite_refusal(exc, ctx.story_id, VERB_PHRASE, _effective(ctx))
    if (
        not isinstance(exc, EngineError)
        or exc.code == BAD_RESPONSE
        or (exc.http_status >= 500 and refusal is None and not apply_failed)
    ):
        context = session_context(ctx, session, answers_form=form, key=key)
        return {**context, "write_error": panel_for(exc)}  # the outcome is unknown
    if exc.code == NOT_FOUND_CODE:
        return {"session_not_found": NOT_FOUND_TEXT, "idempotency_key": new_ulid()}
    text = refusal.text if refusal is not None else ""
    read = read_session(ctx, session_id)
    if isinstance(read, tuple):
        failed = read[1]
        shown = (text,) if text and not failed.get("session_not_found") else ()
        return {**failed, "session_notices": shown, "idempotency_key": new_ulid()}
    fresh = read
    questions = _questions(fresh)
    errors = AnswerErrors(_unanswered_ids(questions))
    notices: list[str] = []
    write_error = None
    if apply_failed:
        notices.append(f"{APPLY_FAILED_PREFIX}{text}")
    elif exc.code == "validation_error" and refusal is None:
        if not _validation(exc, sent, errors):
            write_error = panel_for(exc)
    elif refusal is None:
        write_error = panel_for(exc)
    else:
        notice = _place(exc, text, sent, errors)
        if notice is not None:
            notices.append(notice)
    fresh_form = None
    if fresh.state == AWAITING_ANSWERS:
        fresh_form = AnswersForm(
            questions, initial=_initial(ctx.request.POST, _unanswered_ids(questions))
        )
    context = session_context(
        ctx,
        fresh,
        answers_form=fresh_form,
        key=new_ulid(),
        notices=notices,
        answer_errors=errors.as_context(),
    )
    return {**context, "write_error": write_error}


def _answered(ctx: StoryPageContext, written: Any, key: str) -> HttpResponse:
    count = unanswered_count(written)
    questions = MORE_QUESTIONS_TEXT.replace("{n} more question(s)", plural(count, "more question"))
    flash = session_write_flash(
        written,
        ctx.story_id,
        key,
        apply_directly=False,
        verb=ANSWERED_VERB,
        planned=PLANNED_TEXT,
        questions=questions,
        applied=APPLIED_TEXT,
    )
    messages.success(ctx.request, flash, extra_tags="safe")
    target = rewrite_session_url(ctx.story_id, written.id) or ctx.request.path
    response = HttpResponseRedirect(target)
    response.status_code = 303
    return response


@require_POST
def answers(request: HttpRequest, story_id: str, session_id: str) -> HttpResponse:
    """``stories:rewrite-answers``: answer the session's questions (#298)."""

    def build(ctx: StoryPageContext) -> Mapping[str, Any] | HttpResponse:
        key = posted_idempotency_key(request)
        read = read_session(ctx, session_id)
        if isinstance(read, tuple):
            exc, failed = read
            if failed.get("session_not_found"):
                return failed
            refusal = None
            if isinstance(exc, EngineError) and exc.code != BAD_RESPONSE and exc.http_status < 500:
                refusal = rewrite_refusal(exc, story_id, VERB_PHRASE, _effective(ctx))
            if refusal is None:
                return {**failed, "idempotency_key": key}
            return {"session_notices": (refusal.text,), "idempotency_key": key}
        session = read
        if session.state != AWAITING_ANSWERS:
            notice = NOT_AWAITING_TEXT.format(state=state_text(session.state))
            return session_context(ctx, session, key=key, notices=(notice,))
        guarded = _guarded(ctx, session, key)
        if not isinstance(guarded, AnswersForm):
            return guarded
        sent = guarded.answers()
        try:
            written = ctx.client.answer_rewrite(story_id, session_id, sent, idempotency_key=key)
        except EngineClientError as exc:
            return _refused(ctx, session_id, session, guarded, sent, exc, key)
        return _answered(ctx, written, key)

    return render_session_page(request, story_id, session_id, build)
