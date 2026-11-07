"""The Engine panel's certification admin writes (#315; Engine #235's import, #234's invalidate;
owner answer 8; E11, E19, E21, E23, D8, D14, D19, C13).

Both live under the Engine's ``/admin`` scope. The Console adds no admin gate of its own (E11):
these pages use the Engine panel's access, and an Engine 403 renders #43's panel.

- **``engine:certification-import``** (``/engine/certification-import/``, GET/HEAD/POST): the
  :class:`~console.forms.certification_admin.CertificationImportForm` page, framed by #296's
  ``engine_page`` (Certifications current, label :data:`IMPORT_LABEL`). The GET makes no Engine
  call. A valid POST calls ``import_certification(record, idempotency_key=<the page's key>)``
  with the parsed record as given. ``stored: true`` → a 303 to ``engine:certification`` flashing
  "Stored certification <short id>"; ``stored: false`` → the same redirect with
  :data:`ALREADY_STORED` (info). Both end with E21's key tail; the id is the answer's, else the
  record's. Not behind a confirm page: an import only adds a record, and a same-id different
  record is refused.

  Refusals re-render the form (200) with the record kept and a fresh key (D8, E21):
  ``invalid_certification_record`` → "The Engine refused this record: <reason text>" with one
  line per ``details.errors[]`` entry ("<loc>: <humanised type>"; an entry missing either is
  shown raw); ``certification_record_conflict`` → :data:`CONFLICT_TEXT`, linked to the stored
  certification (``details.certification_id``, else the record's, through E6's
  ``certification_url``); ``validation_error`` → each ``body.<member>`` on the textarea's error
  list (anything else unmapped is #43's panel, logged); anything else → #43's panel. An unknown
  outcome (timeout, transport, ``bad_response``) keeps the posted key. A 503
  ``erasure_pending`` is the frame's held fragment (D17).

- **``engine:certification-invalidate``** (``certifications/<str:certification_id>/invalidate/``,
  GET/HEAD/POST): the id is trimmed and upper-cased (C13); a non-ULID is #43's not-found page
  (404) with no call. #43's confirm page (``console/confirm_page.html``, no Engine frame) in
  #134's confirmed mode with ``danger=True`` (only a new run undoes it) and the fields
  :class:`~console.forms.certification_admin.InvalidateFields` (``_invalidate_fields.html``).
  Its display-only facts (Fingerprint, Profile) come from ``?fingerprint=&profile=`` (#313's
  link, :func:`console.views.certifications.invalidate_url`) and ride in ``hidden`` across the
  POST; Cancel is ``next`` or the certification page. The GET makes no Engine call; a POST
  without ``confirmed=1`` re-renders with none; a confirmed POST with a missing reason or a blank
  note re-renders with the field errors and the same key, and calls nothing. Otherwise
  ``invalidate_certification(id, reason=…, note=<stripped>, idempotency_key=key)``; success is a
  303 to ``engine:certification`` with :data:`INVALIDATED_TEXT`. Refusals set the page's
  ``error`` (the form stays usable): ``certification_not_valid`` (:func:`not_valid_text`),
  ``certification_not_found`` (:data:`NOT_FOUND_TEXT`); a ``validation_error`` at
  ``body.reason``/``body.note`` lands on that field (E21); anything else is #43's panel above
  the page (an unknown outcome keeps the key, E21).

Both writes are recorded (#44) about the certification (``RecordedAbout("certification", id)``,
#292). The note is stored with the row's request body, as every write's body is.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods

from console.certification import INVALIDATED_REASON_TEXT, certification_url, certifications_url
from console.confirm import (
    CONFIRMED_FIELD,
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import CertificationImported
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import engine_field_errors
from console.forms.certification import loc_field
from console.forms.certification_admin import CertificationImportForm, InvalidateFields
from console.held import is_held
from console.ids import is_ulid, new_ulid
from console.inspect import short_id
from console.raw import EMPTY, compact_json
from console.templatetags.inspect_tags import engine_id
from console.views.certifications import MALFORMED_TEXT, NOT_FOUND_TEXT
from console.views.story_pages import EnginePageContext, engine_page
from console.wording import humanised

IMPORT_TEMPLATE = "console/engine/certification_import.html"
FIELDS_TEMPLATE = "console/engine/_invalidate_fields.html"
NOT_FOUND_PAGE = "console/engine_error_page.html"
IMPORT_LABEL = "Import a certification record"
IMPORT_BUTTON = "Import"
CERTIFICATIONS_LINK = "Certifications"

BAD_RESPONSE = "bad_response"
INVALID_RECORD = "invalid_certification_record"
RECORD_CONFLICT = "certification_record_conflict"
VALIDATION_ERROR = "validation_error"
NOT_VALID = "certification_not_valid"
NOT_FOUND = "certification_not_found"

# --- wording --------------------------------------------------------------------------------------

STORED_TEXT = "Stored certification {}"
ALREADY_STORED = "This record was already stored — nothing changed"
REFUSED_TEXT = "The Engine refused this record: {reason}"
REASON_TEXT = {
    "report_hash": "The report doesn't match its report_sha256 — re-export the record",
    "report_missing": "The record has no report (report_md) — re-export it from the run",
}
SCHEMA_REASON = "schema"  # the Engine's message is the reason text
CONFLICT_TEXT = "A different record is already stored under {id} — nothing was stored"
KEY_TAIL = " — idempotency key <code>{}</code>"

INVALIDATE_TITLE = "Invalidate certification"
INVALIDATE_QUESTION = "Mark certification {id} stale?"
INVALIDATE_LINES = (
    "Stories on its fingerprint report Stale from their next turn. The record is kept.",
    "Only a new certification run makes the configuration Certified again (with a new "
    "certification).",
)
INVALIDATE_LABEL = "Invalidate"
INVALIDATED_TEXT = (
    "Certification marked stale ({reason}) — stories on its fingerprint report Stale from their "
    "next turn"
)
ALREADY_STALE = "This certification is already stale — nothing changed"
NOT_VALID_STATUS = "This certification is {status} — only a valid one can be invalidated"
NOT_VALID_UNKNOWN = "This certification isn't valid — only a valid one can be invalidated"
FINGERPRINT = "fingerprint"
PROFILE = "profile"


def _see_other(url: str) -> HttpResponse:
    response = redirect(url)
    response.status_code = 303
    return response


def _with_key(text: SafeString | str, key: str) -> SafeString:
    return format_html("{}" + KEY_TAIL, text, key)


def _unknown(exc: EngineClientError) -> bool:
    """D8's unknown outcome: no answer, or one that didn't parse (the write may have run)."""
    return isinstance(exc, EngineUnreachable) or (
        isinstance(exc, EngineError) and exc.code == BAD_RESPONSE
    )


# --- import ---------------------------------------------------------------------------------------


@dataclass
class ImportRefusal:
    """A refusal shown above the form: its text (with its link, if any) and the lines under it."""

    text: SafeString | str
    lines: list[str] = field(default_factory=list)


def error_line(entry: object) -> str:
    """One ``details.errors[]`` entry: "<loc, "."-joined>: <humanised type>"; an entry that isn't
    an object or misses either key is shown raw (compact JSON)."""
    if isinstance(entry, dict):
        loc, kind = entry.get("loc"), entry.get("type")
        if isinstance(loc, list | tuple):
            loc = ".".join(str(part) for part in loc)
        if isinstance(loc, str) and loc and isinstance(kind, str) and kind:
            return f"{loc}: {humanised(kind)}"
    return compact_json(entry)


def reason_text(exc: EngineError) -> str:
    """The record-level reason: ``details.reason``'s text when the Engine names one (``schema``
    → the Engine's message, an unknown reason humanised), else the message as is."""
    reason = exc.details.get("reason")
    if not isinstance(reason, str) or not reason:
        return exc.message
    if reason == SCHEMA_REASON:
        return exc.message
    return REASON_TEXT.get(reason, humanised(reason))


def invalid_record_refusal(exc: EngineError) -> ImportRefusal:
    listed = exc.details.get("errors")
    lines = [error_line(e) for e in listed] if isinstance(listed, list) else []
    return ImportRefusal(REFUSED_TEXT.format(reason=reason_text(exc)), lines)


def conflict_refusal(exc: EngineError, record: Mapping[str, Any]) -> ImportRefusal:
    found = exc.details.get("certification_id")
    cid = found if isinstance(found, str) and found else record.get("certification_id")
    if not isinstance(cid, str) or not cid:
        return ImportRefusal(CONFLICT_TEXT.format(id=EMPTY))
    url = certification_url(cid)
    shown = format_html('<a href="{}">{}</a>', url, engine_id(cid)) if url else engine_id(cid)
    before, _, after = CONFLICT_TEXT.partition("{id}")
    return ImportRefusal(format_html("{}{}{}", before, shown, after))


def place_validation_error(form: CertificationImportForm, exc: EngineError) -> bool:
    """E21: each ``body.<member>`` failure as a line on the textarea's error list; ``False``
    when one couldn't be placed (#43's panel then, logged)."""
    failures = engine_field_errors(exc)
    placed = True
    for failure in failures:
        if not failure.path.startswith("body."):
            placed = False
            continue
        member = loc_field(failure.path)
        form.add_error("record", f"{member}: {failure.message or exc.message}")
    return placed and bool(failures)


@dataclass
class ImportOutcome:
    refusal: ImportRefusal | None = None
    panel: ErrorPanel | None = None
    key: str | None = None  # kept on an unknown outcome


def import_outcome(
    request: HttpRequest,
    exc: EngineClientError,
    form: CertificationImportForm,
    record: Mapping[str, Any],
    key: str,
) -> ImportOutcome:
    log_engine_error(request, exc)
    if _unknown(exc) or not isinstance(exc, EngineError):
        return ImportOutcome(panel=panel_for(exc), key=key)
    if exc.code == INVALID_RECORD:
        return ImportOutcome(refusal=invalid_record_refusal(exc))
    if exc.code == RECORD_CONFLICT:
        return ImportOutcome(refusal=conflict_refusal(exc, record))
    if exc.code == VALIDATION_ERROR and place_validation_error(form, exc):
        return ImportOutcome()
    return ImportOutcome(panel=panel_for(exc))


def import_context(
    form: CertificationImportForm, outcome: ImportOutcome | None = None
) -> dict[str, Any]:
    outcome = outcome or ImportOutcome()
    return {
        "form": form,
        "idempotency_key": outcome.key or new_ulid(),
        "refusal": outcome.refusal,
        "engine_error": outcome.panel,
        "import_button": IMPORT_BUTTON,
        "certifications_url": certifications_url(),
        "certifications_link": CERTIFICATIONS_LINK,
    }


def imported_id(answer: CertificationImported, record: Mapping[str, Any]) -> str:
    """The flash's and the redirect's id: the answer's, else the record's."""
    return answer.certification_id or str(record.get("certification_id") or "")


def _import(ctx: EnginePageContext) -> dict[str, Any] | HttpResponse:
    request = ctx.request
    if request.method != "POST":
        return import_context(CertificationImportForm())
    form = CertificationImportForm(request.POST, request.FILES)
    if not form.is_valid():  # E21: a local-validation failure mints a fresh key; no call
        return import_context(form)
    record: dict[str, Any] = form.cleaned_data["record"]
    key = posted_idempotency_key(request)
    try:
        answer = ctx.client.import_certification(record, idempotency_key=key)
    except EngineClientError as exc:
        if is_held(exc):
            raise
        return import_context(form, import_outcome(request, exc, form, record, key))
    cid = imported_id(answer, record)
    shown_key = answer.idempotency_key or key
    if answer.stored:
        messages.success(request, _with_key(format_html(STORED_TEXT, engine_id(cid)), shown_key))
    else:
        messages.info(request, _with_key(ALREADY_STORED, shown_key))
    return _see_other(certification_url(cid) or certifications_url() or "/engine/")


@require_http_methods(["GET", "HEAD", "POST"])
def certification_import(request: HttpRequest) -> HttpResponse:
    """``engine:certification-import`` (module docstring)."""
    return engine_page(
        request,
        IMPORT_TEMPLATE,
        _import,
        current="engine:certifications",
        label=IMPORT_LABEL,
    )


# --- invalidate -----------------------------------------------------------------------------------


def not_valid_text(exc: EngineError) -> str:
    """``certification_not_valid``: already stale, another status humanised, or no status."""
    status = exc.details.get("status")
    if not isinstance(status, str) or not status:
        return NOT_VALID_UNKNOWN
    if status == "stale":
        return ALREADY_STALE
    return NOT_VALID_STATUS.format(status=humanised(status).lower())


def place_invalidate_errors(fields: InvalidateFields, exc: EngineError) -> bool:
    """E21: each ``body.reason`` / ``body.note`` failure on its field; ``False`` when any
    failure lies elsewhere (#43's panel then)."""
    failures = engine_field_errors(exc)
    if not failures or any(loc_field(f.path) not in fields.fields for f in failures):
        return False
    for failure in failures:
        fields.add_error(loc_field(failure.path), failure.message or exc.message)
    return True


def reason_label(reason: str | None) -> str:
    """The success flash's reason: the Engine's ``invalidated_reason`` (or the posted one) as
    ``INVALIDATED_REASON_TEXT`` words it, an unknown one humanised."""
    if not reason:
        return EMPTY
    return INVALIDATED_REASON_TEXT.get(reason, humanised(reason).lower())


def _shown(request: HttpRequest, name: str) -> str:
    """A display-only value: the hidden field on POST, the query string on GET."""
    posted = request.POST.get(name) if request.method == "POST" else None
    value = posted if posted is not None else request.GET.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def invalidate_page(cid: str, fingerprint: str, profile: str, cancel_url: str) -> ConfirmPage:
    return ConfirmPage(
        title=INVALIDATE_TITLE,
        question=INVALIDATE_QUESTION.format(id=short_id(cid)),
        confirm_label=INVALIDATE_LABEL,
        cancel_url=cancel_url,
        lines=INVALIDATE_LINES,
        facts=(
            ("Certification", cid),
            ("Fingerprint", fingerprint or EMPTY),
            ("Profile", profile or EMPTY),
        ),
        hidden={FINGERPRINT: fingerprint, PROFILE: profile},
        danger=True,
        fields_template=FIELDS_TEMPLATE,
    )


@require_http_methods(["GET", "HEAD", "POST"])
def certification_invalidate(request: HttpRequest, certification_id: str) -> HttpResponse:
    """``engine:certification-invalidate`` (module docstring)."""
    cid = certification_id.strip().upper()
    if not is_ulid(cid):
        context = {"engine_error": ErrorPanel(MALFORMED_TEXT)}
        return render(request, NOT_FOUND_PAGE, context, status=404)
    detail = certification_url(cid) or "/engine/"
    page = invalidate_page(
        cid, _shown(request, FINGERPRINT), _shown(request, PROFILE), safe_next(request, detail)
    )
    confirmed = request.method == "POST" and request.POST.get(CONFIRMED_FIELD) == "1"
    fields = InvalidateFields(request.POST) if confirmed else InvalidateFields()
    extra: dict[str, Any] = {"fields": fields}

    def perform(key: str) -> HttpResponse:
        if not fields.is_valid():  # no call; the same key, so the eventual confirm sends it
            return render_confirm(request, page, extra=extra, idempotency_key=key)
        reason = fields.cleaned_data["reason"]
        note = fields.cleaned_data["note"]
        try:
            answer = engine_client_for(request).invalidate_certification(
                cid, reason=reason, note=note, idempotency_key=key
            )
        except EngineClientError as exc:
            log_engine_error(request, exc)
            unknown = _unknown(exc)
            if isinstance(exc, EngineError) and exc.code == NOT_VALID:
                refused = dataclasses.replace(page, error=not_valid_text(exc))
                return render_confirm(request, refused, extra=extra)
            if isinstance(exc, EngineError) and exc.code == NOT_FOUND:
                refused = dataclasses.replace(page, error=NOT_FOUND_TEXT)
                return render_confirm(request, refused, extra=extra)
            if (
                isinstance(exc, EngineError)
                and exc.code == VALIDATION_ERROR
                and place_invalidate_errors(fields, exc)
            ):
                return render_confirm(request, page, extra=extra)
            return render_confirm(
                request,
                page,
                extra={**extra, "engine_error": panel_for(exc)},
                idempotency_key=key if unknown else None,
            )
        text = INVALIDATED_TEXT.format(reason=reason_label(answer.invalidated_reason or reason))
        messages.success(request, _with_key(text, answer.idempotency_key or key))
        return _see_other(detail)

    return confirm_action(request, page, perform, extra=extra, require_confirmed=True)
