"""Anchors for validation-report entries (#64, M3 convention 4).

Form fields carry ids that report entries link to: a field at Engine path ``p`` has
``id="f-<p with '.', '[' and ']' each replaced by '-'>"`` (:func:`field_anchor`); a seed's block
is ``seed-<seed_id>`` and a role's block ``role-<role_id>``. An entry links to its field when it
names one, else its seed, else its role (:func:`location_anchor`). Given the anchors a page
actually has, a field anchor that is missing falls back to its nearest ancestor (``cast.roles[1]
.name`` → ``cast.roles[1]`` → ``cast.roles`` → ``cast``).

A report shown after a redirect is read back from its operation-log row (``?report=<pk>``,
:func:`report_from_row`), never from session storage.
"""

import re
from collections.abc import Collection
from typing import Any

from console.engine.models import EntryLocation, ValidationReport
from console.models import EngineOperation

_ANCHOR_CHARS = re.compile(r"[.\[\]]")
# The step back to a field's parent: a trailing ``.name`` or ``[n]``.
_LAST_SEGMENT = re.compile(r"(\.[^.\[\]]+|\[[^\[\]]*\])$")


def field_anchor(path: str) -> str:
    """``cast.roles[1].name`` → ``f-cast-roles-1--name``: the id a form gives the field."""
    return "f-" + _ANCHOR_CHARS.sub("-", path)


def seed_anchor(seed_id: str) -> str:
    return f"seed-{seed_id}"


def role_anchor(role_id: str) -> str:
    return f"role-{role_id}"


def _ancestors(path: str) -> list[str]:
    paths = [path]
    while True:
        parent = _LAST_SEGMENT.sub("", paths[-1])
        if parent == paths[-1] or not parent:
            return paths
        paths.append(parent)


def location_anchor(location: EntryLocation, known: Collection[str] | None = None) -> str | None:
    """The anchor (without ``#``) an entry at ``location`` links to, or ``None`` when it names
    no field, seed or role. With ``known`` (the anchors the page has), the nearest existing
    ancestor of the field is used, then the seed, then the role; ``None`` if none exists."""
    candidates: list[str] = []
    if location.field:
        candidates.extend(field_anchor(p) for p in _ancestors(location.field))
    if location.seed_id:
        candidates.append(seed_anchor(location.seed_id))
    if location.role:
        candidates.append(role_anchor(location.role))
    if not candidates:
        return None
    if known is None:
        return candidates[0]
    return next((c for c in candidates if c in known), None)


def report_from_json(value: object) -> ValidationReport | None:
    """A ``ValidationReport`` from raw JSON: the report itself, or a body carrying one under
    ``report`` (a validation) or ``error.details.report`` (a 422 ``publish_invalid``)."""
    if not isinstance(value, dict):
        return None
    error: Any = value.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    for candidate in (
        details.get("report") if isinstance(details, dict) else None,
        value.get("report"),
        value,
    ):
        if isinstance(candidate, dict) and ({"errors", "warnings", "unavailable"} & set(candidate)):
            parsed = ValidationReport.from_json(candidate)
            if parsed is not None:
                return parsed
    return None


def report_from_row(pk: object) -> ValidationReport | None:
    """The report stored in operation-log row ``pk`` (``?report=<pk>``), or ``None`` when the
    row is missing or holds no report."""
    try:
        row = EngineOperation.objects.get(pk=int(str(pk)))
    except EngineOperation.DoesNotExist, ValueError:
        return None
    return report_from_json(row.response)
