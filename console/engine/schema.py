"""Compare two OpenAPI documents: pure functions, no I/O.

Used by ``manage.py engine_schema --check`` (#34), the daily drift check (#40) and the status
page's API line (#41), so all three agree on what counts as a difference.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The Engine `edge` image's /openapi.json, written by `manage.py engine_schema --write`.
RECORDED_PATH = Path(__file__).resolve().parent / "openapi.json"

# Top-level keys that describe the document rather than the API; a new version is not drift.
IGNORED_TOP_LEVEL = ("info", "servers", "externalDocs")
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def normalise(doc: dict[str, Any]) -> dict[str, Any]:
    """The document without its top-level ``info``, ``servers`` and ``externalDocs``."""
    return {key: value for key, value in doc.items() if key not in IGNORED_TOP_LEVEL}


def dumps(doc: dict[str, Any]) -> str:
    """The recorded file's form: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


@dataclass(frozen=True)
class SchemaDiff:
    operations_added: tuple[str, ...] = ()
    operations_removed: tuple[str, ...] = ()
    operations_changed: tuple[str, ...] = ()
    components_added: tuple[str, ...] = ()
    components_removed: tuple[str, ...] = ()
    components_changed: tuple[str, ...] = ()
    other_changed: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return any(
            (
                self.operations_added,
                self.operations_removed,
                self.operations_changed,
                self.components_added,
                self.components_removed,
                self.components_changed,
                self.other_changed,
            )
        )

    def summary(self) -> str:
        """Counts, zero counts omitted: ``1 operation added, 2 components changed``."""
        parts = [
            f"{_plural(len(items), noun)} {verb}"
            for items, noun, verb in (
                (self.operations_added, "operation", "added"),
                (self.operations_removed, "operation", "removed"),
                (self.operations_changed, "operation", "changed"),
                (self.components_added, "component", "added"),
                (self.components_removed, "component", "removed"),
                (self.components_changed, "component", "changed"),
            )
            if items
        ]
        if self.other_changed:
            parts.append(_plural(len(self.other_changed), "other change"))
        return ", ".join(parts)

    def changes(self) -> tuple[str, ...]:
        """One line per difference: ``added: POST /stories``, ``changed: schemas/Error``.

        Added, then removed, then changed; operations before components before other keys.
        """
        lines: list[str] = []
        for kind, operations, components in (
            ("added", self.operations_added, self.components_added),
            ("removed", self.operations_removed, self.components_removed),
            ("changed", self.operations_changed, self.components_changed),
        ):
            lines.extend(f"{kind}: {name}" for name in operations)
            lines.extend(f"{kind}: {name}" for name in components)
        lines.extend(f"changed: {name}" for name in self.other_changed)
        return tuple(lines)

    def render(self) -> str:
        """Plain text, one section per non-empty kind, entries indented two spaces."""
        sections = (
            ("Operations added:", self.operations_added),
            ("Operations removed:", self.operations_removed),
            ("Operations changed:", self.operations_changed),
            ("Components added:", self.components_added),
            ("Components removed:", self.components_removed),
            ("Components changed:", self.components_changed),
            ("Other changes:", self.other_changed),
        )
        lines: list[str] = []
        for heading, items in sections:
            if items:
                lines.append(heading)
                lines.extend(f"  {item}" for item in items)
        return "\n".join(lines) + ("\n" if lines else "")


def _operation_key(name: str) -> tuple[str, str]:
    method, path = name.split(" ", 1)
    return path, method


def operations(doc: dict[str, Any]) -> dict[str, Any]:
    """``{"GET /health": <operation object>}`` for every HTTP method under ``paths``."""
    found: dict[str, Any] = {}
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        return found
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            if method in item:
                found[f"{method.upper()} {path}"] = item[method]
    return found


def _path_extras(doc: dict[str, Any], path: str) -> Any:
    item = (doc.get("paths") or {}).get(path)
    if not isinstance(item, dict):
        return item
    return {key: value for key, value in item.items() if key not in HTTP_METHODS}


def components(doc: dict[str, Any]) -> dict[str, Any]:
    """``{"schemas/HealthResponse": <schema>}`` across every component section."""
    found: dict[str, Any] = {}
    sections = doc.get("components")
    if not isinstance(sections, dict):
        return found
    for section, entries in sections.items():
        if isinstance(entries, dict):
            for name, value in entries.items():
                found[f"{section}/{name}"] = value
        else:
            found[section] = entries
    return found


def diff(recorded: dict[str, Any], live: dict[str, Any]) -> SchemaDiff:
    """How ``live`` differs from ``recorded``, after :func:`normalise`. Names only."""
    old, new = normalise(recorded), normalise(live)
    old_ops, new_ops = operations(old), operations(new)
    changed_ops = {
        name for name in old_ops.keys() & new_ops.keys() if old_ops[name] != new_ops[name]
    }
    # A change in a path-level key (parameters, summary, ...) changes every operation under it.
    for name in old_ops.keys() & new_ops.keys():
        path = name.split(" ", 1)[1]
        if _path_extras(old, path) != _path_extras(new, path):
            changed_ops.add(name)
    old_comps, new_comps = components(old), components(new)
    other = {
        key
        for key in (old.keys() | new.keys()) - {"paths", "components"}
        if old.get(key) != new.get(key)
    }
    return SchemaDiff(
        operations_added=tuple(sorted(new_ops.keys() - old_ops.keys(), key=_operation_key)),
        operations_removed=tuple(sorted(old_ops.keys() - new_ops.keys(), key=_operation_key)),
        operations_changed=tuple(sorted(changed_ops, key=_operation_key)),
        components_added=tuple(sorted(new_comps.keys() - old_comps.keys())),
        components_removed=tuple(sorted(old_comps.keys() - new_comps.keys())),
        components_changed=tuple(
            sorted(n for n in old_comps.keys() & new_comps.keys() if old_comps[n] != new_comps[n])
        ),
        other_changed=tuple(sorted(other)),
    )
