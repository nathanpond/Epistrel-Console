"""The one shared M7 stub urlconf (#294, E6/E24): the Console's real routes plus a one-line stub
view for every URL name E6's helpers reverse and every E11 page row, mounted at E11's paths, so
an M7 link helper's linked case can be tested before the page's owner registers it.

Django searches one resolver per namespace, so each namespace's list (``stories:``,
``inspect:``, ``engine:``, ``profiles:``) is rebuilt with any real pattern of a stub's name
dropped first, the stubs appended, and remounted in place of the real include (D19's
``tests/urls_with_engine_operation.py`` mechanism, which stays as it is). The file therefore
keeps working as owners land.

**Page stories add a stub here, and never create another stub urlconf.** #292's test-local
``stories:rewrite`` stub was folded in, then dropped once #297 registered the real session page
(the linked cases of its footer and amendment links render the real page here).

**The frames (#296).** ``stories:rewrites`` renders through
:func:`console.views.story_pages.story_page` and ``engine:certifications`` through
:func:`~console.views.story_pages.engine_page`, both with :func:`recording_build`
(``stories:rewrites`` keeps this probe after #297 registers the real Rewrite page, so under this
urlconf ``/stories/<id>/rewrites/`` is the probe, not the page): it appends its
context to :data:`BUILT` and answers :data:`BUILD`'s callable when a test set one, else ``{}``
(``tests/test_story_pages.py``'s autouse fixture clears both). Every other stub answers "stub".

**E6's ``NOT_YET_REGISTERED`` rule.** The complement test ("under the real urlconf none of
these reverses") asserts only the names in :data:`NOT_YET_REGISTERED`. Each owner removes its
own names from that set in the PR that registers them, so a parallel story's CI never goes red
for a name it doesn't own.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.urls import URLPattern, URLResolver, include, path

from console.views.story_pages import (
    EnginePageContext,
    StoryPageContext,
    engine_page,
    story_page,
)
from epistrel_console import urls as real

STORY_PAGE_TEMPLATE = "console/stories/page_base.html"
ENGINE_PAGE_TEMPLATE = "console/engine/page_base.html"
Built = Callable[[Any], Mapping[str, Any] | HttpResponse]
BUILT: list[StoryPageContext | EnginePageContext] = []
BUILD: list[Built] = []  # at most one: the test's build behind recording_build


def stub(request: HttpRequest, **kwargs: str) -> HttpResponse:
    return HttpResponse("stub")


def recording_build(ctx: StoryPageContext | EnginePageContext) -> Mapping[str, Any] | HttpResponse:
    BUILT.append(ctx)
    return BUILD[0](ctx) if BUILD else {}


def rewrites_frame(request: HttpRequest, story_id: str) -> HttpResponse:
    return story_page(request, story_id, STORY_PAGE_TEMPLATE, recording_build)


def certifications_frame(request: HttpRequest) -> HttpResponse:
    return engine_page(request, ENGINE_PAGE_TEMPLATE, recording_build)


def _rebuilt(
    patterns: tuple[Sequence[URLPattern | URLResolver], str], stubs: list[URLPattern]
) -> tuple[list[URLPattern | URLResolver], str]:
    names = {p.name for p in stubs}
    kept = [p for p in patterns[0] if getattr(p, "name", None) not in names]
    return [*kept, *stubs], patterns[1]


S = "<str:story_id>/"
STORY_STUBS = [
    # #296's frame probe for ``tests/test_story_pages.py``: it shadows #297's real Rewrite page
    # under this urlconf only (the real ``stories:rewrite`` session page is served here).
    path(f"{S}rewrites/", rewrites_frame, name="rewrites"),
    path(f"{S}rewrites/<ulid:session_id>/answers/", stub, name="rewrite-answers"),  # #298
    path(f"{S}rewrites/<ulid:session_id>/apply/", stub, name="rewrite-apply"),  # #299
    path(f"{S}rewrites/<ulid:session_id>/cancel/", stub, name="rewrite-cancel"),  # #299
    path(f"{S}history/line/", stub, name="line-edit"),  # #305
    path(f"{S}direct/", stub, name="direct"),  # #300
    path(f"{S}amendments/", stub, name="amendments"),  # #302
    path(f"{S}amendments/<ulid:amendment_id>/", stub, name="amendment"),  # #302
    path(f"{S}amendments/<ulid:amendment_id>/undo/", stub, name="amendment-undo"),  # #303
    path(f"{S}amendments/<ulid:amendment_id>/redo/", stub, name="amendment-redo"),  # #303
    path(f"{S}backfill/", stub, name="backfill"),  # #308
    path(f"{S}backfill/start/", stub, name="backfill-start"),  # #309
    path(f"{S}backfill/cancel/", stub, name="backfill-cancel"),  # #308
    path(f"{S}backfill/conflicts/<str:conflict_id>/", stub, name="backfill-conflict"),  # #309
    path(f"{S}certify/", stub, name="certify"),  # #314
    path(f"{S}prune/", stub, name="prune"),  # #317
    path(f"{S}prune/confirm/", stub, name="prune-confirm"),  # #317
    path(f"{S}export/", stub, name="export"),  # #319
    path(f"{S}export/<ulid:job_id>/", stub, name="export-job"),  # #319
    path(f"{S}export/<ulid:job_id>/bundle/", stub, name="export-download"),  # #319
    path(f"{S}operations/", stub, name="operations-list"),  # #324
]
INSPECT_STUBS = [
    path("canon/merge/", stub, name="entity-merge"),  # #306
    path("effects/outbox/", stub, name="outbox"),  # #325
    path("effects/reconciliation/<str:listing>/resolve/", stub, name="resolve-listing"),  # #326
    path("effects/reconciliation/resolved/", stub, name="resolved"),  # #326
]
ENGINE_STUBS = [
    path("import/", stub, name="import"),  # #320
    path("import/<ulid:job_id>/", stub, name="import-job"),  # #321
    path("certifications/", certifications_frame, name="certifications"),  # #313 (#296's frame)
    path("certifications/<str:certification_id>/", stub, name="certification"),  # #313
    path(
        "certifications/<str:certification_id>/invalidate/",
        stub,
        name="certification-invalidate",
    ),  # #315
    path("certification-import/", stub, name="certification-import"),  # #315
    path("certification-runs/<str:run_id>/", stub, name="certification-run"),  # #313
    path("evals/", stub, name="eval-runs"),  # #316
    path("evals/<str:run_id>/", stub, name="eval-run"),  # #316
]
PROFILE_STUBS = [
    path("<str:profile_id>/upgrade-pinned/", stub, name="upgrade-pinned"),  # #310
    path("<str:profile_id>/certify/", stub, name="certify"),  # #314
]

MOUNTS = {
    "stories/": _rebuilt(real.story_patterns, STORY_STUBS),
    "stories/<str:story_id>/inspect/": _rebuilt(real.inspect_patterns, INSPECT_STUBS),
    "engine/": _rebuilt(real.engine_patterns, ENGINE_STUBS),
    "profiles/": _rebuilt(real.profile_patterns, PROFILE_STUBS),
}

STUB_NAMES = frozenset(
    f"{namespace}:{p.name}"
    for namespace, stubs in (
        ("stories", STORY_STUBS),
        ("inspect", INSPECT_STUBS),
        ("engine", ENGINE_STUBS),
        ("profiles", PROFILE_STUBS),
    )
    for p in stubs
)

# E6: the stubbed names no real pattern registers yet. Each owner removes its own names in the
# PR that registers them; the complement test asserts only these.
NOT_YET_REGISTERED = frozenset(
    {
        "engine:certification",
        "engine:certification-import",
        "engine:certification-invalidate",
        "engine:certification-run",
        "engine:certifications",
        "engine:eval-run",
        "engine:eval-runs",
        "engine:import",
        "engine:import-job",
        "inspect:entity-merge",
        "inspect:outbox",
        "inspect:resolve-listing",
        "inspect:resolved",
        "profiles:certify",
        "profiles:upgrade-pinned",
        "stories:amendment",
        "stories:amendment-redo",
        "stories:amendment-undo",
        "stories:amendments",
        "stories:backfill-conflict",
        "stories:backfill-start",
        "stories:certify",
        "stories:direct",
        "stories:export",
        "stories:export-download",
        "stories:export-job",
        "stories:line-edit",
        "stories:operations-list",
        "stories:prune",
        "stories:prune-confirm",
    }
)


def _remounted(pattern: URLPattern | URLResolver) -> URLPattern | URLResolver:
    mount = str(pattern.pattern)
    return path(mount, include(MOUNTS[mount])) if mount in MOUNTS else pattern


urlpatterns: list[URLPattern | URLResolver] = [
    _remounted(p) for p in real.urlpatterns if isinstance(p, URLPattern | URLResolver)
]
