"""What a story's creation from a scenario applied (#75), as the Console shows it.

The Engine has no read-back for a creation report: the creation report page is the stored #44
row of the ``create_story_from_scenario`` call that answered 201 for the story
(:func:`creation_row`; the newest one, should a replay have written another). An ``applied:
false`` answer created nothing, so its row has a blank ``story_id`` and is never found here.

:func:`shown_creation` turns a :class:`~console.engine.models.CreationReport` into what
``console/stories/_creation_report.html`` renders: the counts line, the entries through the
shared ``_validation_report.html`` (``personality_in_role`` and ``rating_looser`` first among
the warnings), the deferred items grouped by the capability they need, the inactive settings
and, for a structured story, the materialized items raw. The cast table names each member's
role by its scenario label (``public_label`` (``role_id``)), when the page could read it.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from django.urls import reverse

from console.engine.models import (
    CatalogCastCard,
    CreationReport,
    DeferredItem,
    InactiveItem,
    MemberCastCard,
    ReportEntry,
    ValidationReport,
)
from console.models import EngineOperation

CREATION_KIND = "create_story_from_scenario"  # the client method, the #44 row's ``kind``
CREATED = 201
FIRST_WARNINGS = ("personality_in_role", "rating_looser")
SOURCE_LABELS = {"ad_hoc": "ad-hoc", "player": "player"}


def creation_row(story_id: str) -> EngineOperation | None:
    """The newest #44 row of a 201 ``create_story_from_scenario`` that made ``story_id``."""
    return (
        EngineOperation.objects.filter(kind=CREATION_KIND, story_id=story_id, http_status=CREATED)
        .order_by("-created_at", "-id")
        .first()
    )


def has_creation_report(story_id: str) -> bool:
    """Whether the story page offers "Creation report" (one indexed ``exists()`` query)."""
    return EngineOperation.objects.filter(
        kind=CREATION_KIND, story_id=story_id, http_status=CREATED
    ).exists()


@dataclass(frozen=True)
class CastRow:
    """One cast member: its name, its role as "label (role_id)" (or the id alone), where it
    came from ("catalog_id@version", "ad-hoc" or "player") and the catalog link, if any."""

    name: str
    role_id: str
    role_label: str
    kind: str
    source: str
    source_url: str = ""


@dataclass(frozen=True)
class DeferredGroup:
    capability: str
    items: list[DeferredItem]


@dataclass(frozen=True)
class ShownCreation:
    report: CreationReport
    entries: ValidationReport  # for the shared partial
    cast: list[CastRow]
    deferred: list[DeferredGroup]
    inactive: list[InactiveItem]
    materialized: list[dict[str, Any]] | None  # raw, shown under "More"
    lite: bool  # no ``materialized`` list: the Lite genesis notice shows
    counts_line: str
    count_tables: list[tuple[str, str, Mapping[str, int]]] = field(default_factory=list)


def _ordered_warnings(warnings: list[ReportEntry]) -> list[ReportEntry]:
    first = [w for code in FIRST_WARNINGS for w in warnings if w.code == code]
    return first + [w for w in warnings if w.code not in FIRST_WARNINGS]


def cast_row(card: CatalogCastCard | MemberCastCard, role_labels: Mapping[str, str]) -> CastRow:
    role = role_labels.get(card.role_id)
    role_label = f"{role} ({card.role_id})" if role else card.role_id
    if isinstance(card, CatalogCastCard):
        source = card.source
        url = reverse("catalog_characters:detail", args=[source.catalog_id])
        return CastRow(
            card.name or card.character_id,
            card.role_id,
            role_label,
            card.kind,
            f"{source.catalog_id}@{source.version}",
            f"{url}?version={source.version}",
        )
    return CastRow(
        card.name, card.role_id, role_label, card.kind, SOURCE_LABELS.get(card.kind, card.kind)
    )


def shown_creation(
    report: CreationReport, *, role_labels: Mapping[str, str] | None = None
) -> ShownCreation:
    labels = role_labels or {}
    groups: dict[str, list[DeferredItem]] = {}
    for item in report.deferred or []:
        groups.setdefault(item.capability_needed, []).append(item)
    counts = report.counts
    tables: list[tuple[str, str, Mapping[str, int]]] = [
        ("Deferred", "by class", counts.deferred.by_class),
        ("Deferred", "by capability needed", counts.deferred.by_capability),
        ("Inactive", "by class", counts.inactive.by_class),
        ("Inactive", "by capability", counts.inactive.by_capability),
    ]
    return ShownCreation(
        report=report,
        entries=ValidationReport(
            errors=list(report.errors or []),
            warnings=_ordered_warnings(list(report.warnings or [])),
            unavailable=list(report.unavailable),
        ),
        cast=[cast_row(card, labels) for card in report.cast],
        deferred=[DeferredGroup(capability, items) for capability, items in groups.items()],
        inactive=list(report.inactive or []),
        materialized=(
            None if report.materialized is None else [asdict(m) for m in report.materialized]
        ),
        lite=report.materialized is None,
        counts_line=(
            f"{counts.errors} error{'' if counts.errors == 1 else 's'}, "
            f"{counts.conflicts} conflict{'' if counts.conflicts == 1 else 's'}, "
            f"{counts.warnings} warning{'' if counts.warnings == 1 else 's'}"
        ),
        count_tables=[t for t in tables if t[2]],
    )
