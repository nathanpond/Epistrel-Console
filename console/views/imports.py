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
  and answers 303 to ``import_job_url(job_id)`` (E6, #321). While that name is unregistered (D19)
  the POST answers 200 with :data:`STARTED_TEXT` (the new story linked to ``stories:detail``) and
  the form reset below it.
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
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.defaultfilters import filesizeformat
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods

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
from console.engine.models import ImpactReport, ImportImpact, ImportStarted
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
from console.held import is_held
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
from console.jobs import JobKind, recent_jobs
from console.models import EngineOperation
from console.portability import import_job_url
from console.upgrades import UPGRADE_MODE_TEXT, upgrade_mode_text
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
