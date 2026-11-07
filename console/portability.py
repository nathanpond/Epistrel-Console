"""Story portability in M7 (#295): importing stories, the export/import/prune links and the
bundle's file name.

**E1 — the import rule.** Every M7 page that words an importing story, links an export, an
import job or the prune page, or names a downloaded bundle imports these; none defines its own.

**E10 — importing stories.** While a story is being imported the Engine answers ~30 reads and
every write of it with 409 ``story_importing`` (``details``: ``story_id``, ``job_id``, ``stage``,
``progress {done, total}``, read through #292's ``ImportingDetails``).

- :data:`IMPORTING_TEXT` and :func:`importing_refusal` word it: "<IMPORTING_TEXT> (<humanised
  stage>, <done>/<total>)", a missing stage or count dropped (E7's count rule: both ints, total >
  0) and the parenthesis left out when both are; ``url`` is :func:`import_job_url` ("" while
  ``engine:import-job`` is unregistered, and the page renders plain text).
- ``console.wording.write_refusal`` maps ``story_importing`` here, so every existing write words
  it with no change of its own.
- ``console.middleware.ImportingStoryMiddleware`` turns the 409 from any view whose URL has a
  ``story_id`` into ``templates/console/stories/importing.html`` (status 409: the story id, the
  text and link, and "Retry"); an HTMX request gets ``HX-Redirect`` to the story page.
- The story list's note :data:`IMPORTING_ROW_NOTE` is #293's (``console.forms.stories``, the one
  status wording), re-exported here.

**E6 — URL helpers** (D19: ``None`` while the name is unregistered, or for a blank or non-string
id): :func:`export_url` (``stories:export``), :func:`export_job_url` (``stories:export-job``),
:func:`import_job_url` (``engine:import-job``) and :func:`prune_url` (``stories:prune``).

**The bundle's name.** :func:`bundle_filename` is "<slug of the name, else of the id>-<YYYYMMDD>
.epistrel.tgz", ASCII only (``django.utils.text.slugify``), dated today in UTC (the download's
date: ``ExportJob`` carries no export time); an empty slug falls back to the id's, then "story".

**E18 — no export or import list.** The Engine lists neither; past jobs are the Console's own
#44 rows (``console.jobs.recent_jobs``).
"""

from datetime import UTC
from typing import Any, Protocol, TypeGuard

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import slugify

from console.engine.client import EngineClientError, EngineError
from console.engine.models import ImportingDetails
from console.engine_errors import is_htmx, log_engine_error
from console.forms.stories import IMPORTING_ROW_NOTE
from console.wording import WriteRefusal, humanised

__all__ = [
    "BUNDLE_SUFFIX",
    "IMPORTING_CODE",
    "IMPORTING_ROW_NOTE",
    "IMPORTING_TEXT",
    "bundle_filename",
    "export_job_url",
    "export_url",
    "import_job_url",
    "importing_refusal",
    "importing_text",
    "is_importing",
    "prune_url",
    "render_importing",
]

IMPORTING_CODE = "story_importing"
IMPORTING_TEXT = "This story is being imported — its pages open once the import ends"
BUNDLE_SUFFIX = ".epistrel.tgz"
FALLBACK_SLUG = "story"


def is_importing(exc: object) -> TypeGuard[EngineError]:
    """A 409 ``story_importing``."""
    return isinstance(exc, EngineError) and exc.http_status == 409 and exc.code == IMPORTING_CODE


def importing_text(details: ImportingDetails | None) -> str:
    """:data:`IMPORTING_TEXT` with "(<stage>, <done>/<total>)", each part only when known."""
    parts: list[str] = []
    if details is not None and details.stage and details.stage.strip():
        parts.append(humanised(details.stage))
    progress = details.progress if details is not None else None
    if progress is not None and progress.total > 0:
        parts.append(f"{progress.done:,}/{progress.total:,}")
    return f"{IMPORTING_TEXT} ({', '.join(parts)})" if parts else IMPORTING_TEXT


def importing_refusal(exc: EngineClientError) -> WriteRefusal:
    """E10's line for a 409 ``story_importing``, linked to its import job when that page is
    registered."""
    details = ImportingDetails.from_details(exc.details if isinstance(exc, EngineError) else None)
    url = import_job_url(details.job_id) if details is not None else None
    return WriteRefusal(importing_text(details), url or "")


IMPORTING_TEMPLATE = "console/stories/importing.html"


def render_importing(request: HttpRequest, story_id: str, exc: EngineClientError) -> HttpResponse:
    """``ImportingStoryMiddleware``'s answer: the importing page (409) with the story id,
    :func:`importing_refusal`'s text and link, and "Retry" (this URL for a GET or HEAD, else the
    story page, since a POST-only URL would 405); an HTMX request gets ``HX-Redirect`` to the
    story page."""
    log_engine_error(request, exc)
    detail = reverse("stories:detail", args=[story_id])
    if is_htmx(request):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = detail
        return response
    refusal = importing_refusal(exc)
    retry_url = request.get_full_path() if request.method in ("GET", "HEAD") else detail
    context = {"story_id": story_id, "refusal": refusal, "retry_url": retry_url}
    return render(request, IMPORTING_TEMPLATE, context, status=409)


# --- E6: URL helpers ------------------------------------------------------------------------------


def _id(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _reversed(name: str, *ids: object) -> str | None:
    if not all(_id(i) for i in ids):
        return None
    try:
        return reverse(name, args=list(ids))
    except NoReverseMatch:
        return None


def export_url(story_id: str) -> str | None:
    """The story's Export page (``stories:export``, #319)."""
    return _reversed("stories:export", story_id)


def export_job_url(story_id: str, job_id: str) -> str | None:
    """One export job's page (``stories:export-job``, #319)."""
    return _reversed("stories:export-job", story_id, job_id)


def import_job_url(job_id: str | None) -> str | None:
    """One import job's page (``engine:import-job``, #321)."""
    return _reversed("engine:import-job", job_id)


def prune_url(story_id: str) -> str | None:
    """The story's prune page (``stories:prune``, #317)."""
    return _reversed("stories:prune", story_id)


# --- the bundle's file name -----------------------------------------------------------------------


class Named(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> Any: ...


def bundle_filename(story: Named) -> str:
    """ "harbour-nights-e-20261106.epistrel.tgz": the name's slug (else the id's, else "story")
    and today's UTC date."""
    name = story.name if isinstance(story.name, str) else ""
    slug = slugify(name) or slugify(str(story.id or "")) or FALLBACK_SLUG
    return f"{slug}-{timezone.now().astimezone(UTC).strftime('%Y%m%d')}{BUNDLE_SUFFIX}"
