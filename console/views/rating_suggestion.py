"""Suggest ratings for a catalog character or scenario (#82).

The detail pages (full view only) carry a "Rating suggestion" section: a version select (Draft,
then the published versions newest first), "Include retired ratings" and a button that POSTs to
``/catalog/{characters|scenarios}/<id>/rating-suggestion/``. The view checks the version (else
400 "Choose a version", no Engine call), reads the content ratings and the policies at once on
#41's pool (labels, retired marks, policy names, and the rating count the timeout is computed
from), then asks the Engine (:meth:`~console.engine.client.EngineClient.suggest_ratings`). The
answer is an advisory table: it writes nothing and nothing is kept on the page; the operation
log row (#44) stores it. Under HTMX the result is swapped into ``#rating-suggestion``; without
JS a page shows it with a link back to the entry.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.views.decorators.http import require_http_methods

from console.engine.client import (
    EngineClient,
    EngineClientError,
    EngineError,
    EngineUnreachable,
    SuggestionEntity,
)
from console.engine.models import (
    Concern,
    ContentRatingSummary,
    EntryLocation,
    Policy,
    RatingSuggestion,
    SuggestionModelCall,
    SuggestionRow,
    SuggestionVersion,
)
from console.engine.pool import fan_out
from console.engine.timeouts import rating_suggestion
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.entities import label
from console.reports import field_anchor, role_anchor, seed_anchor
from console.templatetags.console_format import humanise

DRAFT: Literal["draft"] = "draft"
VERSION_FIELD = "version"
RETIRED_FIELD = "include_retired"
NAME_FIELD = "name"

WAIT_TEXT = "Asking the model about each rating — this can take a minute"
ADVISORY_TEXT = "Advisory — the Engine does not apply this"
CHOOSE_VERSION = "Choose a version"
NOT_FOUND_TEXT = "That version doesn't exist"
TRUNCATED_TEXT = "The model saw only part of this entry (private text first)"
EMPTY = "—"
# The rating count assumed when the ratings read fails (the timeout is computed from it).
FALLBACK_RATINGS = 10
# How long the browser waits (htmx's own timeout, 60 s by default): longer than any Console
# timeout, so the Console's timeout message is what the operator sees.
BROWSER_TIMEOUT_S = 3600

PAGE = "console/catalog/rating_suggestion.html"
PARTIAL = "console/catalog/_rating_suggestion_result.html"

_URLS: dict[SuggestionEntity, tuple[str, str, str]] = {
    # (detail, editor, suggestion) URL names
    "character": (
        "catalog_characters:detail",
        "catalog_characters:edit",
        "catalog_characters:rating-suggestion",
    ),
    "scenario": ("scenarios:detail", "scenarios:edit", "scenarios:rating-suggestion"),
}
RATINGS = "ratings"
POLICIES = "policies"


# --- the trigger --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SuggestBox:
    """The detail page's trigger form: where it posts, the entry's name (display only), the
    version options (value, label) and the one selected (the page's own view)."""

    url: str
    name: str
    options: list[tuple[str, str]]
    selected: str
    wait_text: str = WAIT_TEXT
    browser_timeout_s: int = BROWSER_TIMEOUT_S


def suggest_box(
    entry_type: SuggestionEntity,
    entry_id: str,
    *,
    name: str | None,
    current_version: int | None,
    shown: SuggestionVersion | None,
) -> SuggestBox:
    """The trigger for an entry whose latest published version is ``current_version`` (``None``:
    never published), shown at ``shown`` (``None`` or ``"draft"``: the draft)."""
    options: list[tuple[str, str]] = [(DRAFT, "Draft")]
    options += [(str(v), f"Version {v}") for v in range(current_version or 0, 0, -1)]
    selected = DRAFT if shown is None or shown == DRAFT else str(shown)
    if selected not in {value for value, _ in options}:
        options.append((selected, f"Version {selected}"))
    return SuggestBox(_url(entry_type, 2, entry_id), name or "", options, selected)


# --- the result ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ShownConcern:
    breaks: SafeString  # the policy (linked, named) or the quoted clause, or "—"
    element: SafeString
    explanation: str


@dataclass(frozen=True)
class ShownRow:
    rating_id: str
    rating_name: str
    rating_url: str
    version: SafeString
    retired: bool
    appropriate: bool | None
    concerns: list[ShownConcern]
    notes: list[str]


@dataclass(frozen=True)
class ShownCall:
    purpose: str
    model: str
    attempt: str
    outcome: str
    latency_ms: str
    tokens_in: str
    tokens_out: str
    cost: str


@dataclass(frozen=True)
class ShownEvaluated:
    rating: SafeString
    version: SafeString


@dataclass(frozen=True)
class ShownSuggestion:
    heading: SafeString
    rows: list[ShownRow]
    unavailable: list[SafeString]
    evaluated: list[ShownEvaluated]
    calls: list[ShownCall]
    footer: str
    lookup_note: str = ""
    operation_url: str = ""


@dataclass(frozen=True)
class Lookups:
    """The ratings (retired included) and policies by id; ``None`` when that read failed."""

    ratings: dict[str, ContentRatingSummary] | None
    policies: dict[str, Policy] | None

    @property
    def note(self) -> str:
        failed = [n for n, v in ((RATINGS, self.ratings), (POLICIES, self.policies)) if v is None]
        return f"Couldn't load: {', '.join(failed)} — names shown as ids" if failed else ""

    def rating_count(self, include_retired: bool) -> int:
        """The ratings the suggestion weighs: live ones, plus retired ones when ticked."""
        if self.ratings is None:
            return FALLBACK_RATINGS
        return sum(1 for r in self.ratings.values() if include_retired or not r.retired)


def read_lookups(request: HttpRequest, client: EngineClient) -> Lookups:
    """The ratings and policies, read at once on #41's pool, each with #43's read timeout."""
    reads: dict[str, Callable[[], list[Any]]] = {
        RATINGS: lambda: client.list_content_ratings(include_retired=True),
        POLICIES: lambda: client.list_policies(include_retired=True),
    }
    found = fan_out(lambda key: reads[key](), list(reads))
    by_id: dict[str, dict[str, Any] | None] = {}
    for key, value in found.items():
        if isinstance(value, EngineClientError):
            log_engine_error(request, value)
            by_id[key] = None
        else:
            by_id[key] = {item.id: item for item in value}
    return Lookups(by_id.get(RATINGS), by_id.get(POLICIES))


def shown_suggestion(
    suggestion: RatingSuggestion,
    *,
    entry_type: SuggestionEntity,
    entry_id: str,
    name: str,
    lookups: Lookups,
    operation_url: str = "",
) -> ShownSuggestion:
    entry = suggestion.entry
    version_text = "Draft" if entry.version == DRAFT else f"Version {entry.version}"
    heading = format_html(
        "Rating suggestion for <em>{}</em> ({}) — {}",
        label(name, entry_id),
        entry_type,
        version_text,
    )
    shown_rows = [_row(row, entry_type, entry_id, lookups) for row in suggestion.rows]
    unavailable = [
        format_html(
            'Role <code>{}</code>: character <a href="{}"><code>{}</code></a> unavailable ({})',
            item.role,
            _character_url(item.entry, item.version),
            item.entry,
            f"version {item.version}" if item.version is not None else "latest",
        )
        for item in suggestion.unavailable
    ]
    names = {row.rating_id: row.rating_name for row in suggestion.rows}
    evaluated = [
        ShownEvaluated(
            _rating_label(e.rating_id, names, lookups),
            _version_label(e.rating_id, e.rating_version, lookups),
        )
        for e in suggestion.evaluated
    ]
    calls = [_call(c) for c in suggestion.calls()]
    count = len(calls)
    footer = f"{count} model call{'' if count == 1 else 's'} · {suggestion.elapsed_ms / 1000:.1f} s"
    return ShownSuggestion(
        heading,
        shown_rows,
        unavailable,
        evaluated,
        calls,
        footer,
        lookups.note,
        operation_url,
    )


def _row(
    row: SuggestionRow, entry_type: SuggestionEntity, entry_id: str, lookups: Lookups
) -> ShownRow:
    listed = lookups.ratings.get(row.rating_id) if lookups.ratings is not None else None
    notes: list[str] = []
    if row.error:
        notes.append(f"Couldn't assess: {row.error}")
    if row.truncated:
        notes.append(TRUNCATED_TEXT)
    if row.dropped_concerns > 0:
        n = row.dropped_concerns
        notes.append(
            f"{n} concern{'' if n == 1 else 's'} dropped: they named a policy, clause or element "
            "the model wasn't given"
        )
    return ShownRow(
        row.rating_id,
        row.rating_name,
        reverse("ratings:detail", args=[row.rating_id]),
        _version_label(row.rating_id, row.rating_version, lookups),
        listed is not None and listed.retired,
        row.appropriate,
        [_concern(c, entry_type, entry_id, lookups) for c in row.concerns],
        notes,
    )


def _concern(
    concern: Concern, entry_type: SuggestionEntity, entry_id: str, lookups: Lookups
) -> ShownConcern:
    if concern.policy_id:
        policies = lookups.policies
        policy = policies.get(concern.policy_id) if policies is not None else None
        if policies is None:
            breaks = format_html("Policy <code>{}</code>", concern.policy_id)
        elif policy is not None:
            breaks = format_html(
                'Policy <a href="{}">{}</a> <code>{}</code>',
                reverse("policies:detail", args=[concern.policy_id]),
                policy.name,
                concern.policy_id,
            )
        else:
            breaks = format_html(
                'Policy <a href="{}"><code>{}</code></a>',
                reverse("policies:detail", args=[concern.policy_id]),
                concern.policy_id,
            )
    elif concern.clause:
        breaks = format_html("Rating description: “{}”", concern.clause)
    else:
        breaks = format_html("{}", EMPTY)
    element = element_html(concern.element.kind, concern.element.location, entry_type, entry_id)
    return ShownConcern(breaks, element, concern.explanation)


def element_html(
    kind: str, location: EntryLocation, entry_type: SuggestionEntity, entry_id: str
) -> SafeString:
    """A concern's element in words, linked where the Console has a place for it: fields and
    reveals to their editor (draft) or detail (version) anchor, seeds and roles to #73's cast
    anchors, a cast character to its version, a caption to its media page. A location in
    another entry than the one reviewed (a cast character inside a scenario's review) links to
    that character's pages."""
    target_type: SuggestionEntity = entry_type if location.entry == entry_id else "character"
    version = location.version
    where = format_html(" (version {})", version) if version != DRAFT else SafeString("")

    def at(anchor: str | None) -> str:
        return _entry_url(target_type, location.entry, version, anchor)

    if kind == "field" and location.field:
        return format_html(
            'Field <a href="{}"><code>{}</code></a>{}',
            at(field_anchor(location.field)),
            location.field,
            where,
        )
    if kind == "reveal" and location.field:
        return format_html(
            'Reveal at <a href="{}"><code>{}</code></a>{}',
            at(field_anchor(location.field)),
            location.field,
            where,
        )
    if kind == "seed" and location.seed_id:
        role = format_html(" (role <code>{}</code>)", location.role) if location.role else ""
        return format_html(
            'Seed <a href="{}"><code>{}</code></a>{}{}',
            at(seed_anchor(location.seed_id)),
            location.seed_id,
            role,
            where,
        )
    if kind == "role" and location.role:
        return format_html(
            'Role <a href="{}"><code>{}</code></a>{}',
            at(role_anchor(location.role)),
            location.role,
            where,
        )
    if kind == "cast_character":
        role = format_html("role <code>{}</code>, ", location.role) if location.role else ""
        return format_html(
            'Cast character <a href="{}"><code>{}</code></a> ({}{})',
            _character_url(location.entry, version),
            location.entry,
            role,
            "draft" if version == DRAFT else f"version {version}",
        )
    if kind == "media_caption" and location.media_id:
        return format_html(
            'Caption of media <a href="{}"><code>{}</code></a>',
            reverse("media:detail", args=[location.media_id]),
            location.media_id,
        )
    # An element the Console has no wording for (or one missing its part): kind and entry.
    return format_html(
        '{} in <a href="{}"><code>{}</code></a>{}',
        humanise(kind).capitalize(),
        at(None),
        location.entry,
        where,
    )


def _entry_url(
    entry_type: SuggestionEntity, entry_id: str, version: SuggestionVersion, anchor: str | None
) -> str:
    """The editor for the draft, the detail page at ``?version=N`` for a version, at
    ``#anchor``."""
    if version == DRAFT:
        base = _url(entry_type, 1, entry_id)
    else:
        base = f"{_url(entry_type, 0, entry_id)}?{urlencode({'version': version})}"
    return f"{base}#{anchor}" if anchor else base


def _character_url(character_id: str, version: SuggestionVersion | None) -> str:
    return detail_at("character", character_id, version)


def detail_at(
    entry_type: SuggestionEntity, entry_id: str, version: SuggestionVersion | None
) -> str:
    """The entry's detail page at ``version`` (``?view=draft`` for the draft; ``None``: the
    page's default view)."""
    base = _url(entry_type, 0, entry_id)
    if version is None:
        return base
    if version == DRAFT:
        return f"{base}?{urlencode({'view': DRAFT})}"
    return f"{base}?{urlencode({'version': version})}"


def _url(entry_type: SuggestionEntity, which: int, entry_id: str) -> str:
    return reverse(_URLS[entry_type][which], args=[entry_id])


def _version_label(rating_id: str, version_id: str, lookups: Lookups) -> SafeString:
    """ "version N" when ``version_id`` is the rating's current version in the ratings read
    (#80's rule), else the raw id."""
    listed = lookups.ratings.get(rating_id) if lookups.ratings is not None else None
    if listed is not None and listed.version_id == version_id:
        return format_html("version {}", listed.version)
    return format_html("<code>{}</code>", version_id)


def _rating_label(rating_id: str, names: dict[str, str], lookups: Lookups) -> SafeString:
    listed = lookups.ratings.get(rating_id) if lookups.ratings is not None else None
    name = names.get(rating_id) or (listed.name if listed is not None else "")
    url = reverse("ratings:detail", args=[rating_id])
    if name:
        return format_html('<a href="{}">{}</a>', url, name)
    return format_html('<a href="{}"><code>{}</code></a>', url, rating_id)


def _call(call: SuggestionModelCall) -> ShownCall:
    def plain(value: object) -> str:
        return EMPTY if value is None else str(value)

    def tokens(value: int | None) -> str:
        return EMPTY if value is None else f"{value:,}"

    return ShownCall(
        plain(call.purpose),
        plain(call.model),
        plain(call.attempt),
        plain(call.outcome),
        plain(call.latency_ms),
        tokens(call.tokens_in),
        tokens(call.tokens_out),
        EMPTY if call.cost is None else f"{call.cost:.4f}",
    )


# --- the view -----------------------------------------------------------------------------------


def parse_version(value: str | None) -> SuggestionVersion | None:
    """``"draft"`` or a positive integer; anything else is ``None``."""
    value = (value or "").strip()
    if value == DRAFT:
        return DRAFT
    if value.isascii() and value.isdigit() and int(value) > 0:
        return int(value)
    return None


@require_http_methods(["GET", "POST"])
def suggest(request: HttpRequest, entry_id: str, entry: SuggestionEntity) -> HttpResponse:
    """``/catalog/{characters|scenarios}/<id>/rating-suggestion/``: GET goes back to the detail
    page; POST asks the Engine for the advisory table and shows it."""
    if request.method != "POST":
        return redirect(_url(entry, 0, entry_id))
    post = request.POST
    name = post.get(NAME_FIELD, "").strip()
    include_retired = post.get(RETIRED_FIELD) in {"true", "on", "1"}
    version = parse_version(post.get(VERSION_FIELD))
    context: dict[str, Any] = {
        "entry_type": entry,
        "entry_id": entry_id,
        "name": label(name, entry_id),
        "back_url": detail_at(entry, entry_id, version),
    }
    template = PARTIAL if is_htmx(request) else PAGE
    if version is None:
        context["problem"] = CHOOSE_VERSION
        return render(request, template, context, status=400)
    hidden = [(VERSION_FIELD, str(version)), (NAME_FIELD, name)]
    if include_retired:
        hidden.append((RETIRED_FIELD, "true"))
    context["rerun"] = {"url": _url(entry, 2, entry_id), "hidden": hidden, "wait_text": WAIT_TEXT}
    context["browser_timeout_s"] = BROWSER_TIMEOUT_S
    client = engine_client_for(request)
    lookups = read_lookups(request, client)
    timeout = rating_suggestion(lookups.rating_count(include_retired))
    try:
        suggestion = client.suggest_ratings(
            entry, entry_id, version=version, include_retired=include_retired, timeout=timeout
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["problem"], context["engine_error"] = _failure(exc, timeout)
        context["operation_url"] = _operation_url(client)
        return render(request, template, context)
    context["result"] = shown_suggestion(
        suggestion,
        entry_type=entry,
        entry_id=entry_id,
        name=name,
        lookups=lookups,
        operation_url=_operation_url(client),
    )
    return render(request, template, context)


def _failure(exc: EngineClientError, timeout: float) -> tuple[str, ErrorPanel | None]:
    """The inline wording for a Console-side timeout and a 404, else #43's panel."""
    if isinstance(exc, EngineUnreachable) and exc.reason == "timeout":
        return (
            f"The suggestion didn't finish within {timeout:g} s; the Engine may still be "
            "working — run it again",
            None,
        )
    if isinstance(exc, EngineError) and exc.http_status == 404:
        return NOT_FOUND_TEXT, None
    return "", panel_for(exc)


def _operation_url(client: EngineClient) -> str:
    pk = getattr(client, "last_operation_id", None)
    return reverse("operations:detail", args=[pk]) if isinstance(pk, int) else ""
