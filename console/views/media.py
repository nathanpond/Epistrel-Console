"""The media library (#70): upload, browse, view and retire images and videos.

- ``/media/``: ``GET /media`` with the GET filters (kind, owner, one relation; an invalid filter
  makes no call), cursor-paged. "Show retired" (``?retired=1``) lists only retired assets
  (``state=retired``). One content-rating read names the ratings (the raw id when it fails).
- ``/media/upload/``: the upload form (:class:`console.forms.media.MediaUploadForm`). Each render
  reads the ratings, the subject picker's two sources and the poster datalist (the first 50 live
  images); a refused form makes no ``POST /media``. The upload is streamed from Django's own
  upload copy (memory, or a temporary file Django deletes at the end of the request): the
  Console keeps nothing. An Engine refusal is shown at the top of the form (or at its field), and
  the operator must choose the file again.
- ``/media/<id>/``: the asset's metadata and the image or ``<video>`` through the proxy. Subject
  names are read per subject on the fan-out pool (the id when a read fails).
- ``/media/<id>/retire/``: the shared confirm page (no call), then ``DELETE /media/{id}``.

Every byte is shown through the proxy (``/media/<id>/content/``, #65). Uploads and retires are
recorded (#44) with entity ``media``; an upload's row stores the metadata and each file's name,
size and type, never the bytes.
"""

from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.acting import acting_principal
from console.confirm import ConfirmPage, confirm_action, posted_idempotency_key
from console.engine.choices import (
    MediaLookup,
    RatingChoices,
    error_text,
    media_lookups,
    rating_choices,
    subject_choices,
)
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import MediaAsset, SubjectRef
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import engine_field_errors
from console.entities import entity_url, label
from console.forms.media import MediaFilterForm, MediaUploadForm
from console.forms.principal import remember_principals
from console.ids import new_ulid
from console.templatetags.console_format import human_bytes, mib

LIST_URL = "/media/"
UPLOAD_TEMPLATE = "console/media/upload.html"
POSTER_OPTIONS = 50
CHOOSE_AGAIN = "Choose the file again — browsers don't keep it"
RETIRE_LINES = (
    "It leaves the library and can't be newly attached; anything already using it keeps showing "
    "it until the sweep purges it. A video's poster is retired too.",
)
NOT_PUBLISHED = "not_published"
# Where a 413 names its limit, in order (the Engine's ``use_multipart`` says ``threshold``).
LIMIT_KEYS = ("limit", "media_max_bytes", "media_multipart_threshold", "threshold")
# ``validation_error`` paths placed at their fields (#70); the rest are non-field errors.
FIELD_PATHS = {
    "metadata.alt_text": "alt_text",
    "metadata.caption": "caption",
    "metadata.content_rating_id": "content_rating",
    "metadata.poster": "poster",
    "metadata.owner": "owner",
}
SUBJECTS_PATH = "metadata.depicted_subjects"
POSTER_PART = "poster_file"
# Refusals placed beside a field: code -> (field, text).
FIELD_REFUSALS = {
    "poster_invalid": ("poster", "Not a live image asset"),
    "media_retired": ("poster", "This asset is retired"),
    "rating_retired": ("content_rating", "This rating is retired — choose another"),
}
# Refusals shown at the top: code -> text (``{kind}`` is the file's kind).
TOP_REFUSALS = {
    "media_type_not_allowed": "The file's type doesn't match {kind} or isn't allowed by the Engine",
    "poster_required": "The Engine can't extract a poster (no ffmpeg); add a poster file or id",
    "poster_extraction_failed": "Poster extraction failed; add a poster",
    "moderation_unavailable": "The moderation hook did not answer; nothing was stored",
    "media_store_unavailable": "Media store unavailable; nothing was stored",
}
TOO_LARGE = {
    "media_too_large": ("This file is larger than the Engine accepts", "."),
    "use_multipart": (
        "This file is above the Engine's single-upload size",
        "; resumable uploads are not supported by the Console.",
    ),
}


def _detail_url(media_id: str) -> str:
    return reverse("media:detail", args=[media_id])


def content_url(media_id: str, *, image: bool = False) -> str:
    """The proxy URL of an asset's bytes (``?img=1`` for an ``<img>``: an error answers an SVG)."""
    url = reverse("media:content", args=[media_id])
    return f"{url}?img=1" if image else url


def _rating_names(ratings: RatingChoices) -> dict[str, str]:
    return dict(ratings.choices)


@dataclass(frozen=True)
class ShownSubject:
    """A depicted subject as a page shows it: ``kind``, the id's last 6 characters, its name
    (the id when unknown or unread), its Console page (``None`` for a story character) and why
    the name could not be read."""

    kind: str
    id: str
    name: str = ""
    url: str | None = None
    error: str | None = None

    @property
    def short(self) -> str:
        return f"…{self.id[-6:]}"


# --- list -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ListRow:
    asset: MediaAsset
    url: str
    thumbnail: str | None  # the proxied image (or a video's poster); ``None``: "[video]"
    rating: str
    subjects: list[ShownSubject] = field(default_factory=list)


def _thumbnail(asset: MediaAsset) -> str | None:
    if asset.kind == "image":
        return content_url(asset.id, image=True)
    if asset.poster_media_id:
        return content_url(asset.poster_media_id, image=True)
    return None


@require_safe
def media_list(request: HttpRequest) -> HttpResponse:
    form = MediaFilterForm(request.GET)
    rows: list[ListRow] = []
    next_query = ""
    engine_error: ErrorPanel | None = None
    if form.is_valid():
        client = engine_client_for(request)
        try:
            items, next_cursor = client.list_media(**form.query())
        except EngineClientError as exc:
            log_engine_error(request, exc)
            engine_error = panel_for(exc)
        else:
            names = _rating_names(rating_choices(client, include_retired=True)) if items else {}
            rows = [
                ListRow(
                    asset,
                    _detail_url(asset.id),
                    _thumbnail(asset),
                    names.get(asset.content_rating_id or "", asset.content_rating_id or "—"),
                    [ShownSubject(s.kind, s.id) for s in asset.depicted_subjects],
                )
                for asset in items
            ]
            if next_cursor:
                params = request.GET.copy()
                params["cursor"] = next_cursor
                next_query = params.urlencode()
    first = request.GET.copy()
    first.pop("cursor", None)
    context = {
        "form": form,
        "rows": rows,
        "engine_error": engine_error,
        "next_query": next_query,
        "first_query": first.urlencode(),
        "paged": bool(request.GET.get("cursor")),
        "any_filter": form.any_filter,
        "retired": form.retired_only,
    }
    return render(request, "console/media/list.html", context)


# --- detail ---------------------------------------------------------------------------------------


def _subject_name(client: EngineClient, subject: SubjectRef) -> str:
    """A catalog character's card name (a draft-only one: its draft card's) or a persona's."""
    if subject.kind == "persona":
        return client.get_persona(subject.id).name
    try:
        card = client.get_catalog_character_card(subject.id)
    except EngineError as exc:
        if exc.code != NOT_PUBLISHED:
            raise
        card = client.get_catalog_character_card(subject.id, draft=True)
    return label(card.name, subject.id)


def shown_subjects(client: EngineClient, subjects: list[SubjectRef]) -> list[ShownSubject]:
    """Each subject with its name and page: catalog characters and personas read on the fan-out
    pool (the id on a failed read); a story character is shown by its id."""
    looked_up = [s for s in subjects if s.kind != "character"]
    names = fan_out(lambda s: _subject_name(client, s), looked_up)
    shown: list[ShownSubject] = []
    for subject in subjects:
        entity = {"catalog_character": "character", "persona": "persona"}.get(subject.kind)
        url = entity_url(entity, subject.id) if entity else None
        result = names.get(subject)
        if isinstance(result, EngineClientError):
            shown.append(
                ShownSubject(subject.kind, subject.id, subject.id, url, error_text(result))
            )
        elif isinstance(result, str):
            shown.append(ShownSubject(subject.kind, subject.id, result, url))
        else:
            shown.append(ShownSubject(subject.kind, subject.id, subject.id, url))
    return shown


def duration(ms: int | None) -> str:
    """``m:ss.s`` (``1:05.3``), or "—"."""
    if ms is None:
        return "—"
    minutes, rest = divmod(ms, 60_000)
    return f"{minutes}:{rest / 1000:04.1f}"


@require_safe
def media_detail(request: HttpRequest, media_id: str) -> HttpResponse:
    """The asset (an Engine error escapes to the shared panel, #43)."""
    client = engine_client_for(request)
    asset = client.get_media(media_id)
    names = _rating_names(rating_choices(client, include_retired=True))
    poster: MediaLookup | None = None
    if asset.poster_media_id:
        poster = media_lookups(client, [asset.poster_media_id])[asset.poster_media_id]
    retire_query = f"?kind={asset.kind}"
    context: dict[str, Any] = {
        "asset": asset,
        "content_url": content_url(asset.id),
        "image_url": content_url(asset.id, image=True),
        "poster_url": content_url(asset.poster_media_id, image=True)
        if asset.poster_media_id
        else None,
        "poster": poster,
        "rating": names.get(asset.content_rating_id or "", asset.content_rating_id or "—"),
        "subjects": shown_subjects(client, asset.depicted_subjects),
        "size": human_bytes(asset.size),
        "duration": duration(asset.duration_ms),
        "retired": asset.state == "retired",
        "retire_url": f"{reverse('media:retire', args=[asset.id])}{retire_query}",
    }
    return render(request, "console/media/detail.html", context)


# --- upload ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Limit:
    text: str  # humanised (MiB, one decimal)
    raw: int  # bytes, shown in ``title``


@dataclass(frozen=True)
class Refusal:
    """An Engine refusal shown at the top of the upload form: ``before`` + `` (<limit>)`` +
    ``after``, then the Engine's code, then ``details`` (a definition list) when set."""

    before: str
    code: str
    limit: Limit | None = None
    after: str = ""
    details: tuple[tuple[str, str], ...] = ()


def _limit(details: dict[str, Any]) -> Limit | None:
    for key in LIMIT_KEYS:
        value = details.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return Limit(mib(value), value)
    return None


def _about_poster(details: dict[str, Any]) -> bool:
    """Whether any string in ``details`` names the ``poster_file`` part."""

    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in strings(v)]
        return []

    return any(POSTER_PART in s for s in strings(details))


def _place_validation(exc: EngineError, form: MediaUploadForm) -> None:
    """A 422 ``validation_error``'s paths at their fields (subjects at the typed list when it
    has lines), else as non-field errors."""
    failures = engine_field_errors(exc)
    if not failures:
        form.add_error(None, exc.message)
    typed = bool((form.data.get("subjects_typed") or "").strip())
    for failure in failures:
        path = failure.path.removeprefix("body.")
        message = failure.message or exc.message
        target = FIELD_PATHS.get(path)
        if path.startswith(SUBJECTS_PATH) and typed:
            target = "subjects_typed"
        if target and target in form.fields:
            form.add_error(target, message)
        else:
            form.add_error(None, f"{path}: {message}")


def refusal_for(
    exc: EngineClientError, form: MediaUploadForm
) -> tuple[list[Refusal], ErrorPanel | None]:
    """How an upload refusal shows: at the top (a :class:`Refusal`), at its field (added to
    ``form``), or, for any other error, as the shared panel."""
    if not isinstance(exc, EngineError):
        return [], panel_for(exc)
    code, details = exc.code, exc.details
    if code in TOO_LARGE:
        before, after = TOO_LARGE[code]
        return [Refusal(before, code, _limit(details), after)], None
    if code == "media_type_not_allowed":
        poster = _about_poster(details)
        kind = "image" if poster else form.cleaned_data.get("kind", "the kind")
        text = TOP_REFUSALS[code].format(kind=kind)
        return [Refusal(f"Poster: {text}" if poster else text, code)], None
    if code == "moderation_rejected":
        reason = details.get("reason")
        shown = reason if isinstance(reason, str) and reason else exc.message
        return [Refusal(f"Rejected by the moderation hook: {shown}", code)], None
    if code == "erased_subject_referenced":
        rows = tuple((str(k), str(v)) for k, v in details.items())
        return [Refusal(f"A subject has been erased: {exc.message}", code, details=rows)], None
    if code in TOP_REFUSALS:
        return [Refusal(TOP_REFUSALS[code], code)], None
    if code in FIELD_REFUSALS:
        name, text = FIELD_REFUSALS[code]
        if name in form.fields:
            form.add_error(name, text)
            return [], None
        return [Refusal(text, code)], None
    if code == "validation_error" and exc.http_status == 422:
        _place_validation(exc, form)
        return [], None
    return [], panel_for(exc)


def _poster_options(request: HttpRequest, client: EngineClient) -> list[tuple[str, str]] | None:
    """The poster id's datalist: the first 50 live images, ``(id, alt text)``; ``None`` when the
    read failed (the input works without it)."""
    try:
        items, _ = client.list_media(kind="image", limit=POSTER_OPTIONS)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None
    return [(a.id, a.alt_text or "(no alt text)") for a in items]


def _render_upload(
    request: HttpRequest,
    form: MediaUploadForm,
    *,
    refusals: list[Refusal] | None = None,
    engine_error: ErrorPanel | None = None,
    refused: bool = False,
) -> HttpResponse:
    context = {
        "form": form,
        "idempotency_key": new_ulid(),  # minted at each render (#43)
        "refusals": refusals or [],
        "engine_error": engine_error,
        "choose_again": CHOOSE_AGAIN if refused else "",
        "poster_datalist": form.poster_options,
    }
    return render(request, UPLOAD_TEMPLATE, context)


@require_http_methods(["GET", "HEAD", "POST"])
def media_upload(request: HttpRequest) -> HttpResponse:
    client = engine_client_for(request)
    lookups: dict[str, Any] = {
        "ratings": rating_choices(client),
        "subjects": subject_choices(client),
        "poster_options": _poster_options(request, client),
    }
    if request.method != "POST":
        initial = {"owner": acting_principal(request)}
        if request.GET.get("kind") in ("image", "video"):
            initial["kind"] = request.GET["kind"]
        return _render_upload(request, MediaUploadForm(initial=initial, **lookups))
    form = MediaUploadForm(request.POST, request.FILES, **lookups)
    if not form.is_valid():
        return _render_upload(request, form)
    remember_principals(form)
    key = posted_idempotency_key(request)
    try:
        uploaded = client.upload_media(
            form.metadata(), form.file_part(), form.poster_part(), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        refusals, panel = refusal_for(exc, form)
        return _render_upload(request, form, refusals=refusals, engine_error=panel, refused=True)
    messages.success(
        request,
        f"Uploaded {uploaded.kind} {uploaded.id} — key {uploaded.idempotency_key or key}",
    )
    return redirect(_detail_url(uploaded.id))


# --- retire ---------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def media_retire(request: HttpRequest, media_id: str) -> HttpResponse:
    """GET: the confirm page with the id, the kind (from the query string, display only) and the
    thumbnail through the proxy; no Engine call. POST: ``DELETE /media/{id}`` (a retired asset's
    is a 204 no-op, flashed the same), then the library."""
    source = request.POST if request.method == "POST" else request.GET
    kind = source.get("kind", "")
    kind = kind if kind in ("image", "video") else ""
    facts = [("Media id", media_id)]
    if kind:
        facts.append(("Kind", kind))
    page = ConfirmPage(
        title="Retire media",
        question=f"Retire this {kind or 'asset'}?",
        lines=RETIRE_LINES,
        confirm_label="Retire",
        cancel_url=_detail_url(media_id),
        facts=tuple(facts),
        hidden={"kind": kind},
        danger=True,
        fields_template="console/media/_retire_preview.html",
    )
    extra = {
        "media_kind": kind,
        "preview_url": content_url(media_id, image=kind != "video"),
    }

    def perform(key: str) -> HttpResponse:
        engine_client_for(request).retire_media(media_id, idempotency_key=key)
        messages.success(request, f"Retired {media_id} — key {key}")
        return redirect(LIST_URL)

    return confirm_action(request, page, perform, extra=extra)
