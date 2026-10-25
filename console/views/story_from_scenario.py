"""Start a story from a scenario and see what was applied (#75).

``/stories/new/from-scenario/``:

- **Step 1** (GET, no ``scenario``): a select of published scenarios (companions included and
  marked), a ``q`` search and cursor paging; "Choose" (``?pick=<id>``) adds the version select
  (blank = latest) and "Continue" goes to step 2 (``?scenario=<id>&version=<v>``).
- **Step 2** (GET ``?scenario=``): the scenario read in ``full`` at that version (none: the
  latest published) and one row per role (``console.forms.story_from_scenario``). The version
  shown is pinned in a hidden field; every POST re-reads the scenario at it (no role list
  travels in a signed field). A read that fails on POST sends nothing: the error panel shows
  with the input kept and a Retry button.
- **POST** ``op``: ``load_personas`` re-renders with each player principal's personas;
  ``create`` validates and sends ``POST /stories``; ``confirm`` ("Create anyway") resends the
  signed body with ``ignore_warnings: true``; ``back`` rebuilds step 2 from it. Only ``create``
  and ``confirm`` call ``POST /stories``. Every render mints a new idempotency key (#43).

Answers: 201 → the creation report page; 200 ``applied: false`` → the not-created page,
rendered in this response with the signed payload (#64 convention 3); 422 → the form again
with the refusal placed and ``details.report`` below it; anything else → the error panel above
the form.

``/stories/<id>/creation-report/`` renders the stored #44 row of the 201 that made the story
(``console.creation_report``); the scenario's role labels and the profile's name are re-read
live, each degrading on its own.
"""

from collections.abc import Callable, Mapping
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods, require_safe

from console import twostage
from console.acting import acting_principal
from console.confirm import posted_idempotency_key
from console.creation_report import creation_row, shown_creation
from console.engine.choices import (
    PROFILES_MAX,
    TOO_MANY,
    engine_profile_choices,
    error_text,
    published_character_choices,
    rating_choices,
)
from console.engine.client import CATALOG_PAGE, EngineClient, EngineClientError, EngineError
from console.engine.models import (
    CatalogCharacterCard,
    CatalogVersion,
    CreationReport,
    Scenario,
    StoryCreated,
)
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.entities import label
from console.forms.principal import clean_principal, remember_principals
from console.forms.story_from_scenario import (
    DEFAULT,
    LOCKED,
    Lookups,
    PersonaOptions,
    RoleName,
    StoryFromScenarioForm,
    data_from_body,
    is_player,
    player_prefix,
    slot_prefix,
)
from console.ids import is_ulid, new_ulid

KIND = "story_from_scenario"  # the two-stage payload's kind
OP_LOAD_PERSONAS = "load_personas"
OP_CREATE = "create"
OP_CONFIRM = "confirm"
OP_BACK = "back"
ARCHIVED = "archived"
COMPANION_SUFFIX = " (companion)"
PERSONAS_PAGE = 200
NOT_CREATED = "Not created — the engine profile leaves recommended capabilities unmet"
LITE_NOTICE = (
    "Lite genesis: seeds are rendered as scenario text; director guidance becomes author "
    "notes; fact consistency is not checked"
)
NOT_AVAILABLE = "Not available — this story was not created from this Console"
UNREADABLE = "Not available — the stored creation response could not be read"
TEMPLATE = "console/stories/from_scenario.html"


def _pick_url() -> str:
    return reverse("stories:from-scenario")


def _step2_url(scenario_id: str, version: object = None) -> str:
    query = {"scenario": scenario_id, "version": "" if version is None else str(version)}
    return f"{_pick_url()}?{urlencode(query)}"


# --- step 1 --------------------------------------------------------------------------------------


def _scenario_label(name: str | None, scenario_id: str, companion: bool) -> str:
    shown = f"{label(name, scenario_id)} (…{scenario_id[-6:]})"
    return f"{shown}{COMPANION_SUFFIX}" if companion else shown


def _pick(request: HttpRequest) -> HttpResponse:
    client = engine_client_for(request)
    q = request.GET.get("q", "").strip()
    cursor = request.GET.get("cursor", "").strip()
    pick = request.GET.get("pick", "").strip()
    context: dict[str, Any] = {"q": q, "pick": pick}
    try:
        items, next_cursor = client.list_scenarios(
            q=q or None,
            status="published",
            include_companions=True,
            cursor=cursor or None,
            limit=CATALOG_PAGE,
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context["engine_error"] = panel_for(exc)
        return render(request, "console/stories/from_scenario_pick.html", context)
    options = [
        (item.id, _scenario_label(item.name, item.id, item.companion_of is not None))
        for item in items
    ]
    if pick and pick not in {value for value, _ in options}:
        options.insert(0, (pick, pick))
    context["options"] = options
    if next_cursor:
        context["next_url"] = f"?{urlencode({'q': q, 'cursor': next_cursor})}"
    if pick:
        versions: list[CatalogVersion] = []
        try:
            versions = client.list_scenario_versions(pick)
        except EngineClientError as exc:
            log_engine_error(request, exc)
            context["versions_error"] = error_text(exc)
        context["versions"] = versions
    return render(request, "console/stories/from_scenario_pick.html", context)


# --- lookups ----------------------------------------------------------------------------------


def _persona_options(client: EngineClient, principal: str) -> PersonaOptions:
    """The principal's saved personas, "name (age) …<last 6>", following cursors up to
    :data:`PROFILES_MAX` items ("All X" rule)."""
    try:
        owner = clean_principal(principal)
    except ValidationError:
        return PersonaOptions(note="Enter a valid principal, then Load personas")
    choices: list[tuple[str, str]] = []
    cursor: str | None = None
    try:
        while True:
            items, cursor = client.list_personas(owner=owner, cursor=cursor, limit=PERSONAS_PAGE)
            choices.extend((p.id, f"{p.name} ({p.age}) …{p.id[-6:]}") for p in items)
            if len(choices) > PROFILES_MAX:
                return PersonaOptions(note=TOO_MANY)
            if not cursor:
                break
    except EngineClientError as exc:
        return PersonaOptions(note=f"Personas unavailable: {error_text(exc)} — enter the id")
    return PersonaOptions(choices)


def _role_names(client: EngineClient, scenario: Scenario) -> dict[str, RoleName]:
    """Each locked or default role's character, one card read per role at its pinned version
    (else the latest), on the fan-out pool."""
    roles = {
        role.id: role.character
        for role in scenario.cast.roles
        if role.character is not None and role.fill in (LOCKED, DEFAULT)
    }

    def read(role_id: str) -> CatalogCharacterCard:
        character = roles[role_id]
        assert character is not None  # noqa: S101 - filtered above
        return client.get_catalog_character_card(character.catalog_id, version=character.version)

    results = fan_out(read, list(roles))
    names: dict[str, RoleName] = {}
    for role_id, character in roles.items():
        assert character is not None  # noqa: S101 - filtered above
        result = results[role_id]
        if isinstance(result, EngineClientError):
            names[role_id] = RoleName(
                character.catalog_id, character.version, character.catalog_id, error_text(result)
            )
        else:
            names[role_id] = RoleName(
                character.catalog_id,
                character.version,
                label(result.name, character.catalog_id),
                archived=result.status == ARCHIVED,
            )
    return names


def _lookups(
    client: EngineClient, scenario: Scenario, data: Mapping[str, Any] | None, acting: str
) -> Lookups:
    current = [
        str(data.get(f"{slot_prefix(role.id)}catalog_id") or "")
        for role in scenario.cast.roles
        if data is not None and not is_player(role)
    ]
    personas: dict[str, PersonaOptions] = {}
    by_principal: dict[str, PersonaOptions] = {}
    for role in scenario.cast.roles:
        if not is_player(role):
            continue
        principal = acting
        if data is not None:
            principal = str(data.get(f"{player_prefix(role.id)}principal") or "")
        if principal not in by_principal:
            by_principal[principal] = _persona_options(client, principal)
        personas[role.id] = by_principal[principal]
    return Lookups(
        ratings=rating_choices(client),
        profiles=engine_profile_choices(client),
        characters=published_character_choices(client, current=current),
        personas=personas,
        role_names=_role_names(client, scenario),
    )


def _form(
    request: HttpRequest,
    client: EngineClient,
    scenario: Scenario,
    data: Mapping[str, Any] | None,
) -> StoryFromScenarioForm:
    acting = acting_principal(request)
    lookups = _lookups(client, scenario, data, acting)
    return StoryFromScenarioForm(data, scenario=scenario, lookups=lookups, acting=acting)


# --- step 2 ---------------------------------------------------------------------------------------


def _render_step2(
    request: HttpRequest,
    scenario: Scenario,
    form: StoryFromScenarioForm,
    *,
    show_errors: bool = False,
    engine_error: ErrorPanel | None = None,
    refusal: CreationReport | None = None,
) -> HttpResponse:
    rating_names = dict(form.lookups.ratings.choices)
    context: dict[str, Any] = {
        "scenario": scenario,
        "scenario_name": label(scenario.public_card.name, scenario.id),
        "form": form,
        "rows": form.role_rows(rating_names),
        "overrides": form.override_fields(),
        "bounds": scenario.creation_rules.bounds,
        "ratings_note": form.lookups.ratings.unavailable_text,
        "show_errors": show_errors,
        "engine_error": engine_error,
        "refusal": shown_creation(refusal) if refusal is not None else None,
        "archived": scenario.status == ARCHIVED,
        "idempotency_key": new_ulid(),
        "pick_url": _pick_url(),
        "scenario_url": reverse("scenarios:detail", args=[scenario.id]),
    }
    return render(request, TEMPLATE, context)


def _step2_get(request: HttpRequest, scenario_id: str, version: str) -> HttpResponse:
    client = engine_client_for(request)
    try:
        scenario = client.get_scenario(scenario_id, version=version or None)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        context = {"engine_error": panel_for(exc), "pick_url": _pick_url()}
        return render(request, "console/stories/from_scenario_error.html", context)
    return _render_step2(request, scenario, _form(request, client, scenario, None))


def _retry(request: HttpRequest, exc: EngineClientError) -> HttpResponse:
    """The pinned scenario could not be re-read: nothing is sent; the panel shows with every
    posted value kept (as hidden inputs) and a Retry button."""
    log_engine_error(request, exc)
    kept = [
        (key, value)
        for key, values in request.POST.lists()
        if key not in ("csrfmiddlewaretoken", "idempotency_key")
        for value in values
    ]
    op = request.POST.get("op", OP_CREATE)
    context = {
        "engine_error": panel_for(exc),
        "pick_url": _pick_url(),
        "kept": kept,
        "idempotency_key": new_ulid(),
        # "Create anyway" and "Back" retry themselves; a create comes back to the form unsent.
        "retry_op": op if op in (OP_CONFIRM, OP_BACK) else OP_LOAD_PERSONAS,
    }
    return render(request, "console/stories/from_scenario_error.html", context)


def _send(
    request: HttpRequest,
    client: EngineClient,
    scenario: Scenario,
    body: dict[str, Any],
    key: str,
    form_for_errors: Callable[[], StoryFromScenarioForm],
) -> HttpResponse:
    """``POST /stories`` with ``body`` and what its answer leads to."""
    try:
        created = client.create_story_from_scenario(
            body, scenario_id=scenario.id, idempotency_key=key
        )
    except EngineError as exc:
        log_engine_error(request, exc)
        form = form_for_errors()
        if exc.http_status == 422:
            report = form.place_refusal(exc)
            return _render_step2(request, scenario, form, show_errors=True, refusal=report)
        return _render_step2(request, scenario, form, show_errors=True, engine_error=panel_for(exc))
    except EngineClientError as exc:
        log_engine_error(request, exc)
        form = form_for_errors()
        return _render_step2(request, scenario, form, show_errors=True, engine_error=panel_for(exc))
    if created.applied and created.story_id:
        messages.success(
            request,
            f"Created {body.get('name') or label(scenario.public_card.name, scenario.id)} from "
            f"the scenario — story {created.story_id}, idempotency key "
            f"{created.idempotency_key or key}",
        )
        return redirect("stories:creation-report", story_id=created.story_id)
    sent = {k: v for k, v in body.items() if k != "ignore_warnings"}
    context: dict[str, Any] = {
        "scenario": scenario,
        "scenario_name": label(scenario.public_card.name, scenario.id),
        "heading": NOT_CREATED,
        "created": created,
        "shown": shown_creation(created.report) if created.report is not None else None,
        "impact": created.impact,
        "token": twostage.sign(KIND, scenario.id, sent, base=scenario.version),
        "idempotency_key": new_ulid(),  # minted when the report renders (#43, convention 3)
        "confirm_url": _pick_url(),
        "hidden": [("scenario", scenario.id), ("version", str(scenario.version))],
    }
    return render(request, "console/stories/not_created.html", context)


def _from_payload(request: HttpRequest, op: str) -> HttpResponse:
    """ "Create anyway" and "Back": the signed body, never the form again."""
    try:
        payload = twostage.unsign(KIND, request.POST.get(twostage.CONFIRM_FIELD, ""))
    except twostage.ConfirmationExpired:
        messages.error(request, twostage.EXPIRED_TEXT)
        scenario_id = request.POST.get("scenario", "")
        if is_ulid(scenario_id):
            return redirect(_step2_url(scenario_id, request.POST.get("version", "")))
        return redirect(_pick_url())
    body: dict[str, Any] = payload.body if isinstance(payload.body, dict) else {}
    client = engine_client_for(request)
    try:
        scenario = client.get_scenario(payload.target_id, version=payload.base)
    except EngineClientError as exc:
        return _retry(request, exc)
    data = QueryDict(mutable=True)
    data.update(data_from_body(body, scenario))
    if op == OP_BACK:
        return _render_step2(request, scenario, _form(request, client, scenario, data))

    def rebuilt() -> StoryFromScenarioForm:
        form = _form(request, client, scenario, data)
        form.is_valid()  # cleaned, so a refusal can be added to it
        return form

    key = posted_idempotency_key(request)
    return _send(request, client, scenario, {**body, "ignore_warnings": True}, key, rebuilt)


@require_http_methods(["GET", "HEAD", "POST"])
def from_scenario(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        scenario_id = request.GET.get("scenario", "").strip()
        if not scenario_id:
            return _pick(request)
        return _step2_get(request, scenario_id, request.GET.get("version", "").strip())
    op = request.POST.get("op", OP_CREATE)
    if op in (OP_CONFIRM, OP_BACK):
        return _from_payload(request, op)
    scenario_id = request.POST.get("scenario", "")
    version = request.POST.get("version", "") or None
    client = engine_client_for(request)
    try:
        scenario = client.get_scenario(scenario_id, version=version)
    except EngineClientError as exc:
        return _retry(request, exc)
    form = _form(request, client, scenario, request.POST)
    if op != OP_CREATE:  # Load personas: the same input, the principal's personas listed
        return _render_step2(request, scenario, form)
    if not form.is_valid():
        return _render_step2(request, scenario, form, show_errors=True)
    remember_principals(form)
    key = posted_idempotency_key(request)
    return _send(request, client, scenario, form.body(), key, lambda: form)


# --- the creation report --------------------------------------------------------------------------


@require_safe
def creation_report(request: HttpRequest, story_id: str) -> HttpResponse:
    context: dict[str, Any] = {"story_id": story_id, "lite_notice": LITE_NOTICE}
    row = creation_row(story_id)
    created = StoryCreated.from_json(row.response) if row is not None else None
    if row is None or created is None or created.report is None:
        context["unavailable"] = NOT_AVAILABLE if row is None else UNREADABLE
        return render(request, "console/stories/creation_report.html", context)
    client = engine_client_for(request)
    role_labels: dict[str, str] = {}
    origin = created.scenario
    if origin is not None:
        context["scenario_url"] = (
            f"{reverse('scenarios:detail', args=[origin.id])}?version={origin.version}"
        )
        try:
            scenario = client.get_scenario(origin.id, version=origin.version)
        except EngineClientError as exc:
            log_engine_error(request, exc)
            context["scenario_error"] = error_text(exc)
        else:
            context["scenario_name"] = label(scenario.public_card.name, origin.id)
            role_labels = {role.id: role.public_label for role in scenario.cast.roles}
    profile = created.engine_profile
    if profile is not None:
        try:
            context["profile_name"] = client.get_engine_profile(profile.id).name
        except EngineClientError as exc:
            log_engine_error(request, exc)
            context["profile_error"] = error_text(exc)
    context.update(
        {
            "created": created,
            "row": row,
            "shown": shown_creation(created.report, role_labels=role_labels),
            "play_url": reverse("stories:detail", args=[story_id]),
        }
    )
    return render(request, "console/stories/creation_report.html", context)
