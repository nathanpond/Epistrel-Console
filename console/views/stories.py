"""Stories (#45): every story in the Engine, a new Lite story with its cast, and archive,
unarchive and delete behind confirm pages.

``/stories/<id>/`` is the play view (#48, ``console/views/play.py``); its header shows these
actions.
"""

from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.core import signing
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
from console.engine.models import StorySummary
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms import (
    CAST_MAX,
    CHARACTERS_PREFIX,
    BaseCharacterFormSet,
    CharacterFormSet,
    StoryFilterForm,
    StoryForm,
    story_spec,
)
from console.formset_rows import edit_rows, wants_edit
from console.ids import new_ulid

EMPTY = "—"
PAUSED_NOTE = "Paused while a check runs"
RATINGS_FIELD = "rating_choices"  # the select's options, signed, carried across re-renders
RATINGS_SALT = "console.stories.rating_choices"
# A 422 at these paths, or with this code, belongs beside the viewpoint field.
VIEWPOINT_ALIASES = {
    "settings.default_output_viewpoint": "viewpoint",
    "settings": "viewpoint",
    "viewpoint_unresolved": "viewpoint",
}


def story_label(name: str | None, story_id: str) -> str:
    """How a story is named in wording and flashes: its name, else its id."""
    return name if name else story_id


# --- List ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoryRow:
    story: StorySummary
    cast_size: int | None  # None when the story's own read failed
    actions_query: str  # ?name=…&next=… for the action links


def _cast_size(client: EngineClient, request: HttpRequest, story_id: str) -> int | None:
    """``GET /stories/{id}``'s cast size; a failed read degrades to "—" for that row only."""
    try:
        return len(client.get_story(story_id).characters)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


@require_safe
def story_list(request: HttpRequest) -> HttpResponse:
    """Every story ``GET /stories`` returns, archived included, in the Engine's order. The list
    item has no cast, so each row's cast size comes from ``GET /stories/{id}`` (sequential)."""
    filters = StoryFilterForm(request.GET)
    status = filters.chosen_status()
    client = engine_client_for(request)
    stories = client.list_stories(status=status)
    here = request.get_full_path()
    rows = [
        StoryRow(story, _cast_size(client, request, story.id), confirm_query(story.name, here))
        for story in stories
    ]
    empty_text = f"No {status} stories." if status else "No stories yet"
    return render(
        request,
        "console/stories/list.html",
        {"rows": rows, "filters": filters, "status": status or "", "empty_text": empty_text},
    )


# --- New story ----------------------------------------------------------------------------------


type Choices = list[tuple[str, str]]  # (rating id, name) for the select


def _rating_choices(request: HttpRequest) -> tuple[Choices, str]:
    """The non-retired ratings and the deployment default (two Engine reads)."""
    client = engine_client_for(request)
    ratings = [(r.id, r.name) for r in client.list_content_ratings()]
    return ratings, client.get_deployment_settings().default_content_rating_id


def _signed_ratings(ratings: Choices) -> str:
    return signing.dumps([list(pair) for pair in ratings], salt=RATINGS_SALT, compress=True)


def _posted_ratings(request: HttpRequest) -> Choices | None:
    """The select's options as the page carried them, or ``None`` when absent or tampered."""
    try:
        pairs = signing.loads(request.POST.get(RATINGS_FIELD, ""), salt=RATINGS_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(pairs, list) or not all(isinstance(p, list) and len(p) == 2 for p in pairs):
        return None
    return [(str(rating_id), str(name)) for rating_id, name in pairs]


def _cast_names(characters: BaseCharacterFormSet) -> list[str]:
    names = []
    for form in characters.forms:
        value = form["name"].value()
        if isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())
    return names


def _render_new(
    request: HttpRequest,
    story: StoryForm,
    characters: BaseCharacterFormSet,
    ratings: Choices,
    *,
    show_errors: bool,
    engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    context: dict[str, Any] = {
        "form": story,
        "characters": characters,
        "cast_names": _cast_names(characters),
        "can_add": len(characters.forms) < CAST_MAX,
        "can_remove": len(characters.forms) > 1,
        "show_errors": show_errors,
        "engine_error": engine_error,
        "idempotency_key": new_ulid(),  # minted at each render (#43)
        "rating_choices": _signed_ratings(ratings),
    }
    return render(request, "console/stories/new.html", context)


@require_http_methods(["GET", "HEAD", "POST"])
def story_new(request: HttpRequest) -> HttpResponse:
    """GET: the empty form (rating preselected to the deployment default). POST "add" /
    "remove": the same form with a row more or less, nothing validated or sent. POST "create":
    validate, then ``POST /stories`` and redirect to the new story's page."""
    if request.method != "POST":
        ratings, default = _rating_choices(request)
        story = StoryForm(ratings=ratings, initial={"content_rating_id": default})
        characters = CharacterFormSet(prefix=CHARACTERS_PREFIX)
        return _render_new(request, story, characters, ratings, show_errors=False)

    posted = _posted_ratings(request)
    ratings = posted if posted is not None else _rating_choices(request)[0]
    if wants_edit(request.POST):
        data = edit_rows(request.POST, CHARACTERS_PREFIX, max_rows=CAST_MAX)
        story = StoryForm(data, ratings=ratings)
        characters = CharacterFormSet(data, prefix=CHARACTERS_PREFIX)
        return _render_new(request, story, characters, ratings, show_errors=False)

    story = StoryForm(request.POST, ratings=ratings)
    characters = CharacterFormSet(request.POST, prefix=CHARACTERS_PREFIX)
    story_ok = story.is_valid()
    if not (characters.is_valid() and story_ok):
        return _render_new(request, story, characters, ratings, show_errors=True)

    key = posted_idempotency_key(request)
    try:
        created = engine_client_for(request).create_story(
            story_spec(story, characters), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if (
            isinstance(exc, EngineError)
            and exc.http_status == 422
            and exc.code != "guardrail_violation"
        ):
            apply_engine_errors(
                exc,
                story,
                formsets={CHARACTERS_PREFIX: characters.sent_forms()},
                aliases=VIEWPOINT_ALIASES,
            )
            story.add_error(None, f"The Engine rejected the story: {exc.message}")
            return _render_new(request, story, characters, ratings, show_errors=True)
        return _render_new(
            request, story, characters, ratings, show_errors=True, engine_error=panel_for(exc)
        )
    assert created.story_id is not None  # noqa: S101 - create_story refuses a missing id
    label = story_label(story.cleaned_data["name"], created.story_id)
    messages.success(
        request,
        f"Created {label} — revision {created.timeline_revision}, idempotency key "
        f"{created.idempotency_key or key}",
    )
    return redirect("stories:detail", story_id=created.story_id)


# --- Archive, unarchive, delete (confirm pages) -----------------------------------------------


def _list_url() -> str:
    return reverse("stories:list")


@require_http_methods(["GET", "HEAD", "POST"])
def story_archive(request: HttpRequest, story_id: str) -> HttpResponse:
    name = shown_name(request)
    label = story_label(name, story_id)
    back = safe_next(request, _list_url())
    page = ConfirmPage(
        title="Archive story",
        question="Archive {name}? Turns will be refused until it is unarchived.",
        emphasis=label,
        confirm_label="Archive",
        cancel_url=back,
        facts=(("Story id", story_id),),
        hidden={"name": name},
    )

    def perform(key: str) -> HttpResponse:
        result = engine_client_for(request).archive_story(story_id, idempotency_key=key)
        messages.success(
            request,
            f"Archived {label} — status {result.status}, revision {result.timeline_revision}, "
            f"idempotency key {result.idempotency_key or key}",
        )
        return redirect(back)

    return confirm_action(request, page, perform)


@require_http_methods(["GET", "HEAD", "POST"])
def story_unarchive(request: HttpRequest, story_id: str) -> HttpResponse:
    name = shown_name(request)
    label = story_label(name, story_id)
    back = safe_next(request, _list_url())
    page = ConfirmPage(
        title="Unarchive story",
        question="Unarchive {name}? Turns will be accepted again.",
        emphasis=label,
        confirm_label="Unarchive",
        cancel_url=back,
        facts=(("Story id", story_id),),
        hidden={"name": name},
    )

    def perform(key: str) -> HttpResponse:
        result = engine_client_for(request).unarchive_story(story_id, idempotency_key=key)
        messages.success(
            request,
            f"Unarchived {label} — status {result.status}, revision "
            f"{result.timeline_revision}, idempotency key {result.idempotency_key or key}",
        )
        return redirect(back)

    return confirm_action(request, page, perform)


@require_http_methods(["GET", "HEAD", "POST"])
def story_delete(request: HttpRequest, story_id: str) -> HttpResponse:
    name = shown_name(request)
    label = story_label(name, story_id)
    page = ConfirmPage(
        title="Delete story",
        question="Delete {name}?",
        emphasis=label,
        lines=("This permanently deletes the story and all its history in the Engine.",),
        confirm_label="Delete",
        cancel_url=safe_next(request, _list_url()),
        facts=(("Story id", story_id),),
        hidden={"name": name},
        danger=True,
    )

    def perform(key: str) -> HttpResponse:
        engine_client_for(request).delete_story(story_id, idempotency_key=key)
        messages.success(request, f"Deleted {label}")
        return redirect("stories:list")  # the story's own page is gone

    return confirm_action(request, page, perform)
