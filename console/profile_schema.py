"""An engine profile's settings, laid out by the Engine's profile schema (#78; #84 reuses it).

``GET /engine-profiles/schema`` describes what a profile may set on this deployment, so the
profile pages render whatever modules the Engine delivers without a Console release:

- :func:`get_profile_schema_cached` reads the schema at most once a minute per Engine (#64
  convention 13): a module-level dict keyed by the Engine's base URL, behind a lock. A failed
  read is not cached.
- :func:`settings_view` walks the schema beside one version's settings, the deployment's
  ``profile_defaults`` and the schema's own defaults, and returns one :class:`SettingBlock` per
  top-level property of ``schema.properties`` (in order), each a flat list of
  :class:`SettingRow` carrying its depth (the template indents by it).

The walker handles ``properties``; maps (``patternProperties`` / ``additionalProperties``: one
row per key present in this version, the deployment defaults or the schema default); ``anyOf``
with ``null`` (its non-null branch); arrays and leaf objects (one JSON value). An ``anyOf`` with
several non-null branches, a value whose type does not fit its node (marked "doesn't match
schema") and anything at :data:`MAX_DEPTH` render in place as one JSON leaf. A stray ``$ref``
(the Engine inlines them) and every setting the schema does not describe go to "Other
settings": one row per first undescribed dotted path, holding its whole subtree.

Tags match the exact dotted join of property names; a map entry and everything under it take
the map path's tags. Tag entries matching no walked path are listed apart (the "More" details).
"""

import json
import re
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from console.engine.client import EngineClient
from console.engine.models import ProfileSchema, SettingTags
from console.reports import field_anchor

CACHE_TTL_S = 60.0
MAX_DEPTH = 8

_cache: dict[str, tuple[float, ProfileSchema]] = {}
_lock = threading.Lock()


def get_profile_schema_cached(client: EngineClient) -> ProfileSchema:
    """The profile schema of ``client``'s Engine: cached for :data:`CACHE_TTL_S` per process,
    keyed by the Engine's base URL. A failed read raises (``EngineClientError``) and is not
    cached, so the next page retries."""
    key = client.base_url
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached is not None and now - cached[0] < CACHE_TTL_S:
            return cached[1]
    schema = client.get_engine_profile_schema()
    with _lock:
        _cache[key] = (time.monotonic(), schema)
    return schema


def clear_schema_cache() -> None:
    """Forget every cached schema (tests)."""
    with _lock:
        _cache.clear()


# --- cells ------------------------------------------------------------------------------------

CellKind = Literal["plain", "json", "null", "not_set", "absent", "blank"]


@dataclass(frozen=True)
class Cell:
    """One value column: ``plain`` text, a ``json`` value (rendered in ``<pre>``), an explicit
    ``null``, ``not_set`` (this version leaves it out: muted "not set"), ``absent`` (no default:
    "—") or ``blank`` (an interior row's empty column)."""

    kind: CellKind
    text: str = ""


class _Absent:
    def __repr__(self) -> str:
        return "ABSENT"


ABSENT: Any = _Absent()
"""A value a source does not hold (distinct from an explicit ``None``)."""

BLANK = Cell("blank")


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _cell(value: Any, *, missing: CellKind, raw: bool = False) -> Cell:
    """A value as a cell: scalars plain, arrays and objects JSON (every value JSON when
    ``raw``); ``missing`` for an absent value."""
    if value is ABSENT:
        return Cell(missing)
    if value is None:
        return Cell("null")
    if raw or isinstance(value, dict | list):
        return Cell("json", _json(value))
    if isinstance(value, bool | int | float):
        return Cell("plain", json.dumps(value))
    return Cell("plain", str(value))


# --- tags -------------------------------------------------------------------------------------

TAG_LABELS = {"behavioral": "behavioural"}
EMPHASISED = frozenset({"data_producing", "interpretive", "relevant"})


@dataclass(frozen=True)
class Badge:
    """One tag as shown: ``data_producing``, ``interpretive`` and quality ``relevant`` are
    emphasised (``strong``)."""

    text: str
    strong: bool


def badges(tags: SettingTags) -> tuple[Badge, ...]:
    """A setting's three tags: "behavioural"/"data-producing", "interpretive"/"prospective",
    "quality-relevant"/"quality-irrelevant" (an unknown value shown raw, ``_`` as ``-``)."""

    def shown(value: str) -> str:
        return TAG_LABELS.get(value, value.replace("_", "-"))

    return (
        Badge(shown(tags.tag), tags.tag in EMPHASISED),
        Badge(shown(tags.class_), tags.class_ in EMPHASISED),
        Badge(f"quality-{shown(tags.quality)}", tags.quality in EMPHASISED),
    )


# --- rows and blocks --------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingRow:
    """One setting (``leaf``) or one object holding settings (``group``), labelled with its
    dotted path. ``value`` is this version's; ``deployment`` the deployment's
    ``profile_defaults``; ``schema_default`` the schema's ``default``."""

    path: str
    depth: int
    kind: Literal["group", "leaf"]
    title: str = ""
    description: str = ""
    constraints: str = ""
    value: Cell = BLANK
    deployment: Cell = BLANK
    schema_default: Cell = BLANK
    tags: SettingTags | None = None
    mismatch: bool = False

    @property
    def anchor(self) -> str:
        return field_anchor(self.path)

    @property
    def untagged(self) -> bool:
        return self.kind == "leaf" and self.tags is None

    @property
    def badges(self) -> tuple[Badge, ...]:
        return badges(self.tags) if self.tags is not None else ()


@dataclass(frozen=True)
class SettingBlock:
    """One top-level property of the schema (a module, or ``pipeline``): its rows, the
    property's own row first."""

    name: str
    rows: tuple[SettingRow, ...]


@dataclass(frozen=True)
class OtherSetting:
    """A setting the schema does not describe: its dotted path and whole subtree."""

    path: str
    value: Any

    @property
    def text(self) -> str:
        return _json(self.value)


@dataclass(frozen=True)
class SettingsView:
    """Everything the settings section shows. ``schema_ok`` is false when the schema could not
    be read: every setting is then in ``other``."""

    blocks: tuple[SettingBlock, ...] = ()
    other: tuple[OtherSetting, ...] = ()
    delivered: tuple[tuple[str, str], ...] = ()
    unmatched_tags: tuple[tuple[str, tuple[Badge, ...]], ...] = ()
    purposes: tuple[str, ...] = ()
    schema_ok: bool = True

    @property
    def has_more(self) -> bool:
        return bool(self.unmatched_tags or self.purposes)


@dataclass
class _Walk:
    tags: Mapping[str, SettingTags]
    rows: list[SettingRow] = field(default_factory=list)
    other: list[OtherSetting] = field(default_factory=list)
    walked: set[str] = field(default_factory=set)

    def emit(self, row: SettingRow) -> None:
        self.walked.add(row.path)
        self.rows.append(row)

    def undescribed(self, path: str, value: Any) -> None:
        if value is not ABSENT:
            self.other.append(OtherSetting(path, value))

    def walk(
        self,
        node: Any,
        path: str,
        depth: int,
        value: Any,
        deployment: Any,
        schema_default: Any,
        map_tags: SettingTags | None = None,
    ) -> None:
        if not isinstance(node, dict) or "$ref" in node:
            self.undescribed(path, value)
            return
        node, several = _resolved(node)
        if "$ref" in node:
            self.undescribed(path, value)
            return
        if "default" in node:
            schema_default = node["default"]
        tags = self.tags.get(path) or map_tags
        title, description = _text(node, "title"), _text(node, "description")
        constraints = _constraints(node)

        def leaf(*, raw: bool, mismatch: bool = False) -> None:
            self.emit(
                SettingRow(
                    path,
                    depth,
                    "leaf",
                    title,
                    description,
                    constraints,
                    _cell(value, missing="not_set", raw=raw),
                    _cell(deployment, missing="absent", raw=raw),
                    _cell(schema_default, missing="absent", raw=raw),
                    tags,
                    mismatch,
                )
            )

        if several or depth >= MAX_DEPTH:
            leaf(raw=True)
            return
        present = value is not ABSENT and value is not None
        if present and not _fits(node, value):
            leaf(raw=True, mismatch=True)
            return
        if not _is_container(node) or value is None:
            leaf(raw=False)
            return
        self.emit(SettingRow(path, depth, "group", title, description, constraints, tags=tags))
        own = value if isinstance(value, dict) else {}
        properties = node.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for key, child in properties.items():
            self.walk(
                child,
                f"{path}.{key}",
                depth + 1,
                _child(value, key),
                _child(deployment, key),
                _child(schema_default, key),
                map_tags,
            )
        for key in _keys(value, deployment, schema_default):
            if key in properties:
                continue
            entry = _map_entry(node, key)
            if entry is None:
                self.undescribed(f"{path}.{key}", own.get(key, ABSENT))
                continue
            self.walk(
                entry,
                f"{path}.{key}",
                depth + 1,
                _child(value, key),
                _child(deployment, key),
                _child(schema_default, key),
                tags,  # a map entry takes the map path's tags
            )


def settings_view(
    schema: ProfileSchema | None,
    settings: Mapping[str, Any],
    defaults: Mapping[str, Any] | None,
) -> SettingsView:
    """``settings`` (one version's) laid out by ``schema``, beside ``defaults`` (the
    deployment's ``profile_defaults``; ``None`` when unknown: every deployment default shows
    "—"). Without a schema, every setting is in ``other``."""
    if schema is None:
        other = tuple(OtherSetting(key, value) for key, value in settings.items())
        return SettingsView(other=other, schema_ok=False)
    root = schema.schema
    properties = root.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    deployment = defaults if isinstance(defaults, Mapping) else {}
    root_default = root.get("default")
    walk = _Walk(schema.tags)
    blocks: list[SettingBlock] = []
    for name, node in properties.items():
        walk.rows = []
        walk.walk(
            node,
            name,
            0,
            settings.get(name, ABSENT),
            deployment.get(name, ABSENT),
            _child(root_default, name),
        )
        if walk.rows:
            blocks.append(SettingBlock(name, tuple(walk.rows)))
    for key, value in settings.items():
        if key not in properties:
            walk.undescribed(key, value)
    unmatched = tuple(
        (path, badges(tags)) for path, tags in schema.tags.items() if path not in walk.walked
    )
    return SettingsView(
        blocks=tuple(blocks),
        other=tuple(walk.other),
        delivered=tuple((module, ", ".join(levels)) for module, levels in schema.delivered.items()),
        unmatched_tags=unmatched,
        purposes=tuple(schema.purposes),
    )


def _child(value: Any, key: str) -> Any:
    """``value[key]`` when ``value`` is an object holding ``key``, else :data:`ABSENT`."""
    if isinstance(value, dict) and key in value:
        return value[key]
    return ABSENT


def _resolved(node: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
    """``node`` with an ``anyOf`` of one non-null branch and ``null`` folded into that branch
    (the outer title, description and default kept), and whether it held several non-null
    branches (rendered as one JSON leaf)."""
    branches = node.get("anyOf")
    if not isinstance(branches, list):
        return node, False
    non_null = [b for b in branches if not (isinstance(b, dict) and b.get("type") == "null")]
    if len(non_null) != 1 or not isinstance(non_null[0], dict):
        return node, True
    outer = {k: v for k, v in node.items() if k != "anyOf"}
    return {**non_null[0], **outer}, False


JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _fits(node: Mapping[str, Any], value: Any) -> bool:
    """Whether ``value`` (present, not ``None``) has the type ``node`` declares (any type when
    it declares none)."""
    declared = node.get("type")
    if not isinstance(declared, str) or declared not in JSON_TYPES:
        return True
    if isinstance(value, bool) and declared != "boolean":
        return False
    return isinstance(value, JSON_TYPES[declared])


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _constraints(node: Mapping[str, Any]) -> str:
    """The muted line's options and bounds: "one of: a, b · min 1 · max 50"."""
    parts: list[str] = []
    options = node.get("enum")
    if isinstance(options, list) and options:
        shown = ", ".join("null" if o is None else str(o) for o in options)
        parts.append(f"one of: {shown}")
    for key, word in (("minimum", "min"), ("maximum", "max")):
        bound = node.get(key)
        if isinstance(bound, int | float) and not isinstance(bound, bool):
            parts.append(f"{word} {_number(bound)}")
    return " · ".join(parts)


def _text(node: Mapping[str, Any], key: str) -> str:
    value = node.get(key)
    return value.strip() if isinstance(value, str) else ""


def _map_entry(node: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    """The schema of map key ``key``: the first ``patternProperties`` pattern it matches, else
    an ``additionalProperties`` schema; ``None`` when neither describes it."""
    patterns = node.get("patternProperties")
    if isinstance(patterns, dict):
        for pattern, entry in patterns.items():
            try:
                matched = re.search(pattern, key) is not None
            except re.error:
                matched = False
            if matched and isinstance(entry, dict):
                return entry
    extra = node.get("additionalProperties")
    return extra if isinstance(extra, dict) else None


def _is_container(node: Mapping[str, Any]) -> bool:
    """Whether ``node`` describes settings inside it (properties or map entries)."""
    return (
        isinstance(node.get("properties"), dict)
        or isinstance(node.get("patternProperties"), dict)
        or isinstance(node.get("additionalProperties"), dict)
    )


def _keys(*values: Any) -> Iterator[str]:
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            for key in value:
                if key not in seen:
                    seen.add(key)
                    yield key
