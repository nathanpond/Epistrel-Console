"""The Engine panel's Import a bundle page (#320; FR-STORE-7, FR-ADMIN-4; Engine #250/#251;
owner answer 12; E6, E7, E8, E11, E14, E18, E19, E21).

A story's restore is an import, and an import always makes a **new** story
(:data:`~console.forms.imports.RESTORE_LINE`). ``engine:import`` (``/engine/import/``,
GET/HEAD/POST) renders through #296's ``engine_page(..., current="engine:import")`` with the
Engine panel's own access (E11). One URL serves the form, the first stage and the confirm: a POST
carrying ``twostage.CONFIRM_FIELD`` is the confirm (E8).

- **GET**: the three reads of :func:`~console.forms.imports.read_import_choices` and no write; it
  sweeps the spool files older than ``twostage.MAX_AGE_S`` (E8) first.
- **First stage** (a POST without the token): the same three reads build the choices; an invalid
  form re-renders with a fresh key and calls nothing. A newly chosen file is spooled
  (``console.import_spool.spool``; an ``OSError`` is :data:`SPOOL_FAILED_TEXT` above the form,
  logged, with no call), else the kept spool is reopened. Then exactly one ``import_story(
  metadata, UploadPart(<the spool, opened>), idempotency_key=<the page's key>)``.
- **200 ``ImportImpact``** (a warning; nothing created): the report through
  ``console/_impact_report.html`` (#310's Mode and Gap columns), :data:`NOTHING_CREATED_YET`, the
  facts (file name and size, owner, name, rating, profile, upgrade mode) and
  ``console/_two_stage.html`` ("Import anyway"; Cancel back to the page, the sweep removes the
  spool). The token is ``twostage.sign("import", "", body, base)``: ``body`` is the metadata with
  ``ignore_warnings: true``, ``base`` the spool's description, ``config_revision`` and the facts'
  labels.
- **The confirm** unsigns the token (expired or tampered → ``EXPIRED_TEXT`` flashed, zero Engine
  calls, the spool deleted when the token still names it), opens the spool (missing →
  :data:`~console.import_spool.IMPORT_SPOOL_EXPIRED` flashed, zero calls), sends one
  ``import_story(body, part, idempotency_key=<the partial's fresh key>)`` and deletes the spool
  whatever the answer. A 200 again shows the new report with no confirm and
  ``IMPORT_SPOOL_EXPIRED`` above the form; a refusal re-renders the form bound to the token's
  metadata with no keep box; an unknown outcome keeps the posted key under #43's panel (the file
  must be chosen again, so the confirm itself can't be resent).
- **202 ``ImportStarted``** (either stage): the spool is deleted, the #44 row is ``queued`` on the
  new story (E19), and the view flashes "Import started: job <code>id</code>" with E21's key tail
  and answers 303 to ``import_job_url(job_id)`` (E6), #321's job page below. Were that name
  unregistered (D19) the POST would answer 200 with :data:`STARTED_TEXT` (the new story linked
  to ``stories:detail``) and the form reset below it.
- **Refusals** (``console.forms.imports.import_refusal``) re-render (200) with the text on its
  field. After a refusal a metadata change can fix (``rating_required``, ``validation_error``,
  ``profile_deleted``) the spool is kept and its signed name rides in the hidden ``spool`` field
  with a checked "Keep the uploaded file …" box; a refusal about the file (``bundle_too_large``,
  ``corrupt_bundle``, ``unsupported_format_version``), any other refusal (#43's panel) and an
  unknown outcome delete it. A 503 ``erasure_pending`` deletes it and is the frame's held
  fragment.
- **Recent imports from this Console**: ``jobs.recent_jobs("import")`` (E7; the Engine keeps no
  list of imports, E18), each row's job linked through ``import_job_url`` and its new story
  (``EngineOperation.entity_id``, one query) through ``stories:detail``.

**The import job page** (#321; owner answer 12; E7, E11, E23): ``engine:import-job``
(``/engine/import/<ulid:job_id>/``, :func:`import_job`, ``@require_safe``) renders through
``engine_page(..., current="engine:import", label="Import job …<short id>")``, one
``import_status`` read per render; ``?partial=1`` answers only ``console/_job_poll.html``
(``jobs.job_poll_context(JobKind.IMPORT, …)``, every 2 s, stopping at ``ready``, ``refused`` or
``failed``; an unknown status keeps polling) around ``_import_report.html``, which includes
#295's ``_job_status.html`` and then the import's own block (``console.imports_status``):

- running: the status line with E7's ``stage_text`` (``STAGE_TEXT``), a ``<progress>`` and
  "Stage <i> of 10" (a known stage only); a ``queued`` or missing stage reads "Importing" with an
  indeterminate bar;
- ``ready``: "Imported as <name> — open it" (``stories:detail``; the name from one ``get_story``
  on each terminal render only, any ``EngineClientError`` logged and the id shown instead), the
  backfill and parked-effects notes, the validation report;
- ``refused``/``failed``: "No story was created", the error (code, ``IMPORT_ERROR_TEXT``, the
  message, a rating check's violations, ``details`` in More), the report, "Try again".

Any terminal read settles the start's ``queued`` #44 row (E7). The partial's failure order is
E7's: 503 ``erasure_pending`` → the held fragment in the block with no trigger; 404 →
:data:`JOB_NOT_FOUND_TEXT` with no trigger (the full page answers 404 inside the frame); anything
else ``poll_failure`` unchanged. The full page leaves a held read to the frame and shows any
other failed read as #43's panel inside it.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.template.defaultfilters import filesizeformat
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_safe

from console import twostage
from console.acting import acting_principal
from console.confirm import posted_idempotency_key
from console.engine.client import (
    EngineClient,
    EngineClientError,
    EngineError,
    EngineUnreachable,
    UploadPart,
)
from console.engine.models import ImpactReport, ImportImpact, ImportJob, ImportStarted, Story
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.imports import (
    FIXABLE_REFUSALS,
    RESTORE_LINE,
    FormRefusal,
    ImportChoices,
    ImportForm,
    bound_data,
    import_refusal,
    keep_text,
    name_text,
    owner_text,
    read_import_choices,
)
from console.held import _ledger_url, held_text, is_held
from console.ids import new_ulid
from console.import_spool import (
    IMPORT_SPOOL_EXPIRED,
    Spool,
    delete_spool,
    open_spool,
    sign_spool,
    spool,
    spool_exists,
    spool_from_json,
    sweep_spools,
)
from console.imports_status import (
    NO_STORY_TEXT,
    TRY_AGAIN,
    error_view,
    import_report_rows,
    ready_html,
    ready_notes,
    shows_stage,
    stage_count_text,
    stage_text,
)
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
from console.models import EngineOperation
from console.portability import import_job_url
from console.upgrades import UPGRADE_MODE_TEXT, backfill_url, upgrade_mode_text
from console.views.story_config import model_json
from console.views.story_pages import EnginePageContext, engine_page

log = logging.getLogger("console.imports")

TEMPLATE = "console/engine/import.html"
CURRENT = "engine:import"
KIND = "import"
BAD_RESPONSE = "bad_response"
IMPORT_OPERATION = "import_story"

IMPORT_BUTTON = "Import"
CONFIRM_LABEL = "Import anyway"
NOTHING_CREATED_YET = "Nothing was created yet"
HELD_LINE = (
    "The Engine held this import: the engine profile's impact on the bundle's history warns. "
    "Review it, then import anyway or cancel."
)
STARTED_FLASH = "Import started: job <code>{}</code>"
STARTED_TEXT = "Import started: job <code>{job}</code>; the new story {story} opens once it ends"
KEY_TAIL = " — idempotency key <code>{}</code>"
SPOOL_FAILED_TEXT = "The Console couldn't hold the upload ({reason}) — nothing was created"
RECENT_HEADING = "Recent imports from this Console"
NOT_LISTED_NOTE = (
    "Imports started outside this Console aren't listed — the Engine keeps no list of imports"
)
NO_IMPORTS = "No imports from this Console yet"


# --- helpers --------------------------------------------------------------------------------------


def _unknown(exc: EngineClientError) -> bool:
    """D8's unknown outcome: no answer, or one that didn't parse (the import may have run)."""
    return isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )


def _with_key(text: SafeString | str, key: str) -> SafeString:
    return format_html("{}" + KEY_TAIL, text, key)


def _story_url(story_id: str | None) -> str | None:
    if not story_id:
        return None
    try:
        return reverse("stories:detail", args=[story_id])
    except NoReverseMatch:
        return None


def _see_other(url: str) -> HttpResponse:
    response = redirect(url)
    response.status_code = 303
    return response


def page_url() -> str:
    return reverse(CURRENT)


@dataclass(frozen=True)
class RecentRow:
    """One recent import: the #44 row's facts, the job page (``None`` while unregistered) and
    the new story (``""`` when the row names none)."""

    job_id: str
    started_at: Any
    principal: str
    outcome: str
    job_url: str | None
    story_id: str
    story_url: str | None


def recent_rows() -> list[RecentRow]:
    """E7's recent imports, newest first, each with its new story from one #44 query."""
    rows = recent_jobs(JobKind.IMPORT)
    ids = [row.job_id for row in rows]
    stories = dict(
        EngineOperation.objects.filter(kind=IMPORT_OPERATION, job_id__in=ids)
        .exclude(entity_id=None)
        .values_list("job_id", "entity_id")
    )
    found = []
    for row in rows:
        story_id = str(stories.get(row.job_id) or "")
        found.append(
            RecentRow(
                row.job_id,
                row.started_at,
                row.principal,
                row.outcome,
                import_job_url(row.job_id),
                story_id,
                _story_url(story_id),
            )
        )
    return found


def started_text(started: ImportStarted) -> SafeString:
    """:data:`STARTED_TEXT`, the new story linked to ``stories:detail``."""
    url = _story_url(started.story_id)
    if url is not None:
        story = format_html('<a href="{}"><code>{}</code></a>', url, started.story_id)
    else:
        story = format_html("<code>{}</code>", started.story_id)
    return format_html(STARTED_TEXT, job=started.job_id, story=story)


# --- the page context -----------------------------------------------------------------------------


@dataclass
class Shown:
    """What one render shows beyond the form."""

    refusal: FormRefusal | None = None
    panel: ErrorPanel | None = None
    form_error: str = ""
    notice: str = ""  # above the form (a confirm re-held: the spool is gone)
    kept: Spool | None = None  # the spool the form keeps (its box and signed field)
    key: str | None = None  # kept on an unknown outcome
    started: ImportStarted | None = None
    started_key: str = ""
    impact: ImpactReport | None = None  # a report shown without a confirm (re-held)


def _context(form: ImportForm, shown: Shown) -> dict[str, Any]:
    kept = shown.kept
    if kept is not None:  # the kept file stands in: the browser mustn't demand a new one
        form.fields["file"].required = False
    return {
        "stage": "form",
        "restore_line": RESTORE_LINE,
        "form": form,
        "idempotency_key": shown.key or new_ulid(),
        "import_button": IMPORT_BUTTON,
        "refusal": shown.refusal,
        "engine_error": shown.panel,
        "form_error": shown.form_error,
        "notice": shown.notice,
        "kept_token": sign_spool(kept) if kept is not None else "",
        "keep_text": keep_text(kept) if kept is not None else "",
        "started_text": started_text(shown.started) if shown.started is not None else "",
        "started_key": shown.started_key,
        "impact": shown.impact,
        "story_labels": {},
        **_recent(),
    }


def _recent() -> dict[str, Any]:
    return {
        "recent_heading": RECENT_HEADING,
        "recent": recent_rows(),
        "no_imports": NO_IMPORTS,
        "not_listed_note": NOT_LISTED_NOTE,
    }


def _form(
    request: HttpRequest, choices: ImportChoices, data: Any = None, files: Any = None
) -> ImportForm:
    return ImportForm(data, files, choices=choices, principal=acting_principal(request))


@dataclass(frozen=True)
class Facts:
    """The confirm page's facts, signed into ``base`` so the confirm shows them again."""

    filename: str
    size: int
    owner: str
    name: str
    rating: str
    profile: str
    upgrade_mode: str

    def rows(self) -> list[tuple[str, str]]:
        return [
            ("File", f"{self.filename} ({filesizeformat(self.size)})"),
            ("Owner", self.owner),
            ("Name", self.name),
            ("Content rating", self.rating),
            ("Engine profile", self.profile),
            ("Earlier turns", upgrade_mode_text(self.upgrade_mode)),
        ]


def _facts(
    request: HttpRequest, held: Spool, metadata: Mapping[str, Any], choices: ImportChoices
) -> Facts:
    return Facts(
        held.filename,
        held.size,
        owner_text(metadata, acting_principal(request)),
        name_text(metadata),
        choices.rating_label(str(metadata.get("content_rating_id") or "")),
        choices.profile_label(str(metadata.get("engine_profile_id") or "")),
        str(metadata.get("upgrade_mode") or ""),
    )


def _impact_context(
    request: HttpRequest,
    held: Spool,
    metadata: Mapping[str, Any],
    choices: ImportChoices,
    answer: ImportImpact,
) -> dict[str, Any]:
    facts = _facts(request, held, metadata, choices)
    body = {**metadata, "ignore_warnings": True}
    base = {
        "spool": held.as_json(),
        "config_revision": answer.config_revision,
        "facts": {
            "owner": facts.owner,
            "name": facts.name,
            "rating": facts.rating,
            "profile": facts.profile,
        },
        "report": model_json(answer.impact),
    }
    return {
        "stage": "impact",
        "restore_line": RESTORE_LINE,
        "held_line": HELD_LINE,
        "nothing_created": NOTHING_CREATED_YET,
        "facts": facts.rows(),
        "impact": answer.impact,
        "story_labels": {},
        "token": twostage.sign(KIND, "", body, base),
        "confirm_url": page_url(),
        "confirm_label": CONFIRM_LABEL,
        "cancel_url": page_url(),
        "confirm_key": new_ulid(),
        **_recent(),
    }


# --- answers --------------------------------------------------------------------------------------


def _started(
    ctx: EnginePageContext, choices: ImportChoices | None, started: ImportStarted, key: str
) -> dict[str, Any] | HttpResponse:
    """A 202: the flash and the 303 to the job page, or (D19) the result in place; the choices
    are read only for that render (``None``: not read yet)."""
    request = ctx.request
    shown_key = started.idempotency_key or key
    url = import_job_url(started.job_id)
    if url is not None:
        flash = _with_key(format_html(STARTED_FLASH, started.job_id), shown_key)
        messages.success(request, flash)
        return _see_other(url)
    # D19: before #321 the result renders in place (200), the form reset below it.
    if choices is None:
        choices = read_import_choices(ctx.client)
    return _context(_form(request, choices), Shown(started=started, started_key=shown_key))


def _refused(
    request: HttpRequest,
    exc: EngineClientError,
    form: ImportForm,
    held: Spool | None,
    key: str,
) -> dict[str, Any]:
    """A first stage's refusal or failure: the spool kept only for a fixable refusal."""
    log_engine_error(request, exc)
    code = exc.code if isinstance(exc, EngineError) else ""
    refusal = None if _unknown(exc) else import_refusal(exc)
    keep = refusal is not None and code in FIXABLE_REFUSALS
    if not keep and held is not None:
        delete_spool(held.name)
    if refusal is None:
        return _context(form, Shown(panel=panel_for(exc), key=key if _unknown(exc) else None))
    return _context(form, Shown(refusal=refusal, kept=held if keep else None))


def _send(
    client: EngineClient, metadata: Mapping[str, Any], held: Spool, key: str
) -> ImportImpact | ImportStarted | None:
    """One ``import_story`` from the spool; ``None`` when the spool can't be opened."""
    handle = open_spool(held.name)
    if handle is None:
        return None
    with handle:
        part = UploadPart(held.filename, held.content_type, held.size, handle)
        return client.import_story(metadata, part, idempotency_key=key)


# --- the stages -----------------------------------------------------------------------------------


def _first_stage(ctx: EnginePageContext, choices: ImportChoices) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    form = _form(request, choices, request.POST, request.FILES)
    if not form.is_valid():  # E21: a local-validation failure mints a fresh key; no call
        kept = form.kept if form.kept is not None and not form.files.get("file") else None
        return _context(form, Shown(kept=kept if kept and spool_exists(kept.name) else None))
    upload = form.cleaned_data.get("file")
    held = form.held_spool()
    if upload:
        if form.kept is not None:  # a newly chosen file wins
            delete_spool(form.kept.name)
        try:
            held = spool(upload)
        except OSError as exc:
            log.warning("couldn't spool an import upload", exc_info=True)
            text = SPOOL_FAILED_TEXT.format(reason=exc.strerror or type(exc).__name__)
            return _context(form, Shown(form_error=text))
    elif form.kept is not None and held is None:
        delete_spool(form.kept.name)
    if held is None:  # unreachable: the form requires a file or a ticked, existing spool
        return _context(form, Shown(form_error=IMPORT_SPOOL_EXPIRED))
    metadata = form.metadata()
    key = posted_idempotency_key(request)
    try:
        answer = _send(ctx.client, metadata, held, key)
    except EngineClientError as exc:
        if is_held(exc):
            delete_spool(held.name)
            raise
        return _refused(request, exc, form, held, key)
    if answer is None:  # swept between the check and the send
        form.add_error("file", IMPORT_SPOOL_EXPIRED)
        return _context(form, Shown())
    if isinstance(answer, ImportStarted):
        delete_spool(held.name)
        return _started(ctx, choices, answer, key)
    return _impact_context(request, held, metadata, choices, answer)


@dataclass(frozen=True)
class Signed:
    metadata: dict[str, Any]
    held: Spool


def _signed(token: str) -> Signed | None:
    """The confirm token's metadata and spool; ``None`` when it is expired, tampered or not one
    this page signed. An expired token that still names a spool deletes it."""
    try:
        payload = twostage.unsign(KIND, token)
    except twostage.ConfirmationExpired:
        try:  # past its age, the token still names the spool to remove
            stale = twostage.unsign(KIND, token, max_age=10**9)
        except twostage.ConfirmationExpired:
            return None
        base = stale.base if isinstance(stale.base, dict) else {}
        held = spool_from_json(base.get("spool"))
        if held is not None:
            delete_spool(held.name)
        return None
    body, base = payload.body, payload.base
    held = spool_from_json(base.get("spool")) if isinstance(base, dict) else None
    if (
        payload.target_id != ""
        or not isinstance(body, dict)
        or body.get("ignore_warnings") is not True
        or body.get("upgrade_mode") not in UPGRADE_MODE_TEXT
        or held is None
    ):
        return None
    return Signed(body, held)


def _confirm(ctx: EnginePageContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    signed = _signed(request.POST.get(twostage.CONFIRM_FIELD, ""))
    if signed is None:
        messages.error(request, twostage.EXPIRED_TEXT)
        return _see_other(page_url())
    key = posted_idempotency_key(request)
    try:
        answer = _send(ctx.client, signed.metadata, signed.held, key)
    except EngineClientError as exc:
        delete_spool(signed.held.name)
        if is_held(exc):
            raise
        log_engine_error(request, exc)
        choices = read_import_choices(ctx.client)
        form = _bound(request, choices, signed.metadata)
        refusal = None if _unknown(exc) else import_refusal(exc)
        if refusal is None:
            panel = panel_for(exc)
            return _context(form, Shown(panel=panel, key=key if _unknown(exc) else None))
        return _context(form, Shown(refusal=refusal))
    delete_spool(signed.held.name)
    if answer is None:
        messages.error(request, IMPORT_SPOOL_EXPIRED)
        return _see_other(page_url())
    if isinstance(answer, ImportStarted):
        return _started(ctx, None, answer, key)
    choices = read_import_choices(ctx.client)
    # Held again: the spool is gone (deleted whatever the answer), so no confirm.
    form = _bound(request, choices, signed.metadata)
    return _context(form, Shown(notice=IMPORT_SPOOL_EXPIRED, impact=answer.impact))


def _bound(request: HttpRequest, choices: ImportChoices, metadata: Mapping[str, Any]) -> ImportForm:
    """A fresh form showing a signed metadata's values (the file must be chosen again)."""
    form = _form(request, choices)
    form.initial.update(bound_data(metadata))
    return form


def _build(ctx: EnginePageContext) -> Mapping[str, Any] | HttpResponse:
    request = ctx.request
    if request.method == "POST" and twostage.CONFIRM_FIELD in request.POST:
        return _confirm(ctx)
    if request.method != "POST":
        sweep_spools()
    choices = read_import_choices(ctx.client)
    if request.method != "POST":
        return _context(_form(request, choices), Shown())
    return _first_stage(ctx, choices)


@require_http_methods(["GET", "HEAD", "POST"])
def import_page(request: HttpRequest) -> HttpResponse:
    """``engine:import`` (module docstring)."""
    return engine_page(request, TEMPLATE, _build, current=CURRENT)


# --- the import job page (#321) -------------------------------------------------------------------

JOB_TEMPLATE = "console/engine/import_job.html"
JOB_BODY_TEMPLATE = "console/engine/_import_report.html"
JOB_NOT_FOUND_TEXT = "Not found (no such import)"
READY = "ready"


def job_label(job_id: str) -> str:
    """E11's label for a page with no nav row: "Import job …<short id>"."""
    return f"Import job {short_id(job_id)}"


def job_page_url(job_id: str) -> str:
    return reverse("engine:import-job", args=[job_id])


def _job_urls(job_id: str) -> dict[str, str]:
    base = job_page_url(job_id)
    return {"poll_url": f"{base}?{PARTIAL_PARAM}=1", "refresh_url": base}


def settle_import(job: ImportJob) -> None:
    """E7: any terminal read settles the start's ``queued`` #44 row (idempotent)."""
    state = job_state(JobKind.IMPORT, job.status)
    if state.terminal and job.job_id:
        settle_job(job.job_id, failed=state.failed, error_code=settle_error_code(job.error))


def _ready_story(request: HttpRequest, client: EngineClient, story_id: str) -> Story | None:
    """The new story, read once on a ``ready`` render (the omniscient default the story page
    uses); ``None`` when the read fails (logged): the link then reads the id."""
    try:
        return client.get_story(story_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def _progress(job: ImportJob) -> dict[str, Any] | None:
    """E7's progress for ``job_state``: the job's counts with its stage, only while a stage
    shows (a ``queued`` or missing stage reads plain "Importing", the bar indeterminate)."""
    if not shows_stage(job.status, job.stage):
        return None
    raw = job.progress if isinstance(job.progress, Mapping) else {}
    return {**raw, "stage": job.stage}


def import_job_block(
    request: HttpRequest, client: EngineClient, job_id: str, job: ImportJob
) -> dict[str, Any]:
    """The polled block's context for ``job`` (E7's import kind, every 2 s): the status, the
    stage line, and once it ends the ready link and notes or the error, and the report."""
    urls = _job_urls(job_id)
    block = job_poll_context(
        JobKind.IMPORT,
        job_id,
        job.status,
        poll_url=urls["poll_url"],
        refresh_url=urls["refresh_url"],
        body_template=JOB_BODY_TEMPLATE,
        progress=_progress(job),
    )
    state = block["job"]
    staged = shows_stage(job.status, job.stage)
    ready = state.terminal and job.status == READY
    story: Story | None = None
    if ready and job.story_id:
        story = _ready_story(request, client, job.story_id)
    return {
        **block,
        "import_job": job,
        "error": None,  # the import's error renders below, not in the generic block
        "facts": (),
        "stage_text": stage_text(job.stage) if staged else "",
        "stage_count": stage_count_text(job.stage) if staged else "",
        "ready": ready,
        "ready_html": ready_html(job.story_id, story) if ready else "",
        "notes": (
            ready_notes(job.story_id, story, job.report, backfill_url(job.story_id or ""))
            if ready
            else []
        ),
        "no_story_text": NO_STORY_TEXT,
        "failed_view": error_view(job.error) if state.terminal and state.failed else None,
        "report": (
            import_report_rows(job.report) if state.terminal and job.report is not None else None
        ),
        "try_again_url": page_url() if state.terminal and state.failed else "",
        "try_again": TRY_AGAIN,
    }


def _empty_job_block(job_id: str, *, polling: bool, retrying: bool = False) -> dict[str, Any]:
    urls = _job_urls(job_id)
    return {
        **poll_context(
            poll_url=urls["poll_url"],
            refresh_url=urls["refresh_url"],
            body_template=JOB_BODY_TEMPLATE,
            polling=polling,
            retrying=retrying,
            trigger=POLL_TRIGGER,
        ),
        "import_job": None,
    }


def _is_404(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.http_status == 404


def _job_partial(request: HttpRequest, job_id: str) -> HttpResponse:
    """E7's partial failure order: held, then not found, then ``poll_failure`` unchanged."""
    client = engine_client_for(request)
    try:
        job = client.import_status(job_id)
    except EngineClientError as exc:
        if is_held(exc):
            log_engine_error(request, exc)
            held = {
                "held": True,
                "held_text": held_text(exc.retry_after),
                "retry_url": job_page_url(job_id),
                "ledger_url": _ledger_url(),
                "delete_url": "",
            }
            return render(
                request, POLL_TEMPLATE, {**_empty_job_block(job_id, polling=False), **held}
            )
        if _is_404(exc):
            log_engine_error(request, exc)
            context = {**_empty_job_block(job_id, polling=False), "not_found": JOB_NOT_FOUND_TEXT}
            return render(request, POLL_TEMPLATE, context)
        failure = poll_failure(request, exc)
        if failure.retry:
            return render(
                request, POLL_TEMPLATE, _empty_job_block(job_id, polling=True, retrying=True)
            )
        context = {**_empty_job_block(job_id, polling=False), "poll_panel": failure.panel}
        return render(request, POLL_TEMPLATE, context)
    settle_import(job)
    return render(request, POLL_TEMPLATE, import_job_block(request, client, job_id, job))


@require_safe
def import_job(request: HttpRequest, job_id: str) -> HttpResponse:
    """``engine:import-job`` (``/engine/import/<ulid:job_id>/``, #321): one ``import_status``
    read per render, framed by ``engine_page`` with "Import a bundle" current; ``?partial=1``
    answers only the poll block. A 404 is :data:`JOB_NOT_FOUND_TEXT` (404 inside the frame; 200
    with no trigger on the partial); a 503 ``erasure_pending`` is the frame's held fragment; any
    other failed read of the full page is #43's panel inside the frame."""
    if request.GET.get(PARTIAL_PARAM) == "1":
        return _job_partial(request, job_id)
    missing: list[bool] = []

    def build(ctx: EnginePageContext) -> Mapping[str, Any]:
        try:
            job = ctx.client.import_status(job_id)
        except EngineClientError as exc:
            if not _is_404(exc):
                raise
            log_engine_error(ctx.request, exc)
            missing.append(True)
            return {"not_found": JOB_NOT_FOUND_TEXT, "job_id": job_id}
        settle_import(job)
        return import_job_block(ctx.request, ctx.client, job_id, job)

    response = engine_page(request, JOB_TEMPLATE, build, current=CURRENT, label=job_label(job_id))
    if missing and response.status_code == 200:
        response.status_code = 404
    return response
