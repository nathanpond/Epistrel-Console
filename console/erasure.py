"""Erasure (#86) pieces other pages share: the "Erase…" links that pre-fill the form, and the
operation-log rows an erasure is kept in (the Engine has no read-back by erasure id).

- :func:`new_url` is ``/erasure/new/?kind=&subject=&scope=[&story=]``, or ``None`` while the
  page does not exist (a page links "Erase…" only once it does).
- :func:`result_row` finds the #44 row of a real run by its ``erasure_id`` (newest first);
  :func:`real_runs` lists every real run that answered an erasure id, :func:`console_runs`
  every real run whatever its outcome (#87). Both runs are recorded as
  ``kind="erase"`` and told apart by the request body's ``dry_run``.
"""

from typing import Literal
from urllib.parse import urlencode

from django.db.models import QuerySet
from django.urls import NoReverseMatch, reverse

from console.engine.models import SubjectKind
from console.models import EngineOperation

KIND = "erase"  # the client method, so the operation log's kind
SUBJECT_KINDS: dict[SubjectKind, str] = {
    "character": "Story character",
    "catalog_character": "Catalog character",
    "persona": "Persona",
}
type ScopeName = Literal["story", "catalog"]
SCOPE_STORY: Literal["story"] = "story"
SCOPE_CATALOG: Literal["catalog"] = "catalog"
SCOPE_LABELS: dict[str, str] = {SCOPE_STORY: "This story", SCOPE_CATALOG: "The catalog"}


def new_url(
    kind: SubjectKind, subject: str, *, scope: ScopeName, story: str | None = None
) -> str | None:
    """The erasure form pre-filled for ``subject``, or ``None`` when ``erasure:new`` does not
    resolve."""
    try:
        url = reverse("erasure:new")
    except NoReverseMatch:
        return None
    query = {"kind": kind, "subject": subject, "scope": scope}
    if story:
        query["story"] = story
    return f"{url}?{urlencode(query)}"


def real_runs() -> QuerySet[EngineOperation]:
    """Every recorded real run (``dry_run`` false) that answered with an ``erasure_id``, newest
    first."""
    return (
        EngineOperation.objects.filter(kind=KIND, request__body__dry_run=False)
        .exclude(response__erasure_id=None)
        .filter(response__erasure_id__isnull=False)
        .order_by("-created_at", "-id")
    )


def console_runs() -> QuerySet[EngineOperation]:
    """Every real run (``dry_run`` false) this Console made, whatever its outcome, newest
    first (#87's list)."""
    return EngineOperation.objects.filter(kind=KIND, request__body__dry_run=False).order_by(
        "-created_at", "-id"
    )


def result_row(erasure_id: str) -> EngineOperation | None:
    """The newest ``erase`` row whose answer carries ``erasure_id`` (any operator's: the Console
    has one operator at a time)."""
    return (
        EngineOperation.objects.filter(kind=KIND, response__erasure_id=erasure_id)
        .order_by("-created_at", "-id")
        .first()
    )


def result_url(erasure_id: str) -> str:
    return reverse("erasure:detail", args=[erasure_id])
