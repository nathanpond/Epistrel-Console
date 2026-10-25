"""Principals (#76): look up everything linked to a principal (``GET /principals/{principal}``).

- ``/principals/``: the lookup form (a GET, ``?principal=``; ``PrincipalField(allow_system=True)``,
  pre-filled with the acting principal) and the known principals (#64), each linking to its
  page. A valid lookup is remembered (``system`` never is: the list feeds write forms, which
  refuse it) and redirects to the principal's page; an invalid one shows the field error with
  zero calls. A blank or absent ``principal`` is no lookup.
- ``/principals/<principal>/``: the principal's **Owned** and **Related** groups, one table per
  type in :data:`ENGINE_LOOKUP_KEYS` order, each paging on its own cursor (``cursor[stories]``,
  ``cursor[related.media]``) while the others keep theirs. One lookup call per render; a stale
  cursor (422 ``cursor_invalid``) is retried once with every cursor dropped, with a notice.
  Opening the page directly remembers nothing.

Links to a principal's page are built by :func:`principal_url`: ``reverse`` of
``quote(p, safe='')``, which ``reverse`` percent-encodes once more, so Django's own decoding
leaves the quoted form and the view unquotes it (a principal may hold ``/`` or ``%``). The
``principal_link`` filter (``console.templatetags.principals``) uses it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlencode

from django import forms
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import F
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_safe

from console.acting import acting_principal
from console.engine.client import RESERVED_PRINCIPAL, EngineClientError, EngineError, principal_for
from console.engine.models import LOOKUP_TYPES, OwnedItem, OwnedPage, PrincipalLinks, RelatedPage
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import entity_link, entity_url, label
from console.forms.principal import PrincipalField, clean_principal, remember_principal
from console.models import KnownPrincipal

DETAIL_URL_NAME = "principals:detail"
# The Engine's plural lookup keys and the registry type each names, in the page's table order.
ENGINE_LOOKUP_KEYS = {
    "stories": "story",
    "characters": "character",
    "scenarios": "scenario",
    "personas": "persona",
    "media": "media",
    "engine_profiles": "engine_profile",
}
TITLES = {
    "stories": "Stories",
    "characters": "Characters",
    "scenarios": "Scenarios",
    "personas": "Personas",
    "media": "Media",
    "engine_profiles": "Engine profiles",
}
# The owned columns each type has besides its name (a column a type never has is left out).
OWNED_COLUMNS: dict[str, tuple[str, ...]] = {
    "stories": ("status",),
    "characters": ("status", "version", "listing_status"),
    "scenarios": ("status", "version", "listing_status"),
    "personas": (),
    "media": ("status",),
    "engine_profiles": ("status",),
}
COLUMN_TITLES = {"status": "Status", "version": "Version", "listing_status": "Listing status"}
RELATED = "related"
OWNED = "owned"
# Every cursor the lookup takes, by the Engine's name without ``cursor[...]``.
CURSOR_KEYS: tuple[str, ...] = (*LOOKUP_TYPES, *(f"{RELATED}.{t}" for t in LOOKUP_TYPES))
CURSOR_MAX = 256
KNOWN_PAGE = 50
HELD = "(held — erasure pending)"
STALE = "A page link was stale — showing first pages."
CURSOR_INVALID = "cursor_invalid"


def principal_url(principal: str) -> str:
    """The principal's page, ``/principals/<quote(p, safe='')>/`` (as ``reverse`` encodes it).
    Raises ``NoReverseMatch`` while the route does not exist."""
    return reverse(DETAIL_URL_NAME, args=[quote(principal, safe="")])


def cursor_param(key: str) -> str:
    """``cursor[<key>]``, the query parameter the Engine (and this page) names a cursor by."""
    return f"cursor[{key}]"


def table_anchor(key: str) -> str:
    """The table's anchor: ``owned-stories`` for ``stories``, ``related-media`` for
    ``related.media``."""
    side, _, kind = key.rpartition(".")
    return f"{side or OWNED}-{kind}"


def cursor_query(current: Mapping[str, str], set_key: str, value: str | None) -> str:
    """The query string (with ``?``) of a paging link: every cursor in ``current`` (known keys
    only, in :data:`CURSOR_KEYS` order) with ``set_key`` set to ``value`` (``None`` removes it),
    then ``#<table-anchor>``."""
    cursors = {k: v for k, v in current.items() if k in CURSOR_KEYS and v}
    if value is None:
        cursors.pop(set_key, None)
    else:
        cursors[set_key] = value
    pairs = [(cursor_param(k), cursors[k]) for k in CURSOR_KEYS if k in cursors]
    return f"?{urlencode(pairs)}#{table_anchor(set_key)}"


def incoming_cursors(request: HttpRequest) -> tuple[dict[str, str], bool]:
    """The page's cursors from its query (the twelve known names only; empty ones omitted) and
    whether one was dropped for being over :data:`CURSOR_MAX` characters (it starts at page 1,
    and the stale notice shows)."""
    cursors: dict[str, str] = {}
    dropped = False
    for key in CURSOR_KEYS:
        value = request.GET.get(cursor_param(key), "")
        if not value:
            continue
        if len(value) > CURSOR_MAX:
            dropped = True
            continue
        cursors[key] = value
    return cursors, dropped


class LookupForm(forms.Form):
    principal = PrincipalField(allow_system=True, label="Principal")


# --- /principals/ ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class KnownRow:
    principal: str
    url: str
    last_used_at: Any
    own: bool = False


def _known_rows(request: HttpRequest) -> tuple[list[KnownRow], Any]:
    """The known principals' page: the operator's own ``console:<username>`` pinned first on
    page 1 only (with or without a row), then ``last_used_at`` descending (nulls last), then
    by principal (case-sensitive), :data:`KNOWN_PAGE` per page."""
    own = principal_for(request.user)
    rows = KnownPrincipal.objects.exclude(principal=own).order_by(
        F("last_used_at").desc(nulls_last=True), "principal"
    )
    page = Paginator(rows, KNOWN_PAGE).get_page(request.GET.get("page"))
    known = [KnownRow(r.principal, principal_url(r.principal), r.last_used_at) for r in page]
    if page.number == 1:
        mine = KnownPrincipal.objects.filter(principal=own).first()
        last = mine.last_used_at if mine is not None else None
        known.insert(0, KnownRow(own, principal_url(own), last, own=True))
    return known, page


def _render_index(request: HttpRequest, form: LookupForm) -> HttpResponse:
    rows, page = _known_rows(request)
    context = {"form": form, "known": rows, "page": page}
    return render(request, "console/principals/index.html", context)


@require_safe
def principal_index(request: HttpRequest) -> HttpResponse:
    if not request.GET.get("principal", "").strip(" \t"):
        return _render_index(request, LookupForm(initial={"principal": acting_principal(request)}))
    form = LookupForm(request.GET)
    if not form.is_valid():
        return _render_index(request, form)
    principal: str = form.cleaned_data["principal"]
    if principal != RESERVED_PRINCIPAL:
        remember_principal(principal)
    return redirect(principal_url(principal))


# --- /principals/<principal>/ ---------------------------------------------------------------------


@dataclass(frozen=True)
class OwnedRow:
    item: OwnedItem
    name: SafeString
    cells: list[str]
    companion: SafeString | None
    held: bool


@dataclass(frozen=True)
class RelatedRow:
    id: SafeString
    relations: str


@dataclass(frozen=True)
class Table:
    key: str  # the cursor key: ``stories`` or ``related.stories``
    kind: str  # the Engine's plural type key
    title: str
    anchor: str
    returned: bool
    count: int
    columns: list[str]
    owned_rows: list[OwnedRow]
    related_rows: list[RelatedRow]
    next_query: str | None
    first_query: str | None


@dataclass(frozen=True)
class Group:
    title: str
    side: str
    total: int
    tables: list[Table]


def _cell(item: OwnedItem, column: str) -> str:
    value = getattr(item, column)
    return "—" if value is None or value == "" else str(value)


def _owned_row(kind: str, item: OwnedItem) -> OwnedRow:
    columns = OWNED_COLUMNS[kind]
    cells = [_cell(item, c) for c in columns]
    companion = entity_link("scenario", item.companion.id) if item.companion else None
    if item.name is None:
        name = format_html("{} <code>{}</code>", HELD, item.id)
        return OwnedRow(item, name, cells, companion, held=True)
    shown = label(item.name, item.id)
    url = entity_url(ENGINE_LOOKUP_KEYS[kind], item.id)
    if url is None:
        name = format_html("{} <code>{}</code>", shown, item.id)
    else:
        name = format_html('<a href="{}">{}</a>', url, shown)
    return OwnedRow(item, name, cells, companion, held=False)


def _table(
    side: str, kind: str, page: OwnedPage | RelatedPage | None, cursors: Mapping[str, str]
) -> Table:
    key = kind if side == OWNED else f"{RELATED}.{kind}"
    owned_rows: list[OwnedRow] = []
    related_rows: list[RelatedRow] = []
    if isinstance(page, OwnedPage):
        owned_rows = [_owned_row(kind, item) for item in page.items]
    elif isinstance(page, RelatedPage):
        related_rows = [
            RelatedRow(entity_link(ENGINE_LOOKUP_KEYS[kind], item.id), ", ".join(item.relations))
            for item in page.items
        ]
    next_cursor = page.next_cursor if page is not None else None
    return Table(
        key=key,
        kind=kind,
        title=TITLES[kind],
        anchor=table_anchor(key),
        returned=page is not None,
        count=page.count if page is not None else 0,
        columns=[COLUMN_TITLES[c] for c in OWNED_COLUMNS[kind]] if side == OWNED else [],
        owned_rows=owned_rows,
        related_rows=related_rows,
        next_query=cursor_query(cursors, key, next_cursor) if next_cursor else None,
        first_query=cursor_query(cursors, key, None) if cursors.get(key) else None,
    )


def _groups(links: PrincipalLinks, cursors: Mapping[str, str]) -> list[Group]:
    groups = []
    for side, title, pages in (
        (OWNED, "Owned", links.owned),
        (RELATED, "Related", links.related),
    ):
        tables = [_table(side, kind, pages.get(kind), cursors) for kind in ENGINE_LOOKUP_KEYS]
        groups.append(Group(title, side, sum(t.count for t in tables), tables))
    return groups


def _optional_url(name: str) -> str | None:
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


@require_safe
def principal_detail(request: HttpRequest, principal: str) -> HttpResponse:
    raw = unquote(principal)
    try:
        cleaned = clean_principal(raw, allow_system=True)
    except ValidationError:
        form = LookupForm({"principal": raw})
        form.is_valid()  # the field's error text, with zero Engine calls
        return render(request, "console/principals/detail.html", {"form": form, "invalid": True})
    if cleaned != raw:
        query = request.META.get("QUERY_STRING", "")
        target = principal_url(cleaned) + (f"?{query}" if query else "")
        return HttpResponsePermanentRedirect(target)

    cursors, stale = incoming_cursors(request)
    client = engine_client_for(request)
    links: PrincipalLinks | None = None
    engine_error: ErrorPanel | None = None
    try:
        try:
            links = client.lookup_principal(cleaned, cursors)
        except EngineError as exc:
            if exc.code != CURSOR_INVALID or not cursors:
                raise
            log_engine_error(request, exc)
            cursors, stale = {}, True
            links = client.lookup_principal(cleaned, cursors)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        engine_error = panel_for(exc)

    shown = links.principal if links is not None else cleaned
    groups = _groups(links, cursors) if links is not None else []
    tables = [t for g in groups for t in g.tables]
    nothing = bool(tables) and all(t.returned and t.count == 0 for t in tables)
    is_system = shown == RESERVED_PRINCIPAL
    context = {
        "form": LookupForm(initial={"principal": shown}),
        "principal": shown,
        "links": links,
        "groups": groups,
        "nothing": nothing,
        "stale": stale and links is not None,
        "stale_text": STALE,
        "engine_error": engine_error,
        "can_act": not is_system and shown != acting_principal(request),
        "page_url": principal_url(shown),
        "offboard_url": None if is_system else _offboard_url(shown),
        "offboarding_url": _optional_url("offboarding:index"),
    }
    return render(request, "console/principals/detail.html", context)


def _offboard_url(principal: str) -> str | None:
    """``/offboarding/new/?principal=<p>`` (#77), or ``None`` while that page does not exist."""
    url = _optional_url("offboarding:new")
    return f"{url}?{urlencode({'principal': principal})}" if url else None
