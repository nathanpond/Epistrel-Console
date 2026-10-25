"""Archive, unarchive, delete and listing status of catalog characters and scenarios (#69).

One view per action, shared by both entry types: the URL carries ``entry`` (``character`` or
``scenario``) as an extra kwarg and :data:`KINDS` maps it to the client methods and URL names.

- ``…/<id>/archive/``, ``…/unarchive/``, ``…/delete/``: GET renders #43's confirm page from the
  query string alone (``name``, ``has_companion``, ``companion_of``, ``next``; **no** Engine
  call); the Confirm POST calls the Engine and redirects with a flash (archive and unarchive to
  ``next``, else the detail page; delete to the type's list). A 422
  ``companion_follows_character`` (a companion scenario, which follows its character) becomes
  an error flash naming the character (one card read of the posted ``companion_of``) and the
  scenario's detail page; any other error re-renders the confirm page under the panel, with a
  fresh idempotency key.
- ``…/<id>/listing-status/``: a plain POST from the detail page's select. An unchanged value
  makes no call; 422 ``invalid_listing_status`` flashes the allowed values; other errors render
  the panel on their own page.

The display data the pages post back (``name``, ``has_companion``, ``companion_of``) is only
shown, never trusted for the action, which acts on the URL's id.

:func:`lifecycle_for` builds what the detail and edit pages show: the controls
(``console/catalog/_lifecycle_controls.html``) and the archived banner
(``console/catalog/_archived_banner.html``).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods, require_POST

from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    render_confirm,
    safe_next,
)
from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine_access import engine_client_for
from console.engine_errors import log_engine_error, panel_for
from console.entities import label
from console.ids import new_ulid

CHARACTER = "character"
SCENARIO = "scenario"
ARCHIVED = "archived"
COMPANION_FOLLOWS = "companion_follows_character"
INVALID_LISTING_STATUS = "invalid_listing_status"
# The display data a confirm page reads from its query string and posts back.
NAME = "name"
HAS_COMPANION = "has_companion"
COMPANION_OF = "companion_of"
VALUE = "value"
CURRENT = "current_listing_status"
CHARACTER_ROOT = "/catalog/characters/"
COMPANION_FOLLOWS_TEXT = " — archive, delete or relabel the character instead"
FOLLOWS_SUFFIX = " (its companion follows)"
NO_CHANGE = "No change."
STAYS = "It stays readable but cannot be edited, published or used to create stories."
RETURNS = "It returns to published (or draft if it was never published)."
KEEP_COPIES = "Stories created from it keep their copies."
ERROR_TEMPLATE = "console/catalog/listing_status_error.html"


@dataclass(frozen=True)
class Kind:
    """One entry type: its URL namespace, the noun its pages use and its client methods."""

    entry: str
    namespace: str
    noun: str
    archive: str
    unarchive: str
    delete: str
    listing: str

    def url(self, action: str, entry_id: str) -> str:
        return reverse(f"{self.namespace}:{action}", args=[entry_id])


KINDS: dict[str, Kind] = {
    CHARACTER: Kind(
        CHARACTER,
        "catalog_characters",
        "character",
        "archive_catalog_character",
        "unarchive_catalog_character",
        "delete_catalog_character",
        "set_catalog_character_listing_status",
    ),
    SCENARIO: Kind(
        SCENARIO,
        "scenarios",
        "scenario",
        "archive_scenario",
        "unarchive_scenario",
        "delete_scenario",
        "set_scenario_listing_status",
    ),
}


# --- what the pages show --------------------------------------------------------------------------


@dataclass(frozen=True)
class Follows:
    """The character a companion scenario follows: its name (the id when the read failed) and
    its detail URL."""

    character_id: str
    text: str
    url: str


@dataclass(frozen=True)
class Lifecycle:
    """The lifecycle controls and the archived banner of one entry's page.

    ``follows`` is set on a companion: its controls are replaced by "Follows *Character*".
    ``listing_values`` is the select's options (``None`` with ``listing_error`` when
    ``/admin/settings`` could not be read, or when the page did not read it)."""

    entry: str
    entry_id: str
    name: str
    archived: bool
    listing_status: str
    archive_url: str
    unarchive_url: str
    delete_url: str
    listing_url: str
    next_url: str
    has_companion: bool = False
    follows: Follows | None = None
    listing_values: list[tuple[str, str]] | None = None
    listing_error: str = ""
    key: str = field(default_factory=new_ulid)


def follows_label(character_id: str, name: str | None = None) -> Follows:
    """How a page names the character a companion follows (the id when ``name`` is unknown)."""
    url = f"{CHARACTER_ROOT}{quote(character_id, safe='')}/"
    return Follows(character_id, name or character_id, url)


def _listing_options(values: list[str], current: str) -> list[tuple[str, str]]:
    """The deployment's values in their order, displayed raw; a current value missing from the
    list stays as "<value> (not in list)"."""
    options = [(value, value) for value in values]
    if current not in values:
        options.append((current, f"{current} (not in list)"))
    return options


def confirm_link(
    kind: Kind,
    action: str,
    entry_id: str,
    *,
    name: str | None,
    has_companion: bool,
    companion_of: str | None,
    next_url: str,
) -> str:
    """A confirm page's URL with the display data in its query string."""
    params = {NAME: name or ""}
    if has_companion:
        params[HAS_COMPANION] = "1"
    if companion_of:
        params[COMPANION_OF] = companion_of
    params["next"] = next_url
    return f"{kind.url(action, entry_id)}?{urlencode(params)}"


def lifecycle_for(
    request: HttpRequest,
    client: EngineClient | None,
    entry: str,
    entry_id: str,
    *,
    name: str | None,
    status: str,
    listing_status: str,
    has_companion: bool = False,
    follows: Follows | None = None,
) -> Lifecycle:
    """The controls and banner of one entry's page. With a ``client`` and not on a companion,
    ``/admin/settings`` is read once for the listing-status select; without one (the edit page)
    nothing is read. ``follows`` reuses the companion-of card read the page already made."""
    kind = KINDS[entry]
    next_url = request.get_full_path()
    companion_of = follows.character_id if follows is not None else None

    def link(action: str) -> str:
        return confirm_link(
            kind,
            action,
            entry_id,
            name=name,
            has_companion=has_companion,
            companion_of=companion_of,
            next_url=next_url,
        )

    values: list[tuple[str, str]] | None = None
    error = ""
    if client is not None and follows is None:
        try:
            settings = client.get_deployment_settings()
        except EngineClientError as exc:
            log_engine_error(request, exc)
            error = f"Listing values unavailable: {error_text(exc)}"
        else:
            values = _listing_options(settings.listing_status_values, listing_status)
    return Lifecycle(
        entry=entry,
        entry_id=entry_id,
        name=label(name, entry_id),
        archived=status == ARCHIVED,
        listing_status=listing_status,
        archive_url=link("archive"),
        unarchive_url=link("unarchive"),
        delete_url=link("delete"),
        listing_url=kind.url("listing-status", entry_id),
        next_url=next_url,
        has_companion=has_companion,
        follows=follows,
        listing_values=values,
        listing_error=error,
    )


# --- shared ---------------------------------------------------------------------------------------


def _source(request: HttpRequest) -> Any:
    return request.POST if request.method == "POST" else request.GET


def _shown(request: HttpRequest, entry_id: str) -> tuple[str, str]:
    """The posted (or queried) display ``name`` and how it is shown (unnamed → "(unnamed) …")."""
    name = _source(request).get(NAME, "")
    return name, label(name, entry_id)


def _has_companion(request: HttpRequest, kind: Kind) -> bool:
    return kind.entry == CHARACTER and _source(request).get(HAS_COMPANION) == "1"


def _hidden(request: HttpRequest, kind: Kind) -> dict[str, str]:
    """What the confirm form posts back: the display data of the query string."""
    source = _source(request)
    hidden = {NAME: source.get(NAME, "")}
    if _has_companion(request, kind):
        hidden[HAS_COMPANION] = "1"
    if source.get(COMPANION_OF):
        hidden[COMPANION_OF] = source.get(COMPANION_OF, "")
    return hidden


def companion_message(client: EngineClient, companion_of: str) -> SafeString:
    """The ``companion_follows_character`` flash: the character named by one card read of the
    posted ``companion_of`` (its id when the read fails) and linked; without one, unlinked."""
    if not companion_of:
        return format_html("This is a character's companion{}", COMPANION_FOLLOWS_TEXT)
    try:
        card = client.get_catalog_character_card(companion_of)
    except EngineClientError:
        follows = follows_label(companion_of)
    else:
        follows = follows_label(companion_of, card.name)
    return format_html(
        'This is <a href="{}">{}</a>\'s companion{}',
        follows.url,
        follows.text,
        COMPANION_FOLLOWS_TEXT,
    )


def _follows_refusal(
    request: HttpRequest, client: EngineClient, kind: Kind, entry_id: str
) -> HttpResponse:
    messages.error(request, companion_message(client, request.POST.get(COMPANION_OF, "")))
    return redirect(kind.url("detail", entry_id))


def _is_follows(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.code == COMPANION_FOLLOWS


# --- archive, unarchive, delete -------------------------------------------------------------------


def _confirmed(
    request: HttpRequest,
    kind: Kind,
    entry_id: str,
    page: ConfirmPage,
    call: Callable[[EngineClient, str], Any],
    done: Callable[[Any], HttpResponse],
) -> HttpResponse:
    """GET: the confirm page. POST: ``call(client, key)``, then ``done(result)``; a refusal is
    the companion flash or the page again under the panel."""

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        try:
            result = call(client, key)
        except EngineClientError as exc:
            log_engine_error(request, exc)
            if _is_follows(exc):
                return _follows_refusal(request, client, kind, entry_id)
            return render_confirm(request, page, extra={"engine_error": panel_for(exc)})
        return done(result)

    return confirm_action(request, page, perform)


def _status_change(request: HttpRequest, entry_id: str, entry: str, archive: bool) -> HttpResponse:
    kind = KINDS[entry]
    _, shown = _shown(request, entry_id)
    companion = _has_companion(request, kind)
    back = safe_next(request, kind.url("detail", entry_id))
    verb = "Archive" if archive else "Unarchive"
    question = f"{verb} {{name}}? {STAYS if archive else RETURNS}"
    if companion:
        question += (
            " Its companion is archived with it."
            if archive
            else " Its companion is restored with it."
        )
    page = ConfirmPage(
        title=f"{verb} {kind.noun}",
        question=question,
        emphasis=shown,
        confirm_label=verb,
        cancel_url=back,
        facts=((f"{kind.noun.capitalize()} id", entry_id),),
        hidden=_hidden(request, kind),
    )
    method = kind.archive if archive else kind.unarchive

    def call(client: EngineClient, key: str) -> Any:
        return getattr(client, method)(entry_id, idempotency_key=key)

    def done(result: Any) -> HttpResponse:
        follows = FOLLOWS_SUFFIX if getattr(result, "companion_id", None) else ""
        if archive:
            messages.success(request, f"Archived {shown}{follows}.")
        else:
            messages.success(request, f"Unarchived {shown} (now {result.status}){follows}.")
        return redirect(back)

    return _confirmed(request, kind, entry_id, page, call, done)


@require_http_methods(["GET", "HEAD", "POST"])
def archive(request: HttpRequest, entry_id: str, entry: str) -> HttpResponse:
    return _status_change(request, entry_id, entry, archive=True)


@require_http_methods(["GET", "HEAD", "POST"])
def unarchive(request: HttpRequest, entry_id: str, entry: str) -> HttpResponse:
    return _status_change(request, entry_id, entry, archive=False)


@require_http_methods(["GET", "HEAD", "POST"])
def delete(request: HttpRequest, entry_id: str, entry: str) -> HttpResponse:
    kind = KINDS[entry]
    _, shown = _shown(request, entry_id)
    companion = _has_companion(request, kind)
    if companion:
        question = f"Permanently delete {{name}}, all its versions and its companion? {KEEP_COPIES}"
    else:
        question = f"Permanently delete {{name}} and all its versions? {KEEP_COPIES}"
    page = ConfirmPage(
        title=f"Delete {kind.noun}",
        question=question,
        emphasis=shown,
        confirm_label="Delete",
        cancel_url=safe_next(request, kind.url("detail", entry_id)),
        facts=((f"{kind.noun.capitalize()} id", entry_id),),
        hidden=_hidden(request, kind),
        danger=True,
    )

    def call(client: EngineClient, key: str) -> None:
        getattr(client, kind.delete)(entry_id, idempotency_key=key)

    def done(_: object) -> HttpResponse:
        also = " and its companion" if companion else ""
        messages.success(request, f"Deleted {shown}{also}.")
        return redirect(f"{kind.namespace}:index")  # the entry's own page is gone

    return _confirmed(request, kind, entry_id, page, call, done)


# --- listing status -------------------------------------------------------------------------------


def _trimmed(value: str) -> str:
    """Surrounding spaces and tabs dropped, as the Engine trims them."""
    return value.strip(" \t")


@require_POST
def listing_status(request: HttpRequest, entry_id: str, entry: str) -> HttpResponse:
    """The detail page's select: ``PUT …/listing-status`` unless the value is unchanged."""
    kind = KINDS[entry]
    _, shown = _shown(request, entry_id)
    detail = kind.url("detail", entry_id)
    back = safe_next(request, detail)
    value = _trimmed(request.POST.get(VALUE, ""))
    if value == _trimmed(request.POST.get(CURRENT, "")):
        messages.info(request, NO_CHANGE)
        return redirect(back)
    client = engine_client_for(request)
    try:
        result = getattr(client, kind.listing)(
            entry_id, value, idempotency_key=posted_idempotency_key(request)
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if _is_follows(exc):
            return _follows_refusal(request, client, kind, entry_id)
        if isinstance(exc, EngineError) and exc.code == INVALID_LISTING_STATUS:
            refused = exc.details.get("value") or value
            allowed = exc.details.get("allowed_values") or []
            messages.error(
                request,
                f"{refused} is not a listing status here. "
                f"Allowed: {', '.join(str(a) for a in allowed)}.",
            )
            return redirect(back)
        context = {"engine_error": panel_for(exc), "name": shown, "detail_url": back}
        return render(request, ERROR_TEMPLATE, context)
    follows = FOLLOWS_SUFFIX if _has_companion(request, kind) else ""
    messages.success(request, f"Listing status set to {result.listing_status}{follows}.")
    return redirect(back)
