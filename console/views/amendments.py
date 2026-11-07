"""A story's Amendments page and an amendment's page (#302; FR-REWRITE-8,9,10,11; E2, E5, E6,
E11, E15, E22, E23).

``stories:amendments`` (``/stories/<id>/amendments/``) and ``stories:amendment``
(``…/amendments/<ulid:amendment_id>/``; a malformed id is Django's 404 with no Engine call) are
``@require_safe`` reads through #296's :func:`console.views.story_pages.story_page`, Amendments
marked current on both. The amendment routes are omniscient-only (E2): neither page offers a
viewpoint picker, each shows ``OMNISCIENT_LINE``, and the client always sends
``viewpoint=omniscient``. The reads never dispatch, so they serve every pipeline (no module
gate). Nothing here writes.

**The list.** Reads per render: the frame's two, then one ``list_amendments(story_id,
status=…, cursor=…, limit=AMENDMENTS_PAGE)``. ``AmendmentFilterForm`` (E22) sends ``status``
only for Active/Undone; an out-of-choice value is ``STATUS_INVALID`` and no list read. A cursor
failing ``is_ulid`` is dropped with ``CURSOR_DROPPED`` and never sent. Paging is convention 6's
:func:`console.paging.paging` over the cleaned filter. The table's Actions column renders only
when the context's ``row_actions`` (a template name; convention 14) is set: #303 sets it.

**The detail.** Reads per render: the frame's two, then one ``get_amendment``, then convention
2's one registry read (``predicate_names``) only when a part is a proposition naming a
predicate. ``amendment.html``'s ``{% block amendment_actions %}`` (convention 14) sits under the
summary, with the parsed ``amendment`` in the context, for #303.

**Errors stay in the page** (convention 10). 404 ``amendment_not_found`` is E5's sentence in
place of the summary (200, the frame kept). 403 ``omniscient_required`` / 422
``viewpoint_unresolved`` are E2's ``OMNISCIENT_REQUIRED_TEXT``, logged (a Console bug). A 422
``validation_error`` naming ``query.cursor`` is the Engine's message with "First page"; any other
422 the Engine's message. 409 ``story_importing`` and 503 ``erasure_pending`` are re-raised to
their middlewares. Anything else is #43's panel (below the filter on the list, in place of the
summary on the detail).

**Undo and redo (#303; FR-REWRITE-9,10; E2, E4, E5, E19, E21, D8).** The list sets
``row_actions`` to ``_amendment_row_actions.html`` and each row carries ``action``
(:func:`console.amendments.amendment_action`); the detail fills ``amendment_actions`` with the
same link, or the redo hint for an active ``undo``. ``stories:amendment-undo`` /
``stories:amendment-redo`` (``…/<ulid:amendment_id>/undo/`` and ``…/redo/``, GET/HEAD/POST) are
one shared handler, :func:`_open_session`, over the :data:`~console.amendments.UNDO` /
:data:`~console.amendments.REDO` specs, through the frame with Amendments current.

- **GET** reads the frame's two, then one ``get_amendment``: #302's summary, E2's line, the
  explanation and E4's ``RewriteModesForm`` with a fresh key. An amendment the form can't open
  (the spec's ``ineligible``) shows E5's sentence and the matching action instead of the form,
  with no submit; 404 is E5's not-found sentence; any other failure is #43's panel (E2's text
  for a viewpoint refusal). A GET never writes.
- **POST** makes the frame's two reads and **no** ``get_amendment`` (the Engine decides
  eligibility), validates the form (invalid: re-rendered with the posted key, no write), then
  makes one ``undo_amendment`` / ``redo_amendment`` with ``form.modes()`` and the posted key, no
  expected revision (E19). A 201 is a 303 to E6's ``rewrite_session_url`` (else ``rewrites_url``,
  else the amendment) with ``OPENED``; a 2xx that doesn't parse warns ``OPENED_UNREADABLE`` and
  redirects to ``rewrites_url`` (else the amendment). A refusal re-renders the form, modes kept,
  under "Amendment <short id>", with ``rewrite_refusal``'s sentence (D8's key rule: fresh after
  an answered refusal, posted after an unknown outcome).
"""

from collections.abc import Mapping
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_safe

from console.amendments import (
    AMENDMENT_REJECTED_TEXT,
    AMENDMENTS_PAGE,
    BACK_TEXT,
    BACK_TO_AMENDMENT,
    CANCEL_TEXT,
    DEPENDENTS_TRUNCATED,
    DETAIL_VERB,
    NO_DEPENDENTS,
    NO_PARTS,
    NO_RELIES,
    OPENED,
    OPENED_UNREADABLE,
    REDO,
    TIMING_UNCERTAIN_TEXT,
    UNDO,
    UNPARSED_TEXT,
    USE_REDO_HINT,
    SessionSpec,
    amendment_action,
    amendment_row,
    amendment_rows,
    dependent_rows,
    edge_rows,
    empty_text,
    needs_predicate_names,
    part_rows,
    raw_list,
    rejected_amendments,
)
from console.cast import character_labels
from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError
from console.engine.models import AmendmentDetail, RewriteSessionWritten
from console.engine_errors import log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.amendments import AmendmentFilterForm
from console.forms.rewrites import RewriteModesForm
from console.held import is_held
from console.ids import is_ulid, new_ulid
from console.inspect import short_id
from console.paging import CURSOR_DROPPED, paging
from console.portability import is_importing
from console.predicates import predicate_names as read_predicate_names
from console.raw import plural
from console.rewrites import (
    KEY_TAIL,
    NOT_APPLIED_PREFIX,
    OMNISCIENT_REQUIRED_TEXT,
    amendment_url,
    amendments_url,
    rewrite_refusal,
    rewrite_session_url,
    rewrites_url,
)
from console.views.rewrites import ALIASES, DEFAULT_LINK, REFUSAL_LINKS
from console.views.story_pages import StoryPageContext, story_page

LIST_TEMPLATE = "console/stories/amendments.html"
DETAIL_TEMPLATE = "console/stories/amendment.html"
SESSION_TEMPLATE = "console/stories/amendment_session.html"
ROW_ACTIONS = "console/stories/_amendment_row_actions.html"
BAD_RESPONSE = "bad_response"
CURRENT = "stories:amendments"
CURSOR = "cursor"
STATUS = "status"
COUNT_SUFFIX = " on this page"
FILTERED_SUFFIX = ", matching filters"
NEXT_TEXT = "Next page"
FIRST_TEXT = "First page"
NOT_FOUND_CODE = "amendment_not_found"
OMNISCIENT_CODES = frozenset({"omniscient_required", "viewpoint_unresolved"})
VALIDATION = "validation_error"


def _passed_on(exc: EngineClientError) -> bool:
    """A held (#87) or importing (E10) answer: its middleware renders it."""
    return is_held(exc) or is_importing(exc)


def _names_cursor(exc: EngineError) -> bool:
    """A ``validation_error`` whose ``details.errors[*].loc`` names ``query.cursor``."""
    errors = exc.details.get("errors") if isinstance(exc.details, Mapping) else None
    if not isinstance(errors, list):
        return False
    return any(
        isinstance(error, Mapping)
        and isinstance(error.get("loc"), list)
        and [str(part) for part in error["loc"][:2]] == ["query", CURSOR]
        for error in errors
    )


def _refusal(exc: EngineClientError) -> dict[str, Any] | None:
    """The in-page wording shared by both pages: E2's text for a viewpoint refusal, the
    Engine's message for a 422 (``cursor_refused`` when it names ``query.cursor``); ``None``
    sends the caller to #43's panel."""
    if not isinstance(exc, EngineError):
        return None
    if exc.code in OMNISCIENT_CODES:
        return {"refusal": OMNISCIENT_REQUIRED_TEXT}
    if exc.http_status == 422:
        cursor = exc.code == VALIDATION and _names_cursor(exc)
        return {"refusal": exc.message, "cursor_refused": cursor}
    return None


# --- the list ------------------------------------------------------------------------------------


def _amendments(ctx: StoryPageContext) -> dict[str, Any]:
    request = ctx.request
    path = request.path
    filter_form = AmendmentFilterForm(request.GET)
    typed_cursor = request.GET.get(CURSOR, "")
    cursor = typed_cursor if is_ulid(typed_cursor) else None
    context: dict[str, Any] = {
        "filter_form": filter_form,
        "filter_url": path,
        "cursor_dropped": CURSOR_DROPPED if typed_cursor and cursor is None else None,
        "list_read": False,
        "row_actions": ROW_ACTIONS,  # convention 14: #303's row template
        "first_url": None,
        "next_url": None,
        "first_text": FIRST_TEXT,
        "next_text": NEXT_TEXT,
    }
    if not filter_form.is_valid():
        return context
    status = filter_form.chosen()
    params = QueryDict(mutable=True)
    if status:
        params[STATUS] = status
    context["clear_url"] = path if status else None
    try:
        page = ctx.client.list_amendments(
            ctx.story_id, status=status, cursor=cursor, limit=AMENDMENTS_PAGE
        )
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        refused = _refusal(exc)
        if refused is None:
            context["list_error"] = panel_for(exc)
            return context
        context.update(refused)
        if refused.get("cursor_refused"):
            context["first_url"] = _with_query(path, params.urlencode())
        return context
    rows = amendment_rows(page, ctx.story_id)
    rejected = rejected_amendments(page)
    links = paging(params, cursor, page.next_cursor, param=CURSOR)
    count = ""
    if rows:
        count = plural(len(rows), "amendment") + COUNT_SUFFIX + (FILTERED_SUFFIX if status else "")
    context.update(
        list_read=True,
        rows=rows,
        rejected=rejected,
        unparsed_text=UNPARSED_TEXT if rejected else "",
        rejected_text=AMENDMENT_REJECTED_TEXT,
        count_text=count,
        empty_text=(empty_text(status=status, cursor=cursor) if not rows and not rejected else ""),
        next_url=_with_query(path, links["next_query"]) if links["next_query"] else None,
        first_url=_with_query(path, links["first_query"]) if links["paged"] else None,
    )
    return context


def _with_query(path: str, query: str) -> str:
    return f"{path}?{query}" if query else path


@require_safe
def amendments(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:amendments``: the story's amendments, newest first (#302). No write."""
    return story_page(request, story_id, LIST_TEMPLATE, _amendments, current=CURRENT)


# --- the detail ----------------------------------------------------------------------------------


def _detail(ctx: StoryPageContext, amendment_id: str) -> dict[str, Any]:
    request = ctx.request
    context: dict[str, Any] = {
        "amendment_id": amendment_id,
        "amendments_url": amendments_url(ctx.story_id) or "",
        "back_text": BACK_TEXT,
        "amendment": None,
    }
    try:
        detail = ctx.client.get_amendment(ctx.story_id, amendment_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code == NOT_FOUND_CODE:
            refusal = rewrite_refusal(exc, ctx.story_id, DETAIL_VERB)
            context["not_found"] = refusal.text if refusal is not None else exc.message
            return context
        refused = _refusal(exc)
        if refused is None:
            context["detail_error"] = panel_for(exc)
        else:
            context["refusal"] = refused["refusal"]
        return context
    context.update(_detail_view(ctx, detail))
    return context


def _detail_view(ctx: StoryPageContext, detail: AmendmentDetail) -> dict[str, Any]:
    parts = raw_list(detail.parts)
    predicate_names: Mapping[str, str] = {}
    if needs_predicate_names(parts):
        predicate_names = read_predicate_names(ctx.request, ctx.client, ctx.story_id)
    names = character_labels(ctx.story.cast) if ctx.story is not None else {}
    relies = raw_list(detail.relies_on)
    dependents = raw_list(detail.dependents)
    return {
        "amendment": detail,
        "action": amendment_action(detail, ctx.story_id),
        "use_redo_hint": USE_REDO_HINT,
        "summary": amendment_row(detail, ctx.story_id),
        "parts": part_rows(parts, names, predicate_names, ctx.story_id),
        "relies": edge_rows(relies, parts),
        "dependents": dependent_rows(dependents),
        "truncated_text": DEPENDENTS_TRUNCATED if detail.truncated is True else "",
        "no_parts": NO_PARTS,
        "no_relies": NO_RELIES,
        "no_dependents": NO_DEPENDENTS,
        "timing_uncertain_text": TIMING_UNCERTAIN_TEXT,
    }


@require_safe
def amendment(request: HttpRequest, story_id: str, amendment_id: str) -> HttpResponse:
    """``stories:amendment``: one amendment's parts and dependencies (#302). No write."""
    return story_page(
        request,
        story_id,
        DETAIL_TEMPLATE,
        lambda ctx: _detail(ctx, amendment_id),
        current=CURRENT,
        label=f"Amendment {short_id(amendment_id)}",
    )


# --- undo and redo (#303) ------------------------------------------------------------------------


def _session_base(ctx: StoryPageContext, spec: SessionSpec, amendment_id: str) -> dict[str, Any]:
    """What every state of the open form shows: the ids, the links and the spec's texts."""
    return {
        "spec": spec,
        "amendment_id": amendment_id,
        "amendment_url": amendment_url(ctx.story_id, amendment_id) or "",
        "explanation": spec.explanation,
        "submit_text": spec.submit,
        "cancel_text": CANCEL_TEXT,
        "back_text": BACK_TO_AMENDMENT,
        "use_redo_hint": USE_REDO_HINT,
        "form": None,
        "summary": None,
    }


def _open_form_get(ctx: StoryPageContext, spec: SessionSpec, amendment_id: str) -> dict[str, Any]:
    context = _session_base(ctx, spec, amendment_id)
    try:
        detail = ctx.client.get_amendment(ctx.story_id, amendment_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(ctx.request, exc)
        if isinstance(exc, EngineError) and exc.code == NOT_FOUND_CODE:
            refusal = rewrite_refusal(exc, ctx.story_id, spec.verb_phrase)
            context["not_found"] = refusal.text if refusal is not None else exc.message
            return context
        refused = _refusal(exc)
        if refused is None:
            context["read_error"] = panel_for(exc)
        else:
            context["refusal"] = refused["refusal"]
        return context
    context["summary"] = amendment_row(detail, ctx.story_id)
    ineligible = spec.ineligible_text(detail)
    if ineligible is not None:
        context.update(ineligible=ineligible, action=amendment_action(detail, ctx.story_id))
        return context
    context.update(form=RewriteModesForm(), idempotency_key=new_ulid())
    return context


def _opened_flash(
    spec: SessionSpec, written: RewriteSessionWritten, key: str, *, apply_directly: bool
) -> SafeString:
    """``OPENED`` with the session's short id (full id in ``title``) and E21's key tail; with
    "Apply directly" ticked and nothing applied, E21's "Not applied directly: " first."""
    session_id = written.session.id
    text = format_html(
        OPENED.replace("{what}", "{}").replace("{session}", '<code title="{}">{}</code>'),
        spec.what,
        session_id,
        short_id(session_id),
    )
    prefix = NOT_APPLIED_PREFIX if apply_directly and written.apply is None else ""
    return format_html(
        "{}{}{}", prefix, text, format_html(KEY_TAIL, written.idempotency_key or key)
    )


def _redirect(target: str) -> HttpResponse:
    response = HttpResponseRedirect(target)
    response.status_code = 303
    return response


def _open_refused(
    ctx: StoryPageContext,
    spec: SessionSpec,
    form: RewriteModesForm,
    exc: EngineClientError,
    key: str,
) -> dict[str, Any]:
    """A refused open's additions: the sentence and its link, or #43's panel; D8's key."""
    if is_held(exc):
        raise exc
    log_engine_error(ctx.request, exc)
    if not isinstance(exc, EngineError) or exc.code == BAD_RESPONSE:
        return {"write_error": panel_for(exc), "idempotency_key": key}  # the outcome is unknown
    effective = ctx.config.effective if ctx.config is not None else None
    refusal = rewrite_refusal(exc, ctx.story_id, spec.verb_phrase, effective)
    if refusal is not None:
        return {
            "write_refusal": refusal.text,
            "refusal_url": refusal.url,
            "refusal_link": REFUSAL_LINKS.get(exc.code, DEFAULT_LINK) if refusal.url else "",
            "idempotency_key": new_ulid(),
        }
    if exc.code == VALIDATION and apply_engine_errors(exc, form, aliases=ALIASES):
        return {"idempotency_key": new_ulid()}
    if exc.http_status >= 500:
        return {"write_error": panel_for(exc), "idempotency_key": key}
    return {"write_error": panel_for(exc), "idempotency_key": new_ulid()}


def _open_form_post(
    ctx: StoryPageContext, spec: SessionSpec, amendment_id: str
) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    context = _session_base(ctx, spec, amendment_id)
    form = RewriteModesForm(request.POST)
    key = posted_idempotency_key(request)
    context.update(form=form, idempotency_key=key, posted=True)
    if not form.is_valid():
        return context
    apply_directly = bool(form.cleaned_data.get("apply_directly"))
    write = getattr(ctx.client, spec.method)
    try:
        written: RewriteSessionWritten = write(
            ctx.story_id, amendment_id, form.modes(), idempotency_key=key
        )
    except EngineClientError as exc:
        if (
            isinstance(exc, EngineError)
            and exc.code == BAD_RESPONSE
            and 200 <= exc.http_status < 300
        ):
            log_engine_error(request, exc)
            unreadable = format_html("{}{}", OPENED_UNREADABLE, format_html(KEY_TAIL, key))
            messages.warning(request, unreadable, extra_tags="safe")
            target = rewrites_url(ctx.story_id) or amendment_url(ctx.story_id, amendment_id)
            return _redirect(target or request.path)
        context.update(_open_refused(ctx, spec, form, exc, key))
        return context
    messages.success(
        request,
        _opened_flash(spec, written, key, apply_directly=apply_directly),
        extra_tags="safe",
    )
    target = (
        rewrite_session_url(ctx.story_id, written.session.id)
        or rewrites_url(ctx.story_id)
        or amendment_url(ctx.story_id, amendment_id)
    )
    return _redirect(target or request.path)


def _open_session(
    request: HttpRequest, story_id: str, amendment_id: str, spec: SessionSpec
) -> HttpResponse:
    """The shared undo/redo open form (module docstring)."""

    def build(ctx: StoryPageContext) -> dict[str, Any] | HttpResponse:
        if request.method == "POST":
            return _open_form_post(ctx, spec, amendment_id)
        return _open_form_get(ctx, spec, amendment_id)

    return story_page(
        request,
        story_id,
        SESSION_TEMPLATE,
        build,
        current=CURRENT,
        label=f"{spec.title} {short_id(amendment_id)}",
    )


@require_http_methods(["GET", "HEAD", "POST"])
def amendment_undo(request: HttpRequest, story_id: str, amendment_id: str) -> HttpResponse:
    """``stories:amendment-undo``: plan an undo of the amendment as a rewrite session (#303)."""
    return _open_session(request, story_id, amendment_id, UNDO)


@require_http_methods(["GET", "HEAD", "POST"])
def amendment_redo(request: HttpRequest, story_id: str, amendment_id: str) -> HttpResponse:
    """``stories:amendment-redo``: plan a redo of the undone amendment (#303)."""
    return _open_session(request, story_id, amendment_id, REDO)
