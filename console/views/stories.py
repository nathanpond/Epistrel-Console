"""Stories (#45): every story in the Engine, a new Lite story with its cast, and archive,
unarchive and delete behind confirm pages.

``/stories/<id>/`` is the play view (#48, ``console/views/play.py``); its header shows these
actions.
"""

from dataclasses import dataclass
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
from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import StorySummary
from console.engine.pool import fan_out
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.entities import entity_url
from console.forms import (
    CAST_MAX,
    CHARACTERS_PREFIX,
    BaseCharacterFormSet,
    CharacterFormSet,
    StoryFilterForm,
    StoryForm,
    story_spec,
)
from console.forms.stories import CREATION_ERROR_PATHS
from console.formset_rows import edit_rows, wants_edit
from console.held import HELD_PARAM, HELD_STATUS
from console.ids import new_ulid

EMPTY = "—"
PAUSED_NOTE = "Paused while the check runs"  # #80's wording
RATINGS_FIELD = "rating_choices"  # the select's options, signed, carried across re-renders
RATINGS_SALT = "console.stories.rating_choices"


def story_label(name: str | None, story_id: str) -> str:
    """How a story is named in wording and flashes: its name, else its id."""
    return name if name else story_id


# --- List ---------------------------------------------------------------------------------------


HELD_NAME = "(held — erasure pending)"  # a held story's list name when the Engine sends none
HELD_STATUS_TEXT = "Held (erasure pending)"


@dataclass(frozen=True)
class StoryRow:
    story: StorySummary
    cast_size: int | None  # None when the story's own read failed (or was skipped: held)
    actions_query: str  # ?name=…&next=… for the action links
    profile_version: int | None = None  # ?profile= only (#78): the version the story runs on

    @property
    def held(self) -> bool:
        """Held by a pending erasure (#87): every read of it is 503 ``erasure_pending``."""
        return self.story.status == HELD_STATUS


def _cast_size(client: EngineClient, request: HttpRequest, story_id: str) -> int | None:
    """``GET /stories/{id}``'s cast size; a failed read degrades to "—" for that row only."""
    try:
        return len(client.get_story(story_id).characters)
    except EngineClientError as exc:
        log_engine_error(request, exc)
        return None


def _list_row(
    client: EngineClient, request: HttpRequest, story: StorySummary, here: str
) -> StoryRow:
    """A held story's row skips its ``GET /stories/{id}`` back-fill (it would answer 503) and
    its actions carry ``held=1`` (the delete confirm's id-only wording)."""
    if story.status == HELD_STATUS:
        query = f"{confirm_query(story.name, here)}&{HELD_PARAM}=1"
        return StoryRow(story, None, query)
    return StoryRow(story, _cast_size(client, request, story.id), confirm_query(story.name, here))


@require_safe
def story_list(request: HttpRequest) -> HttpResponse:
    """Every story ``GET /stories`` returns, archived included, in the Engine's order. The list
    item has no cast, so each row's cast size comes from ``GET /stories/{id}`` (sequential).
    ``?profile=<id>`` narrows it to the stories using that engine profile (#78)."""
    filters = StoryFilterForm(request.GET)
    status = filters.chosen_status()
    client = engine_client_for(request)
    profile_id = request.GET.get(PROFILE_PARAM, "").strip()
    if profile_id:
        return _stories_using_profile(request, client, filters, status, profile_id)
    stories = client.list_stories(status=status)
    here = request.get_full_path()
    rows = [_list_row(client, request, story, here) for story in stories]
    empty_text = (
        f"No {filters.status_label(status).lower()} stories." if status else "No stories yet"
    )
    return render(
        request,
        "console/stories/list.html",
        {
            "rows": rows,
            "filters": filters,
            "status": status or "",
            "empty_text": empty_text,
            "held_name": HELD_NAME,
            "held_status": HELD_STATUS_TEXT,
        },
    )


# --- Stories using an engine profile (#78) ------------------------------------------------------

PROFILE_PARAM = "profile"
PROFILE_PAGE = 50
PROFILE_UNREAD = "Couldn't read this profile"
NO_PROFILE_STORIES = "No stories use this profile."


@dataclass(frozen=True)
class Unchecked:
    """A story whose ``GET /stories/{id}`` failed or was cut by the deadline."""

    story: StorySummary
    reason: str


def _stories_using_profile(
    request: HttpRequest,
    client: EngineClient,
    filters: StoryFilterForm,
    status: str | None,
    profile_id: str,
) -> HttpResponse:
    """The stories (with ``status``, when chosen) running under ``profile_id`` at any version.
    ``StorySummaryBody`` has no profile, so each story is read (``GET /stories/{id}``) on the
    fan-out pool under its 10 s deadline; a failed or cut read is counted and listed, not
    matched (nathanpond/Epistrel#527 asks for an Engine filter). Paged Console-side."""
    stories = client.list_stories(status=status)
    profile_name: str | None = None
    profile_error = ""
    try:
        profile_name = client.get_engine_profile(profile_id).name
    except EngineClientError as exc:
        log_engine_error(request, exc)
        profile_error = PROFILE_UNREAD
    results = fan_out(client.get_story, [story.id for story in stories])
    here = request.get_full_path()
    rows: list[StoryRow] = []
    unchecked: list[Unchecked] = []
    for summary in stories:
        read = results.get(summary.id)
        if isinstance(read, EngineClientError) or read is None:
            reason = error_text(read) if isinstance(read, EngineClientError) else "not read"
            unchecked.append(Unchecked(summary, reason))
            continue
        if read.engine_profile.id != profile_id:
            continue
        rows.append(
            StoryRow(
                summary,
                len(read.characters),
                confirm_query(summary.name, here),
                read.engine_profile.version,
            )
        )
    page = Paginator(rows, PROFILE_PAGE).get_page(request.GET.get("page"))
    keep = request.GET.copy()
    keep.pop("page", None)
    clear = request.GET.copy()
    clear.pop(PROFILE_PARAM, None)
    clear.pop("page", None)
    context = {
        "rows": list(page.object_list),
        "page": page,
        "page_query": keep.urlencode(),
        "filters": filters,
        "status": status or "",
        "empty_text": NO_PROFILE_STORIES,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "profile_error": profile_error,
        "profile_url": entity_url("engine_profile", profile_id),
        "clear_query": clear.urlencode(),
        "unchecked": unchecked,
        "held_name": HELD_NAME,
        "held_status": HELD_STATUS_TEXT,
    }
    return render(request, "console/stories/list.html", context)


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
                aliases=CREATION_ERROR_PATHS,
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


def _held_delete(request: HttpRequest) -> bool:
    """The list row (or the held page) asked for a held story's id-only wording (#87)."""
    source = request.POST if request.method == "POST" else request.GET
    return source.get(HELD_PARAM) == "1"


@require_http_methods(["GET", "HEAD", "POST"])
def story_delete(request: HttpRequest, story_id: str) -> HttpResponse:
    held = _held_delete(request)
    name = "" if held else shown_name(request)
    label = story_label(name, story_id)
    hidden = {"name": name, HELD_PARAM: "1"} if held else {"name": name}
    page = ConfirmPage(
        title="Delete story",
        question="Delete held story {name}?" if held else "Delete {name}?",
        emphasis=label,
        lines=("This permanently deletes the story and all its history in the Engine.",),
        confirm_label="Delete",
        cancel_url=safe_next(request, _list_url()),
        facts=(("Story id", story_id),),
        hidden=hidden,
        danger=True,
    )

    def perform(key: str) -> HttpResponse:
        engine_client_for(request).delete_story(story_id, idempotency_key=key)
        messages.success(request, f"Deleted {label}")
        return redirect("stories:list")  # the story's own page is gone

    return confirm_action(request, page, perform)
