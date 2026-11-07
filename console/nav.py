"""M7's page navigation (E11/E12, #296): the story-level and Engine-panel page registries, and
the nav rows built from them.

**The nav rule (#64): unregistered → no link.** Each row's owner registers its URL in
``epistrel_console/urls.py``; until it does, :func:`story_links` / :func:`engine_links` leave the
row out (never greyed), and with none registered the nav partials render nothing. "Current" is
the request's URL name mapped through :data:`PAGE_PARENT` (a page with no nav row of its own
marks its list's row), else an exact name match (D3's rule).

**Story-level pages** (namespace ``stories:``, under ``/stories/<story_id>/``). The nav rows,
:data:`STORY_PAGES` in nav order, are URL name, path, label:

- ``stories:rewrites`` ``rewrites/`` "Rewrite" (#297)
- ``stories:amendments`` ``amendments/`` "Amendments" (#302)
- ``stories:backfill`` ``backfill/`` "Backfill" (#308)
- ``stories:export`` ``export/`` "Export" (#319)
- ``stories:operations-list`` ``operations/`` "Operations" (#324)

The pages with no nav row, and (in parentheses) the row they mark current:

- ``stories:rewrite`` ``rewrites/<ulid:session_id>/`` (Rewrite), ``stories:rewrite-answers``
  ``…/answers/``, ``stories:rewrite-apply`` ``…/apply/``, ``stories:rewrite-cancel``
  ``…/cancel/`` (Rewrite), ``stories:line-edit`` ``history/line/`` (Rewrite);
- ``stories:amendment`` ``amendments/<ulid:amendment_id>/``, ``stories:amendment-undo``
  ``…/undo/``, ``stories:amendment-redo`` ``…/redo/`` (Amendments);
- ``stories:backfill-start`` ``backfill/start/``, ``stories:backfill-cancel``
  ``backfill/cancel/``, ``stories:backfill-conflict`` ``backfill/conflicts/<str:conflict_id>/``
  (Backfill);
- ``stories:export-job`` ``export/<ulid:job_id>/``, ``stories:export-download``
  ``export/<ulid:job_id>/bundle/`` (Export);
- no row: ``stories:direct`` ``direct/`` (the Play page's Direct tab POST), ``stories:certify``
  ``certify/`` (linked from Config), ``stories:prune`` ``prune/`` (linked from History's
  Variants and Settings), ``stories:prune-confirm`` ``prune/confirm/``.

**Engine-panel pages** (namespace ``engine:``, under ``/engine/``). The nav rows,
:data:`ENGINE_PAGES`:

- ``engine:import`` ``import/`` "Import a bundle" (#320)
- ``engine:certifications`` ``certifications/`` "Certifications" (#313)
- ``engine:eval-runs`` ``evals/`` "Eval runs" (#316)

The pages with no nav row:

- ``engine:import-job`` ``import/<ulid:job_id>/`` (Import a bundle);
- ``engine:certification`` ``certifications/<str:certification_id>/``,
  ``engine:certification-invalidate`` ``…/invalidate/``, ``engine:certification-import``
  ``certification-import/`` (a sibling, so an id ``import`` is never shadowed),
  ``engine:certification-run`` ``certification-runs/<str:run_id>/`` (Certifications);
- ``engine:eval-run`` ``evals/<str:run_id>/`` (Eval runs).

The parentheses are :data:`PAGE_PARENT`; the no-row pages mark none and pass ``label=`` to the
frame. ``stories:operations-list`` is not ``stories:operations``, so it never
reads as the Console's own ``operations:`` log (#44). E12's Effects ``Outbox`` section is a row
of :data:`console.inspect.EFFECTS_SECTIONS`, not a page here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from django.urls import NoReverseMatch, reverse

STORY_PAGES: tuple[tuple[str, str], ...] = (
    ("stories:rewrites", "Rewrite"),  # #297
    ("stories:amendments", "Amendments"),  # #302
    ("stories:backfill", "Backfill"),  # #308
    ("stories:export", "Export"),  # #319
    ("stories:operations-list", "Operations"),  # #324
)
ENGINE_PAGES: tuple[tuple[str, str], ...] = (
    ("engine:import", "Import a bundle"),  # #320
    ("engine:certifications", "Certifications"),  # #313
    ("engine:eval-runs", "Eval runs"),  # #316
)
# A page with no nav row of its own → the nav row it marks current (E11's tables).
PAGE_PARENT: Mapping[str, str] = MappingProxyType(
    {
        "stories:rewrite": "stories:rewrites",
        "stories:rewrite-answers": "stories:rewrites",
        "stories:rewrite-apply": "stories:rewrites",
        "stories:rewrite-cancel": "stories:rewrites",
        "stories:line-edit": "stories:rewrites",
        "stories:amendment": "stories:amendments",
        "stories:amendment-undo": "stories:amendments",
        "stories:amendment-redo": "stories:amendments",
        "stories:backfill-start": "stories:backfill",
        "stories:backfill-cancel": "stories:backfill",
        "stories:backfill-conflict": "stories:backfill",
        "stories:export-job": "stories:export",
        "stories:export-download": "stories:export",
        "engine:import-job": "engine:import",
        "engine:certification": "engine:certifications",
        "engine:certification-run": "engine:certifications",
        "engine:certification-import": "engine:certifications",
        "engine:certification-invalidate": "engine:certifications",
        "engine:eval-run": "engine:eval-runs",
    }
)


@dataclass(frozen=True, slots=True)
class NavLink:
    """One registered nav row: its URL name, label, reversed URL, and whether it is current."""

    name: str
    label: str
    url: str
    current: bool


def nav_row(current: str | None) -> str | None:
    """The nav row ``current`` (a URL name) marks: its :data:`PAGE_PARENT`, else itself."""
    if current is None:
        return None
    return PAGE_PARENT.get(current, current)


def _links(
    rows: tuple[tuple[str, str], ...], args: list[str], current: str | None
) -> list[NavLink]:
    marked = nav_row(current)
    links = []
    for name, label in rows:
        try:
            url = reverse(name, args=args)
        except NoReverseMatch:
            continue  # #64: its owner has not registered it yet
        links.append(NavLink(name, label, url, name == marked))
    return links


def story_links(story_id: str, current: str | None) -> list[NavLink]:
    """The registered :data:`STORY_PAGES` rows for ``story_id``, ``current``'s row marked."""
    return _links(STORY_PAGES, [story_id], current)


def engine_links(current: str | None) -> list[NavLink]:
    """The registered :data:`ENGINE_PAGES` rows, ``current``'s row marked."""
    return _links(ENGINE_PAGES, [], current)


def row_label(rows: tuple[tuple[str, str], ...], current: str | None) -> str | None:
    """The label of the nav row ``current`` marks in ``rows``, or ``None``."""
    marked = nav_row(current)
    return next((label for name, label in rows if name == marked), None)
