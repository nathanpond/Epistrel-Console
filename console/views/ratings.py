"""Content ratings (#49): the list, a rating's detail (effective guardrails, inheritance, guidance
and version history), its versions, and creating, editing and retiring ratings.

Reads per page, each degrading on its own section:

- the list reads every rating (``include_retired=1``, so a retired parent still shows by name;
  "Show retired" filters here) and ``GET /admin/settings`` for the default marker (a failed read
  shows the error panel above the list, without the marker);
- the detail reads the rating, every rating (inheritance and source names), every policy
  (``include_retired=1``, for names) and the versions.

The forms carry their option lists (and on edit the rating's name and version) in a signed
hidden field, so a re-render after the Console refuses input makes no Engine call. An edit
re-reads the rating on POST and compares (:func:`console.forms.ratings.unchanged`): no change,
no ``PUT``. The Engine is last-write-wins here, so there is no stale-version check.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.contrib import messages
from django.core import signing
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import (
    ConfirmPage,
    confirm_action,
    confirm_query,
    posted_idempotency_key,
    safe_next,
    shown_name,
)
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import (
    ContentRating,
    ContentRatingMutation,
    ContentRatingSummary,
    Policy,
    RatingVersion,
)
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.ratings import RatingForm, rating_initial, unchanged
from console.ids import new_ulid

PAGE_SIZE = 50
HISTORY_SHOWN = 20  # newest versions on the detail page; the rest on the versions page
OPTIONS_FIELD = "options"
OPTIONS_SALT = "console.ratings.options"
DEFAULT_REFUSAL = "This is the deployment default — choose another default first"
RETIRED_REFUSAL = "Retired ratings cannot be changed"
NO_CHANGES = "No changes — no new version"
GUIDANCE_NOTE = "Guidance is not inherited"
RETIRE_QUESTION = "Stories using {name} keep their version; new stories cannot choose it."
SETTINGS_URL = "/engine/"  # deployment settings (#50)
GLOBAL_POLICIES_URL = "/guardrails/policies/?scope=global"
CHAIN_DEPTH_MAX = 64  # guards the drawing against a loop the Engine should never store


def cycle_message(name: str) -> str:
    """The ``inheritance_cycle`` refusal, naming the rating being saved."""
    return f"That would make {name} inherit from itself"


# --- Names --------------------------------------------------------------------------------------


type Ratings = list[ContentRatingSummary] | None  # None: the read failed
type Policies = list[Policy] | None


def _read_ratings(request: HttpRequest, client: EngineClient) -> Ratings:
    """Every rating, retired included; ``None`` when the read fails (logged)."""
    try:
        return client.list_content_ratings(include_retired=True)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def _read_policies(request: HttpRequest, client: EngineClient) -> Policies:
    """Every rating-scoped policy, retired included; ``None`` when the read fails (logged)."""
    try:
        return client.list_policies("rating", include_retired=True)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def rating_label(rating: ContentRatingSummary) -> str:
    return f"{rating.name} (retired)" if rating.retired else rating.name


def _rating_labels(ratings: Ratings) -> dict[str, str]:
    return {rating.id: rating_label(rating) for rating in ratings or ()}


def policy_label(policy_id: str, policies: dict[str, Policy]) -> str:
    """ "<name>", "<name> (retired)", or the raw id with "(unknown policy)"."""
    policy = policies.get(policy_id)
    if policy is None:
        return f"{policy_id} (unknown policy)"
    return f"{policy.name} (retired)" if policy.retired else policy.name


def _list_url() -> str:
    return reverse("ratings:list")


def _detail_url(rating_id: str) -> str:
    return reverse("ratings:detail", kwargs={"rating_id": rating_id})


# --- Inheritance --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChainNode:
    """A rating in the inheritance drawing and the ratings it inherits from (Engine order)."""

    rating_id: str
    label: str  # name, "(retired)", or the raw id when the rating is not in the list
    parents: tuple[ChainNode, ...] = ()
    loop: bool = False  # already drawn above on this path (never expected from the Engine)


def inheritance(rating: ContentRatingSummary, ratings: Ratings) -> ChainNode:
    """``rating`` and, recursively, what it inherits from, as the Engine reports it."""
    by_id = {r.id: r for r in ratings or ()}

    def node(rating_id: str, parents: Sequence[str], path: tuple[str, ...]) -> ChainNode:
        known = by_id.get(rating_id)
        label = rating_label(known) if known is not None else rating_id
        if rating_id in path or len(path) >= CHAIN_DEPTH_MAX:
            return ChainNode(rating_id, label, loop=True)
        here = (*path, rating_id)
        drawn = []
        for parent_id in parents:
            parent = by_id.get(parent_id)
            drawn.append(node(parent_id, parent.inherits_from if parent else (), here))
        return ChainNode(rating_id, label, tuple(drawn))

    root = node(rating.id, rating.inherits_from, ())
    return replace(root, label=rating_label(rating))


def chain_labels(root: ChainNode) -> list[str] | None:
    """The labels from ``root`` up when every rating has at most one parent (a line:
    "Everyone → Teen → Mature → Explicit"); ``None`` when the drawing branches."""
    labels = []
    current: ChainNode | None = root
    while current is not None:
        if len(current.parents) > 1:
            return None
        labels.append(current.label + (" (loop)" if current.loop else ""))
        current = current.parents[0] if current.parents else None
    return labels


# --- List ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RatingRow:
    rating: ContentRatingSummary
    parents: list[str]  # labels
    default: bool
    action_query: str

    @property
    def retirable(self) -> bool:
        return not self.rating.retired


def _default_rating(
    request: HttpRequest, client: EngineClient
) -> tuple[str | None, ErrorPanel | None]:
    """The deployment default's id, or the error panel for a failed settings read."""
    try:
        return client.get_deployment_settings().default_content_rating_id, None
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None, panel_for(exc)


@require_safe
def rating_list(request: HttpRequest) -> HttpResponse:
    """Ratings in display order with what each inherits from, its version and status; the
    deployment default marked."""
    show_retired = request.GET.get("retired") == "1"
    client = engine_client_for(request)
    ratings = client.list_content_ratings(include_retired=True)
    default_id, settings_error = _default_rating(request, client)
    labels = _rating_labels(ratings)
    shown = [r for r in ratings if show_retired or not r.retired]
    page = Paginator(shown, PAGE_SIZE).get_page(request.GET.get("page"))
    next_url = request.get_full_path()
    rows = [
        RatingRow(
            rating,
            [labels.get(parent, parent) for parent in rating.inherits_from],
            rating.id == default_id,
            confirm_query(rating.name, next_url),
        )
        for rating in page.object_list
    ]
    return render(
        request,
        "console/ratings/list.html",
        {
            "rows": rows,
            "page": page,
            "show_retired": show_retired,
            "filter_query": "retired=1" if show_retired else "",
            "engine_error": settings_error,
        },
    )


# --- Detail and versions ------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardrailRow:
    policy_id: str
    policy: str  # label
    source_id: str
    source: str  # label
    own: bool


def guardrail_rows(
    rating: ContentRating, ratings: Ratings, policies: Policies
) -> tuple[list[GuardrailRow], list[GuardrailRow]]:
    """``(own, inherited)``: own when the source is this rating, inherited otherwise."""
    labels = _rating_labels(ratings)
    known = {policy.id: policy for policy in policies or ()}
    own: list[GuardrailRow] = []
    inherited: list[GuardrailRow] = []
    for item in rating.effective_guardrails:
        is_own = item.source_rating_id == rating.id
        source = rating_label(rating) if is_own else labels.get(item.source_rating_id)
        row = GuardrailRow(
            item.policy_id,
            policy_label(item.policy_id, known) if policies is not None else item.policy_id,
            item.source_rating_id,
            source or item.source_rating_id,
            is_own,
        )
        (own if is_own else inherited).append(row)
    return own, inherited


def _read_versions(
    request: HttpRequest, client: EngineClient, rating_id: str
) -> tuple[list[RatingVersion], ErrorPanel | None]:
    """The versions, newest first; an empty list and the error panel when the read fails."""
    try:
        versions = client.list_content_rating_versions(rating_id)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return [], panel_for(exc)
    return sorted(versions, key=lambda v: v.version, reverse=True), None


@require_safe
def rating_detail(request: HttpRequest, rating_id: str) -> HttpResponse:
    """Every field of the latest version, the effective guardrails (own and inherited), the
    inheritance, and the newest versions."""
    client = engine_client_for(request)
    rating = client.get_content_rating(rating_id)
    ratings = _read_ratings(request, client)
    policies = _read_policies(request, client)
    versions, versions_error = _read_versions(request, client, rating_id)
    own, inherited = guardrail_rows(rating, ratings, policies)
    tree = inheritance(rating, ratings)
    labels = _rating_labels(ratings)
    known = {policy.id: policy for policy in policies or ()}
    return render(
        request,
        "console/ratings/detail.html",
        {
            "rating": rating,
            "own": own,
            "inherited": inherited,
            "tree": tree,
            "chain": chain_labels(tree),
            "parents": [labels.get(parent, parent) for parent in rating.inherits_from],
            "policies": [
                policy_label(pid, known) if policies is not None else pid
                for pid in rating.policy_ids
            ],
            "ratings_failed": ratings is None,
            "policies_failed": policies is None,
            "versions": versions[:HISTORY_SHOWN],
            "more_versions": len(versions) > HISTORY_SHOWN,
            "version_count": len(versions),
            "versions_error": versions_error,
            "guidance_note": GUIDANCE_NOTE,
            "global_policies_url": GLOBAL_POLICIES_URL,
            "action_query": confirm_query(rating.name, request.get_full_path()),
        },
    )


@require_safe
def rating_versions(request: HttpRequest, rating_id: str) -> HttpResponse:
    """Every version, newest first, 50 per page."""
    client = engine_client_for(request)
    versions = sorted(
        client.list_content_rating_versions(rating_id), key=lambda v: v.version, reverse=True
    )
    page = Paginator(versions, PAGE_SIZE).get_page(request.GET.get("page"))
    name = versions[0].name if versions else rating_id
    return render(
        request,
        "console/ratings/versions.html",
        {"rating_id": rating_id, "name": name, "page": page},
    )


@require_safe
def rating_version(request: HttpRequest, rating_id: str, version: int) -> HttpResponse:
    """One version as ``GET …/{id}/versions`` lists it (no diff in M2)."""
    client = engine_client_for(request)
    versions = client.list_content_rating_versions(rating_id)
    entry = next((v for v in versions if v.version == version), None)
    if entry is None:
        raise Http404(f"Version {version} of rating {rating_id} does not exist")
    ratings = _read_ratings(request, client)
    labels = _rating_labels(ratings)
    return render(
        request,
        "console/ratings/version.html",
        {
            "entry": entry,
            "latest": max(v.version for v in versions),
            "parents": [labels.get(parent, parent) for parent in entry.inherits_from],
            "guidance_note": GUIDANCE_NOTE,
        },
    )


# --- Form options -------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormOptions:
    """What a rating form was rendered from: its pickers' options (``None``: the read failed)
    and, on edit, the rating's name and version (headings only)."""

    policies: list[tuple[str, str]] | None
    parents: list[tuple[str, str]] | None
    name: str = ""
    version: int = 0

    def signed(self) -> str:
        raw = {
            "policies": self.policies,
            "parents": self.parents,
            "name": self.name,
            "version": self.version,
        }
        return signing.dumps(raw, salt=OPTIONS_SALT, compress=True)


def _pairs(raw: object) -> list[tuple[str, str]] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("options")
    pairs = []
    for item in raw:
        if not (
            isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)
        ):
            raise ValueError("options")
        pairs.append((item[0], item[1]))
    return pairs


def _posted_options(request: HttpRequest) -> FormOptions | None:
    """The options the posted page carried; ``None`` when absent or tampered."""
    try:
        raw: Any = signing.loads(request.POST.get(OPTIONS_FIELD, ""), salt=OPTIONS_SALT)
        return FormOptions(
            _pairs(raw["policies"]), _pairs(raw["parents"]), str(raw["name"]), int(raw["version"])
        )
    except signing.BadSignature, KeyError, TypeError, ValueError:
        return None


def parent_options(
    ratings: Ratings, rating: ContentRatingSummary | None
) -> list[tuple[str, str]] | None:
    """Non-retired ratings other than ``rating``, in display order, plus any rating it
    inherits from now that is retired ("(retired)") or not in the list (its raw id)."""
    if ratings is None:
        return None
    own_id = rating.id if rating else None
    options = [
        (r.id, r.name)
        for r in sorted(ratings, key=lambda r: r.display_order)
        if not r.retired and r.id != own_id
    ]
    offered = {value for value, _ in options}
    by_id = {r.id: r for r in ratings}
    for parent_id in rating.inherits_from if rating else ():
        if parent_id not in offered:
            known = by_id.get(parent_id)
            options.append((parent_id, rating_label(known) if known else parent_id))
            offered.add(parent_id)
    return options


def policy_options(
    policies: Policies, rating: ContentRatingSummary
) -> list[tuple[str, str]] | None:
    """Live policies scoped to ``rating`` (by name), plus any it applies now that is not one of
    them — retired, another rating's, or unknown — so saving never drops it unseen."""
    if policies is None:
        return None
    mine = sorted(
        (p for p in policies if p.scope_id == rating.id and not p.retired),
        key=lambda p: p.name.casefold(),
    )
    options = [(p.id, f"{p.name} ({p.id})") for p in mine]
    offered = {value for value, _ in options}
    known = {p.id: p for p in policies}
    for policy_id in rating.policy_ids:
        if policy_id in offered:
            continue
        policy = known.get(policy_id)
        if policy is not None and not policy.retired:
            label = f"{policy.name} ({policy_id}) — another rating's policy"
        else:
            label = f"{policy_label(policy_id, known)} ({policy_id})"
        options.append((policy_id, label))
        offered.add(policy_id)
    return options


# --- New ----------------------------------------------------------------------------------------


def _render_form(
    request: HttpRequest,
    form: RatingForm,
    options: FormOptions,
    *,
    rating_id: str | None = None,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    return render(
        request,
        "console/ratings/form.html",
        {
            "form": form,
            "options": options,
            "rating_id": rating_id,
            "engine_error": engine_error,
            "idempotency_key": new_ulid(),  # minted at each render (#43)
            "signed_options": options.signed(),
            "guidance_note": GUIDANCE_NOTE,
        },
    )


def _refused(
    request: HttpRequest,
    exc: EngineClientError,
    form: RatingForm,
    options: FormOptions,
    rating_id: str | None = None,
) -> HttpResponse:
    """A failed write, shown on the form (input kept)."""
    log_engine_error(request, exc)
    if isinstance(exc, EngineError):
        if exc.code == "inheritance_cycle":
            name = form.cleaned_data.get("name") or options.name
            target = "inherits_from" if "inherits_from" in form.fields else None
            form.add_error(target, cycle_message(name))
            return _render_form(request, form, options, rating_id=rating_id)
        if exc.http_status == 409 and rating_id is not None:
            form.add_error(None, RETIRED_REFUSAL)
            return _render_form(request, form, options, rating_id=rating_id)
        if exc.http_status == 422:
            apply_engine_errors(exc, form)
            form.add_error(None, f"The Engine rejected the rating: {exc.message}")
            return _render_form(request, form, options, rating_id=rating_id)
    return _render_form(request, form, options, rating_id=rating_id, engine_error=panel_for(exc))


def _flash_write(request: HttpRequest, verb: str, written: ContentRatingMutation, key: str) -> None:
    messages.success(
        request,
        f"{verb} {written.name} — version {written.version}, config revision "
        f"{written.config_revision}, idempotency key {written.idempotency_key or key}",
    )


@require_http_methods(["GET", "HEAD", "POST"])
def rating_new(request: HttpRequest) -> HttpResponse:
    """GET: an empty form. POST: validate, then ``POST /admin/content-ratings`` and go to the
    new rating's page."""
    options = _posted_options(request) if request.method == "POST" else None
    if options is None:
        ratings = _read_ratings(request, engine_client_for(request))
        options = FormOptions(None, parent_options(ratings, None))
    if request.method != "POST":
        form = RatingForm(policies=None, parents=options.parents, creating=True)
        return _render_form(request, form, options)
    form = RatingForm(request.POST, policies=None, parents=options.parents, creating=True)
    if not form.is_valid():
        return _render_form(request, form, options)
    key = posted_idempotency_key(request)
    try:
        created = engine_client_for(request).create_content_rating(
            form.fields_body(), idempotency_key=key
        )
    except EngineClientError as exc:
        return _refused(request, exc, form, options)
    _flash_write(request, "Created", created, key)
    return redirect(_detail_url(created.id))


# --- Edit ---------------------------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def rating_edit(request: HttpRequest, rating_id: str) -> HttpResponse:
    """GET: the latest version as a form. POST: validate; re-read the rating and compare (no
    change: no ``PUT``); else ``PUT`` the whole rating as a new version."""
    if request.method != "POST":
        client = engine_client_for(request)
        rating = client.get_content_rating(rating_id)
        options = FormOptions(
            policy_options(_read_policies(request, client), rating),
            parent_options(_read_ratings(request, client), rating),
            rating.name,
            rating.version,
        )
        form = RatingForm(
            policies=options.policies,
            parents=options.parents,
            creating=False,
            initial=rating_initial(rating),
        )
        return _render_form(request, form, options, rating_id=rating_id)

    posted = _posted_options(request)
    client = engine_client_for(request)
    if posted is None:  # absent or tampered: read the options again
        current = client.get_content_rating(rating_id)
        posted = FormOptions(
            policy_options(_read_policies(request, client), current),
            parent_options(_read_ratings(request, client), current),
            current.name,
            current.version,
        )
    options = posted
    form = RatingForm(
        request.POST, policies=options.policies, parents=options.parents, creating=False
    )
    if not form.is_valid():
        return _render_form(request, form, options, rating_id=rating_id)
    baseline = client.get_content_rating(rating_id)
    body = form.replacement(baseline)
    if unchanged(body, baseline):
        messages.info(request, NO_CHANGES)
        return redirect(_detail_url(rating_id))
    key = posted_idempotency_key(request)
    try:
        saved = client.update_content_rating(rating_id, body, idempotency_key=key)
    except EngineClientError as exc:
        return _refused(request, exc, form, options, rating_id)
    _flash_write(request, "Saved", saved, key)
    return redirect(_detail_url(rating_id))


# --- Retire (confirm page) ----------------------------------------------------------------------


def is_default_refusal(exc: EngineClientError) -> bool:
    """The Engine's 409 for retiring the deployment default (``details.reason = "is_default"``)."""
    return (
        isinstance(exc, EngineError)
        and exc.http_status == 409
        and exc.details.get("reason") == "is_default"
    )


@require_http_methods(["GET", "HEAD", "POST"])
def rating_retire(request: HttpRequest, rating_id: str) -> HttpResponse:
    """GET: the confirm page (no Engine call). POST: ``DELETE /admin/content-ratings/{id}`` and
    back to the list; the deployment default's 409 gets its own message."""
    label = shown_name(request) or rating_id
    page = ConfirmPage(
        title="Retire content rating",
        question=RETIRE_QUESTION,
        emphasis=label,
        confirm_label="Retire",
        cancel_url=safe_next(request, _detail_url(rating_id)),
        facts=(("Rating id", rating_id),),
        hidden={"name": shown_name(request)},
    )

    def perform(key: str) -> HttpResponse:
        try:
            retired = engine_client_for(request).retire_content_rating(
                rating_id, idempotency_key=key
            )
        except EngineClientError as exc:
            if not is_default_refusal(exc):
                raise
            log_engine_error(request, exc)
            return render(
                request,
                "console/ratings/retire_refused.html",
                {
                    "label": label,
                    "rating_id": rating_id,
                    "message": DEFAULT_REFUSAL,
                    "settings_url": SETTINGS_URL,
                },
            )
        if retired is None:
            messages.info(request, f"{label} was already retired — nothing changed")
        else:
            messages.success(
                request,
                f"Retired {retired.name} — config revision {retired.config_revision}, "
                f"idempotency key {retired.idempotency_key or key}",
            )
        return redirect(_list_url())

    return confirm_action(request, page, perform)
