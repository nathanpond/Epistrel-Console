"""A scenario's validation report as its pages show it (#74): Validate's answer, a publish's
report (after a redirect, read back from the operation-log row) and a 422 ``publish_invalid``.

:func:`shown_report` wraps #64's :class:`~console.engine.models.ValidationReport` with what the
partial ``console/catalog/scenarios/_report.html`` renders around #64's ``_location.html``:

- a headline: "Valid", "Valid — N warning(s)", "N error(s)" or "N error(s), M warning(s)";
- the heading naming what was checked ("Draft revision R", or "Version N — checked against the
  catalog as it stands now");
- each entry in the Engine's order; one whose ``location.entry`` is another entry links to
  ``/catalog/characters/<id>/`` (labelled with the role id) when a role's character is that id,
  else shows the id in ``<code>``;
- the checks the Engine could not run, by label and code ("Seed normalization
  (``seed_normalization``)"; an unknown code raw).

Field locations link to the editor's ``#f-<path>`` anchors (``anchor_base``: the edit page's URL,
or empty on the edit page itself), or are plain text (``linked=False``: a published version's
report, whose fields no editor shows).
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.urls import reverse

from console.engine.models import ReportEntry, ValidationReport
from console.models import EngineOperation

# The checks a deployment may not run (``report.unavailable``), by code.
UNAVAILABLE_LABELS = {
    "seed_normalization": "Seed normalization",
    "fact_consistency": "Fact consistency",
    "state_schema": "State schema",
}


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def headline(report: ValidationReport) -> str:
    """The report's headline: "Valid", "Valid — 1 warning", "2 errors" or "2 errors,
    1 warning"."""
    errors, warnings = len(report.errors), len(report.warnings)
    if errors:
        shown = _count(errors, "error")
        return f"{shown}, {_count(warnings, 'warning')}" if warnings else shown
    return f"Valid — {_count(warnings, 'warning')}" if warnings else "Valid"


def unavailable_label(code: str) -> str:
    """An unavailable check as text: "Seed normalization (seed_normalization)"; an unknown
    code raw."""
    label = UNAVAILABLE_LABELS.get(code)
    return f"{label} ({code})" if label else code


def draft_heading(draft_revision: int | None) -> str:
    return f"Draft revision {draft_revision}" if draft_revision is not None else "Draft"


def version_heading(version: int | str) -> str:
    return f"Version {version} — checked against the catalog as it stands now"


@dataclass(frozen=True)
class ShownEntry:
    """One entry, and how its ``location.entry`` shows: linked to a role's character
    (``entry_url``, labelled ``entry_label``), or as another entry's id (``other_entry``)."""

    entry: ReportEntry
    entry_url: str = ""
    entry_label: str = ""
    other_entry: bool = False


@dataclass(frozen=True)
class ShownReport:
    report: ValidationReport
    heading: str
    errors: list[ShownEntry]
    warnings: list[ShownEntry]
    unavailable: list[tuple[str, str]]  # (label, or "" for an unknown code; code)
    anchor_base: str = ""
    linked: bool = True
    anchors: Collection[str] | None = None
    label: str = ""  # e.g. "Published v3"

    @property
    def headline(self) -> str:
        return headline(self.report)

    @property
    def valid(self) -> bool:
        return not self.report.errors


def role_characters(cast: Mapping[str, Any] | None) -> dict[str, str]:
    """``{catalog_id: role_id}`` of the roles naming a catalog character, from a cast in the
    write shape (``draft_to_write``'s ``cast``) or ``asdict`` of a ``ScenarioCast``."""
    found: dict[str, str] = {}
    for role in (cast or {}).get("roles") or []:
        if not isinstance(role, Mapping):
            continue
        character = role.get("character")
        catalog_id = character.get("catalog_id") if isinstance(character, Mapping) else None
        if isinstance(catalog_id, str) and isinstance(role.get("id"), str):
            found.setdefault(catalog_id, role["id"])
    return found


def _character_url(character_id: str) -> str:
    return reverse("catalog_characters:detail", args=[character_id])


def _shown(entry: ReportEntry, scenario_id: str, characters: Mapping[str, str]) -> ShownEntry:
    other = entry.location.entry
    if not other or other == scenario_id:
        return ShownEntry(entry)
    role_id = characters.get(other)
    if role_id is not None:
        return ShownEntry(entry, _character_url(other), role_id)
    return ShownEntry(entry, other_entry=True)


def shown_report(
    report: ValidationReport,
    *,
    scenario_id: str,
    heading: str,
    characters: Mapping[str, str] | None = None,
    anchor_base: str = "",
    linked: bool = True,
    anchors: Collection[str] | None = None,
    label: str = "",
) -> ShownReport:
    """``report`` as the partial shows it; ``characters`` is :func:`role_characters` of the
    scenario's cast."""
    known = characters or {}
    return ShownReport(
        report,
        heading,
        [_shown(e, scenario_id, known) for e in report.errors],
        [_shown(e, scenario_id, known) for e in report.warnings],
        [(UNAVAILABLE_LABELS.get(code, ""), code) for code in report.unavailable],
        anchor_base,
        linked,
        anchors if linked else (),  # an empty collection: every location plain text
        label,
    )


@dataclass(frozen=True)
class RowReport:
    """A report read back from an operation-log row (``?report=<pk>``): what it is about and
    the version a successful publish made (``None`` for a refusal)."""

    report: ValidationReport
    heading: str
    version: int | None = None
    draft_revision: int | None = None
    error_code: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def report_from_publish_row(pk: object, scenario_id: str) -> RowReport | None:
    """The report a scenario publish stored in row ``pk``: a success's ``report`` (heading
    "Published v<N>") or a 422 ``publish_invalid``'s ``details.report`` (heading the draft
    revision sent). ``None`` when the row is missing, about another entry or holds no report."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    if row.entity_type != "scenario" or row.entity_id != scenario_id:
        return None
    body: dict[str, Any] = row.response if isinstance(row.response, dict) else {}
    request: dict[str, Any] = row.request if isinstance(row.request, dict) else {}
    sent = request.get("body")
    revision = sent.get("draft_revision") if isinstance(sent, dict) else None
    revision = revision if isinstance(revision, int) else None
    error = body.get("error")
    if isinstance(error, dict):
        raw_details = error.get("details")
        details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
        report = ValidationReport.from_json(details.get("report"))
        if report is None:
            return None
        code = error.get("code")
        return RowReport(
            report,
            draft_heading(revision),
            None,
            revision,
            code if isinstance(code, str) else "",
            details,
        )
    report = ValidationReport.from_json(body.get("report"))
    version = body.get("version")
    if report is None or not isinstance(version, int):
        return None
    published_revision = body.get("draft_revision")
    return RowReport(
        report,
        f"Published v{version}",
        version,
        published_revision if isinstance(published_revision, int) else None,
    )
