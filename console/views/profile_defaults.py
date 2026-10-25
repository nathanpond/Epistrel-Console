"""Edit the deployment's ``profile_defaults`` (#84; #50 shows them read-only and links here).

``/engine/profile-defaults/edit/`` renders the schema-generated form
(``console.forms.profile_schema_form``, ``defaults`` mode) pre-filled from ``GET
/admin/settings``. Save sends ``PUT /admin/settings {profile_defaults: <merge patch of changed
leaves>}``: objects merge, ``null`` is a value (not a reset), so clearing a stored value is a
form error. Nothing changed sends nothing. There is no impact preview: the Engine validates the
merged document against every profile and story and answers 422 ``invalid_profile`` naming the
profile versions (``details.profiles``) and stories (``details.stories``) that could not run.
The page facts (the stored defaults, the config revision, the schema's digest) travel signed, so
an invalid or unchanged form makes no Engine call. Every save is recorded (#44).
"""

from dataclasses import dataclass, field
from typing import Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from console import twostage
from console.confirm import posted_idempotency_key
from console.engine.choices import StoryLabel, story_labels
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import ProfileSchema
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.forms.profile_schema_form import (
    SCHEMA_CHANGED,
    PlacedStories,
    SchemaSettingsForm,
    schema_digest,
)
from console.ids import new_ulid
from console.profile_schema import get_profile_schema_cached
from console.views.profiles import detail_url

KIND = "profile-defaults"
ROOT = "profile_defaults"
SNAPSHOT_FIELD = "snapshot"
TEMPLATE = "console/engine/profile_defaults.html"
INVALID_PROFILE = "invalid_profile"
EXPIRED = "This form expired — your changes were not saved; check and submit again"
NO_CHANGE = "No changes — nothing was sent."


@dataclass(frozen=True)
class DefaultsFacts:
    defaults: dict[str, Any]
    revision: int
    digest: str

    def signed(self) -> str:
        base = {"defaults": self.defaults, "revision": self.revision, "digest": self.digest}
        return twostage.sign(KIND, "", None, base)

    @classmethod
    def unsigned(cls, token: str) -> DefaultsFacts | None:
        try:
            raw = twostage.unsign(KIND, token).base
        except twostage.ConfirmationExpired:
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("defaults"), dict):
            return None
        revision = raw.get("revision")
        return cls(
            raw["defaults"], revision if isinstance(revision, int) else 0, str(raw.get("digest"))
        )


@dataclass(frozen=True)
class Link:
    text: str
    url: str
    note: str = ""


@dataclass
class Refusal:
    """A 422 ``invalid_profile``: the profile versions and stories that could not run."""

    profiles: list[Link] = field(default_factory=list)
    stories: list[Link] = field(default_factory=list)
    count: int | None = None
    extra: list[tuple[str, str]] = field(default_factory=list)


def _refusal(client: EngineClient, placed: PlacedStories) -> Refusal:
    labels: dict[str, StoryLabel] = story_labels(client, placed.stories) if placed.stories else {}
    profiles = []
    for item in placed.profiles:
        profile_id, _, raw = item.partition("@")
        profiles.append(Link(item, detail_url(profile_id, int(raw) if raw.isdigit() else None)))
    stories = [
        Link(
            labels[s].text if s in labels else s,
            reverse("stories:detail", args=[s]),
            (labels[s].error or "") if s in labels else "",
        )
        for s in placed.stories
    ]
    return Refusal(profiles, stories, placed.count, placed.extra)


def _form(data: Any, schema: ProfileSchema, facts: DefaultsFacts) -> SchemaSettingsForm:
    return SchemaSettingsForm(
        data, schema=schema, root=ROOT, stored=facts.defaults, mode="defaults"
    )


def _render(
    request: HttpRequest,
    facts: DefaultsFacts,
    form: SchemaSettingsForm,
    *,
    engine_error: ErrorPanel | None = None,
    refusal: Refusal | None = None,
) -> HttpResponse:
    context = {
        "facts": facts,
        "form": form,
        "nodes": form.shown(),
        "snapshot": facts.signed(),
        "idempotency_key": new_ulid(),  # minted at each render (#43)
        "engine_error": engine_error,
        "refusal": refusal,
    }
    return render(request, TEMPLATE, context)


def _load(client: EngineClient) -> tuple[DefaultsFacts, ProfileSchema]:
    """``GET /admin/settings`` and the schema (cached); a failed read escapes to #43's panel."""
    settings = client.get_deployment_settings()
    schema = get_profile_schema_cached(client)
    defaults = settings.profile_defaults if isinstance(settings.profile_defaults, dict) else {}
    return DefaultsFacts(defaults, settings.config_revision, schema_digest(schema)), schema


@require_http_methods(["GET", "HEAD", "POST"])
def profile_defaults_edit(request: HttpRequest) -> HttpResponse:
    client = engine_client_for(request)
    if request.method != "POST":
        facts, schema = _load(client)
        return _render(request, facts, _form(None, schema, facts))
    posted = DefaultsFacts.unsigned(request.POST.get(SNAPSHOT_FIELD, ""))
    if posted is None:
        facts, schema = _load(client)
        form = _form(request.POST, schema, facts)
        form.is_valid()
        form.add_error(None, EXPIRED)
        return _render(request, facts, form)
    facts = posted
    schema = get_profile_schema_cached(client)
    form = _form(request.POST, schema, facts)
    if schema_digest(schema) != facts.digest:
        form.is_valid()
        form.add_error(None, SCHEMA_CHANGED)
        fresh = DefaultsFacts(facts.defaults, facts.revision, schema_digest(schema))
        return _render(request, fresh, form)
    if not form.is_valid():
        return _render(request, facts, form)
    patch = form.defaults_patch()
    if not form.is_valid():  # a key removed from a JSON subtree
        return _render(request, facts, form)
    if not patch:
        messages.info(request, NO_CHANGE)
        return redirect(reverse("engine:settings"))
    key = posted_idempotency_key(request)
    try:
        saved = client.update_admin_settings({ROOT: patch}, idempotency_key=key)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            if exc.code == INVALID_PROFILE:
                placed = form.place_invalid_profile(exc)
                return _render(request, facts, form, refusal=_refusal(client, placed))
            if form.place_validation_error(exc):
                return _render(request, facts, form)
        return _render(request, facts, form, engine_error=panel_for(exc))
    messages.success(
        request,
        f"Saved profile defaults — config revision {saved.config_revision}, "
        f"idempotency key {saved.idempotency_key or key}",
    )
    return redirect(reverse("engine:settings"))
