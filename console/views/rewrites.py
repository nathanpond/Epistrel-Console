"""The story's Rewrite page and a rewrite session's page (#297; FR-REWRITE-1,2,7; E2 to E5,
E11, E19, E21 to E23).

``stories:rewrites`` (``/stories/<id>/rewrites/``, GET/HEAD/POST) and ``stories:rewrite``
(``…/rewrites/<ulid:session_id>/``, GET/HEAD) render through #296's
:func:`console.views.story_pages.story_page`, Rewrite marked current on both. Every rewrite
route is omniscient-only (E2): neither page offers a viewpoint picker, each shows
``OMNISCIENT_LINE``, and the client always sends ``viewpoint=omniscient`` (a ``?viewpoint=`` on
the page URL is ignored).

**The Rewrite page.** Reads per render: the frame's story and effective-config reads, then one
``list_rewrites(story_id, states=…, cursor=…, limit=REWRITES_PAGE)``. The state filter
(``RewriteFilterForm``, a GET form, E22) sends each ticked state once in ``STATE_TEXT``'s order;
none ticked sends no ``state``; an out-of-choice value is a field error and no list read. A
cursor that fails ``is_ulid`` is dropped with ``CURSOR_DROPPED`` (first page read). Rows are
:func:`console.rewrites.rewrite_rows`; a rejected entry is D15's notice plus one spanning row;
a failed list read is #43's panel in place of the table, the New rewrite form still rendered.
Paging: "Older →" with the Engine's ``next_cursor``, "Newest page" on any cursor page, both
rebuilt from the cleaned filter.

**Opening a session** (POST to the same URL, PRG). The New rewrite form (``NewRewriteForm``) is
first on the page. An invalid form makes no write and re-renders (200) with the posted key and
the list read again. A valid one calls ``open_rewrite`` once (``turn_timeout(None)``, E19) inside
``build``, after the frame's reads (it writes even when the story read failed: the Engine's
refusal is authoritative), and answers 303 to the session page with E21's flash
(:func:`console.rewrites.session_write_flash`). A refusal re-renders the page (200) with the
instruction kept: :func:`console.rewrites.rewrite_refusal` first (instruction codes and the
guardrail on Instruction; the session limit, ``module_off`` and the rest above the form with
their links), then ``validation_error``'s paths through ``apply_engine_errors``, else #43's
panel. D8's key rule: a fresh key after an answered refusal, the posted key after an unknown
outcome (transport failure, timeout, ``bad_response``, an unworded 5xx). A held story (503
``erasure_pending``) goes to #87's middleware.

**The session page.** Reads: the frame's two, then one ``get_rewrite``. It renders E3's
partial over ``session_view(session, story_id, cast_names(story.cast))`` (raw ids when the story
read failed), the heading "<kind> session" and the id, "← All rewrites" and "Reload". 404
``rewrite_session_not_found`` is E5's text in the frame with status 404; any other failure is
#43's panel under "Rewrite session <id>". The page makes no write and doesn't poll: the footer
shows only the actions whose URL names resolve (D19). #298 factored the page into
:func:`render_session_page` (the frame, the links and the 404), :func:`read_session` (the one
``get_rewrite`` and its failure context) and :func:`session_context` (E3's view, the answers
form while ``awaiting_answers``, the key, and the answers POST's notices and errors), which
``console/views/rewrite_answers.py`` re-renders through. #299's apply and cancel
(``console/views/rewrite_apply.py``) re-render through the same three; ``session_context``
adds the Apply form's ``apply_hidden`` (the *preview's* ``base_revision``) and the stale
notice.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.canon import cast_names
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError
from console.engine.models import RewriteList, RewriteSession
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.rewrites import AnswersForm, NewRewriteForm, RewriteFilterForm
from console.held import is_held
from console.ids import is_ulid, new_ulid
from console.impact import short_fingerprint
from console.paging import CURSOR_DROPPED
from console.portability import is_importing
from console.raw import plural
from console.rewrites import (
    APPLY,
    AWAITING_ANSWERS,
    AWAITING_APPROVAL,
    NO_MORE_SESSIONS,
    NO_SESSIONS,
    NO_SESSIONS_MATCH,
    NOT_FOUND_TEXT,
    SESSION_REJECTED_TEXT,
    StaleNotice,
    rejected_notice,
    rejected_sessions,
    rewrite_refusal,
    rewrite_rows,
    rewrite_session_url,
    rewrites_url,
    session_view,
    session_write_flash,
)
from console.views.story_pages import StoryBuild, StoryPageContext, story_page

INDEX_TEMPLATE = "console/rewrites/index.html"
SESSION_TEMPLATE = "console/rewrites/session.html"
CURRENT = "stories:rewrites"
REWRITES_PAGE = 20  # under the Engine's cap of 50, like #52's history pages
VERB_PHRASE = "open a rewrite"
CURSOR = "cursor"
STATE = "state"
BAD_RESPONSE = "bad_response"
NOT_FOUND_CODE = "rewrite_session_not_found"
COUNT_SUFFIX = " on this page"
FILTERED_SUFFIX = ", matching filters"
OLDER = "Older →"
NEWEST = "Newest page"
# E21: an Engine ``body.<member>`` path → the form field that carries it.
DIRECTED_RESOLUTION = "directed_resolution"  # #301's session kind
ALIASES: Mapping[str, str] = {"preview": "apply_directly", "resolution_mode": "resolution"}
# The label of a refusal's link, by code (E5's session limit, convention 24's Config).
REFUSAL_LINKS: Mapping[str, str] = {"rewrite_session_limit": "Rewrite", "module_off": "Config"}
DEFAULT_LINK = "See the check"


def _passed_on(exc: EngineClientError) -> bool:
    """A held (#87) or importing (E10) answer: its middleware renders it."""
    return is_held(exc) or is_importing(exc)


def _effective(ctx: StoryPageContext) -> Mapping[str, Any] | None:
    return ctx.config.effective if ctx.config is not None else None


def _query(states: tuple[str, ...], cursor: str | None = None) -> str:
    params = QueryDict(mutable=True)
    params.setlist(STATE, list(states))
    if cursor:
        params[CURSOR] = cursor
    return params.urlencode()


def _with_query(path: str, query: str) -> str:
    return f"{path}?{query}" if query else path


# --- the session list ----------------------------------------------------------------------------


def _session_list(ctx: StoryPageContext) -> dict[str, Any]:
    """The filter, the cursor check, the one list read and what the table shows."""
    request = ctx.request
    path = request.path
    filter_form = RewriteFilterForm(request.GET)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    context: dict[str, Any] = {
        "filter_form": filter_form,
        "filter_url": path,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "list_read": False,
        "form_action": path,
    }
    if not filter_form.is_valid():
        return context
    states = filter_form.states()
    context.update(
        filtered=bool(states),
        clear_url=path if states else None,
        form_action=_with_query(path, _query(states, cursor)),
    )
    try:
        page = ctx.client.list_rewrites(
            ctx.story_id, states=states, cursor=cursor, limit=REWRITES_PAGE
        )
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        context["list_error"] = panel_for(exc)
        return context
    context.update(_table(ctx, page, states, cursor))
    return context


def _table(
    ctx: StoryPageContext, page: RewriteList, states: tuple[str, ...], cursor: str | None
) -> dict[str, Any]:
    path = ctx.request.path
    rows = rewrite_rows(page, ctx.story_id)
    rejected = rejected_sessions(page)
    count = ""
    if rows:
        count = plural(len(rows), "session") + COUNT_SUFFIX + (FILTERED_SUFFIX if states else "")
    empty = ""
    if not rows and not rejected:
        empty = NO_MORE_SESSIONS if cursor else (NO_SESSIONS_MATCH if states else NO_SESSIONS)
    return {
        "list_read": True,
        "rows": rows,
        "rejected": rejected,
        "rejected_notice": rejected_notice(len(rejected)) if rejected else "",
        "rejected_text": SESSION_REJECTED_TEXT,
        "count_text": count,
        "empty_text": empty,
        "older_url": (
            _with_query(path, _query(states, page.next_cursor)) if page.next_cursor else None
        ),
        "newest_url": _with_query(path, _query(states)) if cursor else None,
        "older_text": OLDER,
        "newest_text": NEWEST,
    }


# --- opening a session ---------------------------------------------------------------------------


def _refused(
    ctx: StoryPageContext, form: NewRewriteForm, exc: EngineClientError, key: str
) -> dict[str, Any]:
    """The refused open's context additions (the form carries field errors itself)."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return {"write_error": panel_for(exc), "idempotency_key": key}  # the outcome is unknown
    refusal = rewrite_refusal(exc, ctx.story_id, VERB_PHRASE, _effective(ctx))
    if refusal is not None:
        if refusal.field is not None and refusal.field in form.fields and not refusal.url:
            form.add_error(refusal.field, refusal.text)
            return {"idempotency_key": new_ulid()}
        return {
            "refusal": refusal.text,
            "refusal_url": refusal.url,
            "refusal_link": REFUSAL_LINKS.get(exc.code, DEFAULT_LINK) if refusal.url else "",
            "idempotency_key": new_ulid(),
        }
    if exc.code == "validation_error" and apply_engine_errors(exc, form, aliases=ALIASES):
        return {"idempotency_key": new_ulid()}
    if exc.http_status >= 500:
        return {"write_error": panel_for(exc), "idempotency_key": key}
    return {"write_error": panel_for(exc), "idempotency_key": new_ulid()}


def _open(ctx: StoryPageContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    form = NewRewriteForm(request.POST)
    key = posted_idempotency_key(request)
    if not form.is_valid():
        return {"form": form, "idempotency_key": key, **_session_list(ctx)}
    try:
        written = ctx.client.open_rewrite(ctx.story_id, form.body(), idempotency_key=key)
    except EngineClientError as exc:
        refused = _refused(ctx, form, exc, key)
        return {"form": form, **refused, **_session_list(ctx)}
    flash = session_write_flash(
        written,
        ctx.story_id,
        key,
        apply_directly=bool(form.cleaned_data.get("apply_directly")),
    )
    messages.success(request, flash, extra_tags="safe")
    target = rewrite_session_url(ctx.story_id, written.id) or rewrites_url(ctx.story_id)
    response = HttpResponseRedirect(target or request.path)
    response.status_code = 303
    return response


def _rewrites(ctx: StoryPageContext) -> dict[str, Any] | HttpResponse:
    if ctx.request.method == "POST":
        return _open(ctx)
    return {"form": NewRewriteForm(), "idempotency_key": new_ulid(), **_session_list(ctx)}


@require_http_methods(["GET", "HEAD", "POST"])
def rewrites(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:rewrites``: the story's rewrite sessions and the New rewrite form (#297)."""
    return story_page(request, story_id, INDEX_TEMPLATE, _rewrites, current=CURRENT)


# --- the session page ----------------------------------------------------------------------------


def session_context(
    ctx: StoryPageContext,
    session: RewriteSession,
    *,
    answers_form: AnswersForm | None = None,
    key: str | None = None,
    notices: Sequence[str] = (),
    answer_errors: Mapping[str, Any] | None = None,
    stale: StaleNotice | None = None,
    display_only: bool = False,
    preview_source_note: str = "",
) -> dict[str, Any]:
    """The session page's context for ``session``: E3's view, the answers form (an unbound
    ``AnswersForm`` while ``awaiting_answers`` unless the caller passes its own; none in any
    other state), the key (fresh unless given), #298's notices and answer errors, and #299's
    Apply form (``apply_hidden``: the preview's ``base_revision`` and plan hash, while
    ``awaiting_approval`` with a preview and Apply resolves), stale notice and preview note. A
    ``display_only`` session (#299's synthetic one, read from a refusal) gets no answers form
    and no Apply form."""
    from console.views.rewrite_answers import REJECTED_QUESTION  # it imports this module
    from console.views.rewrite_apply import APPLY_LABEL, APPLY_LINE  # likewise

    names = cast_names(ctx.story.cast if ctx.story is not None else None)
    view = session_view(session, ctx.story_id, names)
    awaiting = view.state == AWAITING_ANSWERS and not display_only
    if awaiting and answers_form is None:
        answers_form = AnswersForm(view.questions)
    preview = session.preview
    apply_hidden = None
    if (
        not display_only
        and preview is not None
        and view.state == AWAITING_APPROVAL
        and any(action.label == APPLY for action in view.actions)
    ):
        apply_hidden = {
            "base_revision": preview.base_revision,
            "plan_hash": preview.plan_hash,
            "plan_short": short_fingerprint(preview.plan_hash),
        }
    return {
        "session_view": view,
        "answers_form": answers_form if awaiting else None,
        "idempotency_key": key or new_ulid(),
        "apply_hidden": apply_hidden,
        "apply_line": APPLY_LINE if apply_hidden else "",
        "apply_label": APPLY_LABEL,
        "session_notices": tuple(notices),
        "answer_errors": answer_errors,
        "rejected_question_text": REJECTED_QUESTION if awaiting else "",
        "stale_notice": stale,
        "preview_source_note": preview_source_note,
        # #301: a directed regenerate's session links back to the story it was regenerating.
        "back_to_story_url": (
            reverse("stories:detail", args=[ctx.story_id])
            if session.kind == DIRECTED_RESOLUTION
            else None
        ),
    }


def read_session(
    ctx: StoryPageContext, session_id: str
) -> RewriteSession | tuple[EngineClientError, dict[str, Any]]:
    """One ``get_rewrite``: the session, or the failure and the context showing it (404
    ``rewrite_session_not_found`` as E5's text, anything else #43's panel). A held or importing
    answer is raised for its middleware."""
    try:
        return ctx.client.get_rewrite(ctx.story_id, session_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.code == NOT_FOUND_CODE:
            return exc, {"session_not_found": NOT_FOUND_TEXT}
        return exc, {"session_error": panel_for(exc)}


def render_session_page(
    request: HttpRequest, story_id: str, session_id: str, build: StoryBuild
) -> HttpResponse:
    """The session page through #296's frame, Rewrite current: ``build``'s context over the
    page's links; a context carrying ``session_not_found`` answers 404 (#297). Shared by the
    session page and #298's answers POST."""
    not_found: list[bool] = []

    def framed(ctx: StoryPageContext) -> Mapping[str, Any] | HttpResponse:
        built = build(ctx)
        if isinstance(built, HttpResponse):
            return built
        if built.get("session_not_found"):
            not_found.append(True)
        return {
            "session_id": session_id,
            "rewrites_url": rewrites_url(story_id),
            "session_reload_url": rewrite_session_url(story_id, session_id) or request.path,
            **built,
        }

    response = story_page(request, story_id, SESSION_TEMPLATE, framed, current=CURRENT)
    if not_found:
        response.status_code = 404
    return response


@require_safe
def rewrite(request: HttpRequest, story_id: str, session_id: str) -> HttpResponse:
    """``stories:rewrite``: one rewrite session through E3's partial (#297). No write."""

    def build(ctx: StoryPageContext) -> dict[str, Any]:
        read = read_session(ctx, session_id)
        if isinstance(read, tuple):
            return read[1]
        return session_context(ctx, read)

    return render_session_page(request, story_id, session_id, build)
