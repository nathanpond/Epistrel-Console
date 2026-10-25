"""Guardrail policies (#47): every global, rating, story and character policy the Engine lists;
creating, editing and retiring the deployment's own (global and rating) policies.

Story and character policies are listed and shown, but written through the story's settings
(#54): they have no Edit or Retire here, and the Engine refuses a forced write (409
``story_scoped``, the error panel).

Ratings come from one ``GET /admin/content-ratings?include_retired=1`` per page: a retired
rating shows "<name> (retired)", an unknown id shows raw, and a failed read degrades to raw ids
(and no rating scope on the new-policy form). The forms carry what they were rendered from in
signed hidden fields (the rating options; on edit, the policy as read), so a re-render after the
Console refuses input makes no Engine call. Scope, rating and kind are fixed after create: to
change them, retire the policy and create another.
"""

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

from django.contrib import messages
from django.core import signing
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
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
from console.engine.models import Policy, PolicyMutation
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms.policy import (
    DEFAULT_TARGETS,
    DEPLOYMENT_RULE_TYPES,
    POLICY_ERROR_PATHS,
    RULE_LABELS,
    PolicyContentForm,
    PolicyCreateForm,
    PolicyFilterForm,
    kind_of,
    policy_initial,
    target_label,
)
from console.ids import new_ulid
from console.rating_policies import attach_key, attach_policy

PAGE_SIZE = 50
MINORS_POLICY_ID = "minors_no_sexual_content"
MINORS_RETIRE_LABEL = "Retire (minors protection)"
MINORS_WARNING = (
    "This is the minors protection policy. Retiring it stops it applying to every story — "
    "removing it is the deployment's decision."
)
RETIRED_REFUSAL = "Retired policies cannot be changed"
RATINGS_UNAVAILABLE = "The content ratings could not be read: rating scope is unavailable."
REJECTIONS_URL = "/operations/?guardrail=1"  # #44's "guardrail rejections only" filter
ADMIN_SCOPES = frozenset({"global", "rating"})  # written here; story/character through #54
RATINGS_FIELD = "ratings"
RATINGS_SALT = "console.policies.ratings"
SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.policies.snapshot"
SCOPE_ORDER = {"global": 0, "rating": 1, "story": 2, "character": 3}


# --- Ratings ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RatingOption:
    id: str
    name: str
    display_order: int
    retired: bool

    @property
    def label(self) -> str:
        return f"{self.name} (retired)" if self.retired else self.name


type Ratings = list[RatingOption] | None  # None: the read failed


def _read_ratings(request: HttpRequest, client: EngineClient) -> Ratings:
    """Every rating, retired included (one read); ``None`` when the read fails (logged)."""
    try:
        listed = client.list_content_ratings(include_retired=True)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None
    return [RatingOption(r.id, r.name, r.display_order, r.retired_at is not None) for r in listed]


def _signed_ratings(ratings: Ratings) -> str:
    raw = None if ratings is None else [asdict(rating) for rating in ratings]
    return signing.dumps(raw, salt=RATINGS_SALT, compress=True)


def _posted_ratings(request: HttpRequest) -> tuple[bool, Ratings]:
    """``(ok, ratings)`` as the posted page carried them; ``ok`` is false when the field is
    absent or tampered (the caller reads them again)."""
    try:
        raw = signing.loads(request.POST.get(RATINGS_FIELD, ""), salt=RATINGS_SALT)
    except signing.BadSignature:
        return False, None
    if raw is None:
        return True, None
    try:
        return True, [RatingOption(**item) for item in raw]
    except TypeError:
        return False, None


def _form_ratings(request: HttpRequest) -> Ratings:
    ok, ratings = _posted_ratings(request)
    return ratings if ok else _read_ratings(request, engine_client_for(request))


def _scope_options(ratings: Ratings) -> list[tuple[str, str]] | None:
    """The new-policy scope select's rating options: non-retired, in display order."""
    if ratings is None:
        return None
    live = sorted((r for r in ratings if not r.retired), key=lambda r: r.display_order)
    return [(rating.id, rating.name) for rating in live]


def _rating_label(ratings: Ratings, rating_id: str | None) -> str:
    if rating_id is None:
        return "—"
    for rating in ratings or ():
        if rating.id == rating_id:
            return rating.label
    return rating_id


def scope_label(policy: Policy, ratings: Ratings) -> str:
    """``global``, ``rating: Teen``, ``story: <id>``, ``character: <id>``."""
    if policy.scope == "global":
        return "global"
    if policy.scope == "rating":
        return f"rating: {_rating_label(ratings, policy.scope_id)}"
    return f"{policy.scope}: {policy.scope_id or '—'}"


def _sort_key(policy: Policy, ratings: Ratings) -> tuple[int, int, str, str]:
    """Global first, then rating scope by the rating's display order, then name."""
    order = 0
    if policy.scope == "rating":
        known = {rating.id: rating.display_order for rating in ratings or ()}
        order = known.get(policy.scope_id or "", 1 << 30)
    scope_id = (policy.scope_id or "") if policy.scope in ("story", "character") else ""
    return SCOPE_ORDER.get(policy.scope, 9), order, scope_id, policy.name.casefold()


# --- Display ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyView:
    """A policy with what its pages show beside it."""

    policy: Policy
    scope_label: str
    action_query: str  # ?name=…&next=… for the Retire link

    @property
    def rule_label(self) -> str:
        return RULE_LABELS.get(self.policy.rule_type, self.policy.rule_type or "—")

    @property
    def targets(self) -> list[str]:
        return [target_label(target) for target in self.policy.applies_to]

    @property
    def editable(self) -> bool:
        """Written here (global and rating); story and character policies through #54."""
        return self.policy.scope in ADMIN_SCOPES

    @property
    def retirable(self) -> bool:
        return self.editable and not self.policy.retired

    @property
    def minors(self) -> bool:
        return self.policy.id == MINORS_POLICY_ID

    @property
    def retire_label(self) -> str:
        return MINORS_RETIRE_LABEL if self.minors else "Retire"

    @property
    def rule_entries(self) -> list[str]:
        """A list rule's entries (words, patterns or names)."""
        for name in ("words", "patterns", "names"):
            value = self.policy.rule.get(name)
            if isinstance(value, list):
                return [str(entry) for entry in value]
        return []


def _views(policies: Iterable[Policy], ratings: Ratings, next_url: str) -> list[PolicyView]:
    return [
        PolicyView(policy, scope_label(policy, ratings), confirm_query(policy.name, next_url))
        for policy in policies
    ]


def _list_url() -> str:
    return reverse("policies:list")


def _detail_url(policy_id: str) -> str:
    return reverse("policies:detail", kwargs={"policy_id": policy_id})


# --- List and detail ----------------------------------------------------------------------------


@require_safe
def policy_list(request: HttpRequest) -> HttpResponse:
    """``GET /admin/policies`` (``scope``, ``include_retired``), sorted here: global first, then
    rating scope by the rating's display order, then name; 50 per page."""
    filters = PolicyFilterForm(request.GET)
    scope, include_retired = filters.chosen()
    client = engine_client_for(request)
    policies = client.list_policies(scope, include_retired=include_retired)
    ratings = _read_ratings(request, client)
    ordered = sorted(policies, key=lambda policy: _sort_key(policy, ratings))
    page = Paginator(ordered, PAGE_SIZE).get_page(request.GET.get("page"))
    kept = request.GET.copy()
    kept.pop("page", None)
    return render(
        request,
        "console/policies/list.html",
        {
            "page": page,
            "rows": _views(page.object_list, ratings, request.get_full_path()),
            "filters": filters,
            "scope": scope or "",
            "include_retired": include_retired,
            "filter_query": kept.urlencode(),
            "ratings_failed": ratings is None,
            "rejections_url": REJECTIONS_URL,
            "minors_warning": MINORS_WARNING,
        },
    )


@require_safe
def policy_detail(request: HttpRequest, policy_id: str) -> HttpResponse:
    """``GET /admin/policies/{id}``, read-only: every field, the full rule, version and
    attribution."""
    client = engine_client_for(request)
    policy = client.get_policy(policy_id)
    ratings = _read_ratings(request, client) if policy.scope == "rating" else []
    (view,) = _views([policy], ratings, request.get_full_path())
    return render(
        request,
        "console/policies/detail.html",
        {"row": view, "policy": policy, "minors_warning": MINORS_WARNING},
    )


# --- New policy ---------------------------------------------------------------------------------


def _render_new(
    request: HttpRequest,
    form: PolicyCreateForm,
    ratings: Ratings,
    *,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    return render(
        request,
        "console/policies/new.html",
        {
            "form": form,
            "engine_error": engine_error,
            "ratings_note": RATINGS_UNAVAILABLE if ratings is None else "",
            "idempotency_key": new_ulid(),  # minted at each render (#43)
            "ratings": _signed_ratings(ratings),
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def policy_new(request: HttpRequest) -> HttpResponse:
    """GET: an empty form (the Engine's default targets ticked). POST: validate, then
    ``POST /admin/policies`` and go to the new policy's page."""
    if request.method != "POST":
        ratings = _read_ratings(request, engine_client_for(request))
        form = PolicyCreateForm(
            ratings=_scope_options(ratings),
            initial={"applies_to": DEFAULT_TARGETS, "rule_type": "wordlist"},
        )
        return _render_new(request, form, ratings)

    ratings = _form_ratings(request)
    form = PolicyCreateForm(request.POST, ratings=_scope_options(ratings))
    if not form.is_valid():
        return _render_new(request, form, ratings)
    key = posted_idempotency_key(request)
    try:
        created = engine_client_for(request).create_policy(form.fields_body(), idempotency_key=key)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            apply_engine_errors(exc, form, aliases=POLICY_ERROR_PATHS)
            form.add_error(None, f"The Engine rejected the policy: {exc.message}")
            return _render_new(request, form, ratings)
        return _render_new(request, form, ratings, engine_error=panel_for(exc))
    _flash_write(request, "Created", created, key)
    if created.scope == "rating" and created.scope_id:
        return _attach_to_rating(request, created, ratings, key)
    return redirect(_detail_url(created.id))


def _attach_to_rating(
    request: HttpRequest, created: PolicyMutation, ratings: Ratings, key: str
) -> HttpResponse:
    """#49's second write: add the new rating policy to its rating's ``policy_ids`` (a rating
    ``PUT``, recorded on its own). If it fails, the policy stays created and the page says it
    was not added."""
    rating_id = created.scope_id or ""
    label = _rating_label(ratings, rating_id)
    try:
        added = attach_policy(
            engine_client_for(request), rating_id, created.id, idempotency_key=attach_key(key)
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        panel = replace(
            panel_for(exc),
            headline=f"{created.name} was not added to {label}'s policies — "
            f"{panel_for(exc).headline}",
        )
        return render(
            request,
            "console/policies/rating_link_failed.html",
            {"policy": created, "rating_id": rating_id, "rating": label, "engine_error": panel},
        )
    if added is not None:
        messages.success(
            request,
            f"Added {created.name} to {added.name}'s policies — version {added.version}, "
            f"config revision {added.config_revision}, "
            f"idempotency key {added.idempotency_key or attach_key(key)}",
        )
    return redirect(_detail_url(created.id))


def _flash_write(request: HttpRequest, verb: str, written: PolicyMutation, key: str) -> None:
    messages.success(
        request,
        f"{verb} {written.name} — version {written.version}, config revision "
        f"{written.config_revision}, idempotency key {written.idempotency_key or key}",
    )


# --- Edit ---------------------------------------------------------------------------------------


def _snapshot(policy: Policy) -> str:
    return signing.dumps(asdict(policy), salt=SNAPSHOT_SALT, compress=True)


def _posted_policy(request: HttpRequest, policy_id: str) -> Policy:
    """The policy as the posted page read it, else (absent, tampered, another id) a fresh
    ``GET /admin/policies/{id}``. Its fixed fields (scope, rating, kind) are shown from it."""
    try:
        raw: Any = signing.loads(request.POST.get(SNAPSHOT_FIELD, ""), salt=SNAPSHOT_SALT)
    except signing.BadSignature:
        raw = None
    policy = Policy.from_json(raw)
    if policy is None or policy.id != policy_id:
        policy = engine_client_for(request).get_policy(policy_id)
    return policy


def _render_edit(
    request: HttpRequest,
    policy: Policy,
    ratings: Ratings,
    form: PolicyContentForm,
    *,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    (view,) = _views([policy], ratings, _detail_url(policy.id))
    return render(
        request,
        "console/policies/edit.html",
        {
            "row": view,
            "policy": policy,
            "form": form,
            "engine_error": engine_error,
            "minors_warning": MINORS_WARNING,
            "idempotency_key": new_ulid(),  # minted at each render (#43)
            "snapshot": _snapshot(policy),
            "ratings": _signed_ratings(ratings),
        },
    )


def _same_kind(policy: Policy) -> tuple[str, ...]:
    """The rule types an edit offers: those of the policy's kind, which is fixed after create."""
    return tuple(t for t in DEPLOYMENT_RULE_TYPES if kind_of(t) == policy.kind)


def is_retired_refusal(exc: EngineClientError) -> bool:
    """The Engine's 409 for a write to a retired policy (``details.reason = "retired"``; the
    Engine sends it with code ``validation_error``, the schema lists ``config_conflict``)."""
    return (
        isinstance(exc, EngineError)
        and exc.http_status == 409
        and exc.details.get("reason") == "retired"
    )


@require_http_methods(["GET", "HEAD", "POST"])
def policy_edit(request: HttpRequest, policy_id: str) -> HttpResponse:
    """GET: the policy's content as a form; its scope, rating, kind and id read-only. POST:
    validate, then ``PUT`` the whole content and go to the policy's page."""
    if request.method != "POST":
        client = engine_client_for(request)
        policy = client.get_policy(policy_id)
        ratings = _read_ratings(request, client) if policy.scope == "rating" else []
        form = PolicyContentForm(
            initial=policy_initial(policy), rule_types=_same_kind(policy), kept_rule=policy.rule
        )
        return _render_edit(request, policy, ratings, form)

    policy = _posted_policy(request, policy_id)
    ratings = _form_ratings(request)
    form = PolicyContentForm(request.POST, rule_types=_same_kind(policy), kept_rule=policy.rule)
    if not form.is_valid():
        return _render_edit(request, policy, ratings, form)
    key = posted_idempotency_key(request)
    try:
        saved = engine_client_for(request).update_policy(
            policy_id, form.content(), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if is_retired_refusal(exc):
            form.add_error(None, RETIRED_REFUSAL)
            return _render_edit(request, policy, ratings, form)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            apply_engine_errors(exc, form, aliases=POLICY_ERROR_PATHS)
            form.add_error(None, f"The Engine rejected the policy: {exc.message}")
            return _render_edit(request, policy, ratings, form)
        return _render_edit(request, policy, ratings, form, engine_error=panel_for(exc))
    _flash_write(request, "Saved", saved, key)
    return redirect(_detail_url(policy_id))


# --- Retire (confirm page) ----------------------------------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def policy_retire(request: HttpRequest, policy_id: str) -> HttpResponse:
    """GET: the confirm page (no Engine call). POST: ``DELETE /admin/policies/{id}`` and back
    to the list."""
    label = shown_name(request) or policy_id
    minors = policy_id == MINORS_POLICY_ID
    page = ConfirmPage(
        title="Retire policy",
        question="{name} stops applying to every story that uses it.",
        emphasis=label,
        lines=(MINORS_WARNING,) if minors else (),
        confirm_label=MINORS_RETIRE_LABEL if minors else "Retire",
        cancel_url=safe_next(request, _list_url()),
        facts=(("Policy id", policy_id),),
        hidden={"name": shown_name(request)},
        danger=minors,
    )

    def perform(key: str) -> HttpResponse:
        retired = engine_client_for(request).retire_policy(policy_id, idempotency_key=key)
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
