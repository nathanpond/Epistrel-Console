"""Export a story and download its bundle (#319, FR-STORE-7, FR-ADMIN-4; E7, E11, E18, E21, E23).

A story's **snapshot is its export** (Engine ``docs/OPERATIONS.md``, "Story snapshots and
restore"), and its restore is an import, which always makes a new story (#320/#321). Three URLs
(E11), each framed by #296's :func:`~console.views.story_pages.story_page` with the "Export" nav
row current:

- ``stories:export`` (``/stories/<id>/export/``, :func:`export`): **GET** makes the frame's two
  reads and one local :func:`console.jobs.recent_jobs` query, and no Engine write. It shows
  :data:`SNAPSHOT_LINE` ("an import" linked to ``engine:import`` while it resolves, D19), the
  "Export now" form (its only input the per-render idempotency key, E21) and "Recent exports from
  this Console" (the #44 rows, E7; the Engine keeps no export list, E18) with
  :data:`NOT_LISTED_NOTE`. **POST** sends exactly one ``export_story(story_id,
  idempotency_key=key)`` (E19: no expected revision; the #44 row is ``queued``). A 202 is a 303
  to the job page (no flash). A refusal re-renders the page at 200 with C5's text for
  :data:`VERB_PHRASE` above the button (``story_busy`` with " · retry after <n> s") and a fresh
  key; an unknown outcome (no answer, ``bad_response``) keeps the posted key under #43's panel.
  A held (#87) or importing (E10) story is re-raised to its middleware; anything else is #43's
  panel inside the frame. Nothing is retried.
- ``stories:export-job`` (``export/<ulid:job_id>/``, :func:`export_job`): E7's export job page,
  ``console/_job_poll.html`` around ``_export_job_body.html`` (which includes
  ``console/_job_status.html``), polling every 2 s until ``ready``, ``failed`` or ``expired``.
  One ``export_status`` read per render; the full GET reads it inside ``story_page``'s
  ``build``, and ``?partial=1`` skips the frame. A job whose ``story_id`` isn't the URL's
  (compared upper-cased; a missing one counts as another story's) is :data:`WRONG_STORY_TEXT`, a
  404 job :data:`NOT_FOUND_TEXT`: 404 inside the frame (label "Export …<short id>"), 200 with no
  trigger on the partial, and never settled. Any terminal read settles the start's ``queued``
  #44 row (E7). Ready shows the size, the full sha256, the revision, the expiry and "Download"
  with :func:`~console.portability.bundle_filename`'s name, then the raw manifest under More
  (E15); a ready job's null fact reads "—" and Download stays (E23). Failed shows the error's
  code humanised, :data:`EXPORT_ERROR_TEXT` when known, the humanised ``reason`` and the raw
  error under More (the Engine's export ``error`` is ``{code, reason?, member?, media_id?,
  event_id?}``: no ``message``). Failed and expired offer "Export again" (a POST to
  ``stories:export`` with its own key). The partial's ready render reads the story once (for the
  filename); without it the "saves as" part is left out.
- ``stories:export-download`` (``export/<ulid:job_id>/bundle/``, :func:`export_download`):
  ``get_story``, then ``export_status`` (another story's job or a 404 is the job page's
  not-found render, a ``failed`` job a redirect with :data:`FAILED_FLASH`; the stream is never
  opened), then ``open_export_bundle``, streamed through
  :class:`console.views.media_proxy.StreamedBody` (owner answer 11: the Engine's URL never
  reaches the page, a header or a redirect). The answer carries the Engine's ``Content-Type``
  (``application/gzip`` when none) and ``Content-Length``, the Console's own
  ``Content-Disposition`` (never the Engine's), ``Cache-Control: private, no-store``, ``nosniff``
  and the media proxy's sandbox CSP, and no other Engine header. No request header is forwarded,
  so the answer is a full 200. A non-success stream redirects to the job page with a
  ``messages.error`` flash (:data:`STREAM_FLASHES`, else :data:`DOWNLOAD_FAILED`); no answer is
  #43's panel inside the frame (502 unreachable, 504 timeout). HEAD makes both reads and answers
  the headers without opening the stream. The download is never recorded (#292).
"""

import logging
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.template.defaultfilters import filesizeformat
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import posted_idempotency_key
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ExportJob, Story
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.held import _ledger_url, held_text, is_held
from console.ids import new_ulid
from console.inspect import short_id
from console.job_poll import POLL_TEMPLATE, poll_context, poll_failure, settle_job
from console.jobs import (
    PARTIAL_PARAM,
    POLL_TRIGGER,
    JobKind,
    job_poll_context,
    job_state,
    recent_jobs,
    settle_error_code,
)
from console.portability import bundle_filename, is_importing
from console.raw import EMPTY, more_json, number_text
from console.templatetags.console_format import engine_time
from console.views.certify import busy_text
from console.views.media_proxy import CSP, StreamedBody
from console.views.story_pages import StoryPageContext, story_page
from console.wording import humanised, write_refusal

log = logging.getLogger("console.exports")

TEMPLATE = "console/stories/export.html"
JOB_TEMPLATE = "console/stories/export_job.html"
BODY_TEMPLATE = "console/stories/_export_job_body.html"
CURRENT = "stories:export"

SNAPSHOT_LINE = (
    "An export is this story's snapshot: one checksummed bundle of its history at one revision. "
    "Restoring a bundle is an import, which always makes a new story"
)
IMPORT_WORDS = "an import"
NOT_LISTED_NOTE = (
    "Exports started outside this Console aren't listed — the Engine keeps no list of exports"
)
LATEST_TEXT = "Showing the latest {n}"
RECENT_LIMIT = 20
EXPORT_NOW = "Export now"
EXPORT_AGAIN = "Export again"
VERB_PHRASE = "export the story"
EXPORT_ERROR_TEXT: Mapping[str, str] = {
    "secret_detected": "A configured secret was found in the story's records, so no file was kept",
}
WRONG_STORY_TEXT = "Not found (that export isn't this story's)"
NOT_FOUND_TEXT = "Not found (no such export)"
NOT_READY_FLASH = "This export isn't ready yet — wait for it to finish"
EXPIRED_FLASH = "This export has expired — export the story again"
NO_SUCH_FLASH = "No such export"
FAILED_FLASH = "This export failed — export the story again"
DOWNLOAD_FAILED = "Download failed — {code}"
STREAM_FLASHES: Mapping[str, str] = {
    "export_not_ready": NOT_READY_FLASH,
    "export_expired": EXPIRED_FLASH,
    "job_not_found": NO_SUCH_FLASH,
}
FALLBACK_CONTENT_TYPE = "application/gzip"
CACHE_CONTROL = "private, no-store"
BAD_RESPONSE = "bad_response"
READY = "ready"
FAILED = "failed"
EXPIRED = "expired"


# --- small helpers --------------------------------------------------------------------------------


def _reversed(name: str, *args: str) -> str | None:
    try:
        return reverse(name, args=list(args))
    except NoReverseMatch:
        return None


def export_page_url(story_id: str) -> str:
    return reverse("stories:export", args=[story_id])


def job_page_url(story_id: str, job_id: str) -> str:
    return reverse("stories:export-job", args=[story_id, job_id])


def download_url(story_id: str, job_id: str) -> str:
    return reverse("stories:export-download", args=[story_id, job_id])


def job_label(job_id: str) -> str:
    """E11's sim2 label for the job page: "Export …<short id>"."""
    return f"Export {short_id(job_id)}"


def snapshot_line() -> SafeString:
    """:data:`SNAPSHOT_LINE`, "an import" linked to ``engine:import`` when it resolves (D19)."""
    before, words, after = SNAPSHOT_LINE.partition(IMPORT_WORDS)
    url = _reversed("engine:import")
    if url is None:
        return format_html("{}", SNAPSHOT_LINE)
    return format_html('{}<a class="import-link" href="{}">{}</a>{}', before, url, words, after)


def _passed_on(exc: EngineClientError) -> bool:
    return is_held(exc) or is_importing(exc)


def _unknown(exc: EngineClientError) -> bool:
    """D8's unknown outcome: no answer, or one that didn't parse (the write may have run)."""
    return isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )


def _is_404(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 404


def is_this_storys(job: ExportJob, story_id: str) -> bool:
    """The wrong-story check: the job's ``story_id`` and the URL's, upper-cased (ULIDs are
    case-insensitive); a missing or null ``story_id`` is another story's."""
    found = job.story_id
    return isinstance(found, str) and bool(found) and found.upper() == story_id.upper()


def settle(job: ExportJob) -> None:
    """E7: any terminal read settles the start's ``queued`` #44 row (idempotent)."""
    state = job_state(JobKind.EXPORT, job.status)
    if state.terminal and job.job_id:
        settle_job(job.job_id, failed=state.failed, error_code=settle_error_code(job.error))


# --- the Export page ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Refusal:
    text: str
    url: str | None = None


def refusal_for(exc: EngineClientError, story_id: str) -> Refusal | None:
    """C5's line for a refused export (``story_busy`` with its retry), or ``None`` (#43's panel)."""
    if not isinstance(exc, EngineError):
        return None
    if exc.code == "story_busy":
        return Refusal(busy_text(exc))
    found = write_refusal(exc, story_id, VERB_PHRASE)
    if found is None:
        return None
    return Refusal(found.text, found.url or None)


def _page_context(
    ctx: StoryPageContext,
    *,
    refusal: Refusal | None = None,
    panel: ErrorPanel | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    rows = recent_jobs(JobKind.EXPORT, story_id=ctx.story_id, limit=RECENT_LIMIT)
    return {
        "snapshot_line": snapshot_line(),
        "export_url": export_page_url(ctx.story_id),
        "export_label": EXPORT_NOW,
        "export_key": key or new_ulid(),
        "refusal": refusal,
        "engine_error": panel,
        "recent": [(row, job_page_url(ctx.story_id, row.job_id)) for row in rows],
        "latest_text": LATEST_TEXT.format(n=RECENT_LIMIT) if len(rows) == RECENT_LIMIT else "",
        "not_listed_note": NOT_LISTED_NOTE,
    }


def _render_page(request: HttpRequest, story_id: str, **shown: Any) -> HttpResponse:
    return story_page(
        request, story_id, TEMPLATE, lambda ctx: _page_context(ctx, **shown), current=CURRENT
    )


def _start(request: HttpRequest, story_id: str) -> HttpResponse:
    key = posted_idempotency_key(request)
    client = engine_client_for(request)
    try:
        started = client.export_story(story_id, idempotency_key=key)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        if _unknown(exc):  # E21: the posted key is kept, so a resubmit replays
            return _render_page(request, story_id, panel=panel_for(exc), key=key)
        found = refusal_for(exc, story_id)
        if found is not None:
            return _render_page(request, story_id, refusal=found)

        panel = panel_for(exc)

        def build(ctx: StoryPageContext) -> Mapping[str, Any]:
            return _page_context(ctx, panel=panel)

        return story_page(request, story_id, TEMPLATE, build, current=CURRENT)
    response = redirect("stories:export-job", story_id=story_id, job_id=started.job_id)
    response.status_code = 303  # E21: PRG
    return response


@require_http_methods(["GET", "HEAD", "POST"])
def export(request: HttpRequest, story_id: str) -> HttpResponse:
    """``stories:export`` (module docstring)."""
    if request.method == "POST":
        return _start(request, story_id)
    return _render_page(request, story_id)


# --- the job page ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Named:
    """A story for :func:`bundle_filename` when only its id is known."""

    id: str
    name: Any = None


def _fact(value: object, shown: str | SafeString) -> str | SafeString:
    return EMPTY if value is None or value == "" else shown


def ready_facts(job: ExportJob) -> list[tuple[str, str | SafeString]]:
    """E7's export facts (E23: a null one reads "—")."""
    size = job.size if isinstance(job.size, int) and not isinstance(job.size, bool) else None
    revision = job.exported_revision
    return [
        ("Size", _fact(size, filesizeformat(size) if size is not None else EMPTY)),
        (
            "SHA-256",
            _fact(job.sha256, format_html('<code class="hash">{}</code>', job.sha256 or "")),
        ),
        ("Exported at revision", _fact(revision, number_text(revision))),
        ("Expires", engine_time(job.expires_at)),
    ]


@dataclass(frozen=True)
class ErrorLines:
    """A failed (or erased-and-expired) export's error, as lines: the code humanised, its known
    text, the humanised reason, and the raw error for More."""

    code_text: str
    code_raw: str
    known_text: str
    reason_text: str
    more: str


def error_lines(error: Mapping[str, Any] | None) -> ErrorLines | None:
    if not error:
        return None
    code = error.get("code")
    reason = error.get("reason")
    code_raw = code if isinstance(code, str) else ""
    return ErrorLines(
        code_text=humanised(code_raw) if code_raw else EMPTY,
        code_raw=code_raw,
        known_text=EXPORT_ERROR_TEXT.get(code_raw, ""),
        reason_text=humanised(reason) if isinstance(reason, str) and reason.strip() else "",
        more=more_json(dict(error)),
    )


def _base_block(story_id: str, job_id: str) -> dict[str, str]:
    base = job_page_url(story_id, job_id)
    return {"poll_url": f"{base}?{PARTIAL_PARAM}=1", "refresh_url": base}


def job_block(story_id: str, job: ExportJob, filename: str | None) -> dict[str, Any]:
    """The polled block's context for ``job`` (E7)."""
    urls = _base_block(story_id, job.job_id)
    status = job.status
    block = job_poll_context(
        JobKind.EXPORT,
        job.job_id,
        status,
        poll_url=urls["poll_url"],
        refresh_url=urls["refresh_url"],
        body_template=BODY_TEMPLATE,
    )
    ready = status == READY
    return {
        **block,
        "export": job,
        "error": None,  # the export's error renders as error_lines, not the generic block
        "facts": ready_facts(job) if ready else (),
        "ready": ready,
        "download_url": download_url(story_id, job.job_id) if ready else None,
        "filename": filename,
        "manifest": more_json(job.manifest) if ready else "",
        "error_lines": error_lines(job.error) if status in (FAILED, EXPIRED) else None,
        "export_again": status in (FAILED, EXPIRED),
        "export_url": export_page_url(story_id),
        "export_again_label": EXPORT_AGAIN,
        "export_key": new_ulid(),
    }


def _empty_block(
    story_id: str, job_id: str, *, polling: bool, retrying: bool = False
) -> dict[str, Any]:
    return {
        **poll_context(
            **_base_block(story_id, job_id),
            body_template=BODY_TEMPLATE,
            polling=polling,
            retrying=retrying,
            trigger=POLL_TRIGGER,
        ),
        "export": None,
    }


def _filename_for_partial(request: HttpRequest, story_id: str) -> str | None:
    """The ready partial's filename: one story read (the frame isn't there); ``None`` when it
    fails, and the "saves as" part is left out."""
    try:
        story = engine_client_for(request).get_story(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None
    return bundle_filename(story)


def _partial(request: HttpRequest, story_id: str, job_id: str) -> HttpResponse:
    """E7's partial failure order: held, then not found / another story's, then
    ``poll_failure`` unchanged."""
    try:
        job = engine_client_for(request).export_status(job_id)
    except EngineClientError as exc:
        if is_held(exc):
            log_engine_error(request, exc)
            held = {
                "held_text": held_text(exc.retry_after),
                "retry_url": job_page_url(story_id, job_id),
                "ledger_url": _ledger_url(),
                "delete_url": "",
                "held": True,
            }
            return render(
                request, POLL_TEMPLATE, {**_empty_block(story_id, job_id, polling=False), **held}
            )
        if _is_404(exc):
            log_engine_error(request, exc)
            context = {
                **_empty_block(story_id, job_id, polling=False),
                "not_found": NOT_FOUND_TEXT,
            }
            return render(request, POLL_TEMPLATE, context)
        failure = poll_failure(request, exc)
        if failure.retry:
            return render(
                request,
                POLL_TEMPLATE,
                _empty_block(story_id, job_id, polling=True, retrying=True),
            )
        context = {**_empty_block(story_id, job_id, polling=False), "poll_panel": failure.panel}
        return render(request, POLL_TEMPLATE, context)
    if not is_this_storys(job, story_id):
        context = {**_empty_block(story_id, job_id, polling=False), "not_found": WRONG_STORY_TEXT}
        return render(request, POLL_TEMPLATE, context)
    settle(job)
    filename = _filename_for_partial(request, story_id) if job.status == READY else None
    return render(request, POLL_TEMPLATE, job_block(story_id, job, filename))


def _with_404(response: HttpResponse, flag: list[bool]) -> HttpResponse:
    if flag and response.status_code == 200:
        response.status_code = 404
    return response


def _story_named(ctx: StoryPageContext) -> Story | Named:
    return ctx.story if ctx.story is not None else Named(ctx.story_id)


def _not_found_page(request: HttpRequest, story_id: str, job_id: str, text: str) -> HttpResponse:
    """The job page's not-found render: 404 inside the frame, the Export row current."""

    def build(ctx: StoryPageContext) -> Mapping[str, Any]:
        return {"not_found": text, "job_id": job_id}

    response = story_page(
        request, story_id, JOB_TEMPLATE, build, current=CURRENT, label=job_label(job_id)
    )
    return _with_404(response, [True])


@require_safe
def export_job(request: HttpRequest, story_id: str, job_id: str) -> HttpResponse:
    """``stories:export-job`` (module docstring)."""
    if request.GET.get(PARTIAL_PARAM) == "1":
        return _partial(request, story_id, job_id)
    missing: list[bool] = []

    def build(ctx: StoryPageContext) -> Mapping[str, Any]:
        try:
            job = ctx.client.export_status(job_id)
        except EngineClientError as exc:
            if not _is_404(exc):
                raise
            log_engine_error(ctx.request, exc)
            missing.append(True)
            return {"not_found": NOT_FOUND_TEXT, "job_id": job_id}
        if not is_this_storys(job, ctx.story_id):
            missing.append(True)
            return {"not_found": WRONG_STORY_TEXT, "job_id": job_id}
        settle(job)
        filename = bundle_filename(_story_named(ctx)) if job.status == READY else None
        return job_block(ctx.story_id, job, filename)

    response = story_page(
        request, story_id, JOB_TEMPLATE, build, current=CURRENT, label=job_label(job_id)
    )
    return _with_404(response, missing)


# --- the download ---------------------------------------------------------------------------------


def _framed_error(
    request: HttpRequest, story_id: str, job_id: str, exc: EngineClientError, status: int | None
) -> HttpResponse:
    """#43's panel inside the job page's frame (the error escapes ``build``); ``status`` when
    there was no Engine answer (502/504)."""

    def build(ctx: StoryPageContext) -> Mapping[str, Any]:
        raise exc

    response = story_page(
        request, story_id, JOB_TEMPLATE, build, current=CURRENT, label=job_label(job_id)
    )
    if status is not None and response.status_code == 200:
        response.status_code = status
    return response


def _no_answer_status(exc: EngineUnreachable) -> int:
    return 504 if exc.reason == "timeout" else 502


def _to_job_page(request: HttpRequest, story_id: str, job_id: str, text: str) -> HttpResponse:
    messages.error(request, text)  # E21: a failed download or stream
    return redirect("stories:export-job", story_id=story_id, job_id=job_id)


def _headers(response: HttpResponse | StreamingHttpResponse, filename: str) -> None:
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = CACHE_CONTROL
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = CSP


@require_safe
def export_download(
    request: HttpRequest, story_id: str, job_id: str
) -> HttpResponse | StreamingHttpResponse:
    """``stories:export-download`` (module docstring)."""
    client = engine_client_for(request)
    try:
        story = client.get_story(story_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        if _is_404(exc):  # the frame's own read says not found
            return _framed_error(request, story_id, job_id, exc, None)
        status = _no_answer_status(exc) if isinstance(exc, EngineUnreachable) else None
        return _framed_error(request, story_id, job_id, exc, status)
    try:
        job = client.export_status(job_id)
    except EngineClientError as exc:
        if _passed_on(exc):
            raise
        log_engine_error(request, exc)
        if _is_404(exc):
            return _not_found_page(request, story_id, job_id, NOT_FOUND_TEXT)
        status = _no_answer_status(exc) if isinstance(exc, EngineUnreachable) else None
        return _framed_error(request, story_id, job_id, exc, status)
    if not is_this_storys(job, story_id):
        return _not_found_page(request, story_id, job_id, WRONG_STORY_TEXT)
    if job.status == FAILED:
        return _to_job_page(request, story_id, job_id, FAILED_FLASH)
    filename = bundle_filename(story)
    if request.method == "HEAD":
        # A streaming answer, so no Content-Length is added (CommonMiddleware's).
        head = StreamingHttpResponse(iter(()), content_type=FALLBACK_CONTENT_TYPE)
        _headers(head, filename)
        return head
    stack = ExitStack()
    try:
        content = stack.enter_context(client.open_export_bundle(job_id))
    except EngineUnreachable as exc:
        stack.close()
        log.warning("export %s: %s", job_id, exc.reason)
        return _framed_error(request, story_id, job_id, exc, _no_answer_status(exc))
    if not 200 <= content.status < 300:
        try:
            with stack:
                error = content.error()
        except EngineUnreachable as exc:
            log.warning("export %s: %s", job_id, exc.reason)
            return _framed_error(request, story_id, job_id, exc, _no_answer_status(exc))
        log.warning("export %s: %s %s", job_id, content.status, error.code)
        text = STREAM_FLASHES.get(error.code) or DOWNLOAD_FAILED.format(code=humanised(error.code))
        return _to_job_page(request, story_id, job_id, text)
    body = StreamedBody(content, stack, job_id, logger=log, what="export")
    response = StreamingHttpResponse(body, status=content.status)
    response["Content-Type"] = content.headers.get("Content-Type") or FALLBACK_CONTENT_TYPE
    if content.headers.get("Content-Length"):
        response["Content-Length"] = content.headers["Content-Length"]
    _headers(response, filename)
    return response
