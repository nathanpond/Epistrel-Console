"""A Django form generated from the Engine's profile schema (#84; #85 reuses it for overrides).

``GET /engine-profiles/schema`` describes what a profile may set on this deployment.
:func:`build_tree` walks it into fieldsets (:class:`Group`) and fields (:class:`Leaf`), and
:class:`SchemaSettingsForm` turns them into form fields, so a newly delivered module is editable
without a Console release:

- ``$ref`` (local) resolves and ``allOf`` merges; ``anyOf [X, null]`` (or ``type: [X, "null"]``,
  or an enum holding ``null``) is a nullable X, its default and bounds taken from the non-null
  branch when the property itself lacks them.
- booleans → a "(default)/Yes/No" select; enums → a select; integers and numbers → number
  inputs (``step=1`` / ``step="any"``, bounds as ``min``/``max``); strings → text; an ``anyOf``
  of non-null scalars (``response_length``) → text parsed against each branch in order; arrays
  of enum scalars → a multi-select; other scalar arrays → one item per line.
- objects with ``properties`` nest as fieldsets (to depth :data:`MAX_DEPTH`); anything else the
  generator can't map (``oneOf``, ``patternProperties``, ``additionalProperties`` schemas,
  ``maxProperties``, objects without properties, arrays of objects, deeper nesting) is one JSON
  textarea for that subtree.

Field names are the Engine path joined with ``__`` (#64 convention 6), rooted at the document's
key in the request body (``settings__memory__rolling_summary__window_messages``); a nullable
field's "null" checkbox is ``null__<name>``.

Every field has an **unset** state (an empty input, or the "(default…)" option), which leaves
the key out. In ``profile`` mode the form is sparse: :meth:`SchemaSettingsForm.settings` holds
exactly the set fields (an edit drops a stored key whose field was cleared), plus the "Settings
not in this deployment's schema" textarea (stored keys the schema does not describe, sent as
entered). In ``defaults`` mode (the deployment's ``profile_defaults``) the fields are pre-filled
with the stored values, unset means unchanged, clearing a stored value is an error ("null" is a
value there), and :meth:`SchemaSettingsForm.defaults_patch` is the merge patch of changed leaves.

Values are parsed to the schema type before they are compared with the stored JSON (``10``
equals ``10.0``). A changed value outside the schema's bounds is a form error (zero Engine
calls); an unchanged stored one outside them only warns, and is sent as stored.

:meth:`SchemaSettingsForm.place` puts an Engine path (``body.settings.x.y``, ``settings.x.y``
or bare ``x.y``) on its field, on the JSON textarea holding it (naming the sub-path), on the
"not in schema" textarea, or atop the form; :meth:`~SchemaSettingsForm.place_validation` places
a validate result and :meth:`~SchemaSettingsForm.place_invalid_profile` a 422
``invalid_profile``.
"""

import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from django import forms
from django.core.exceptions import ValidationError

from console.engine.client import EngineError
from console.engine.models import (
    MissingDependency,
    ProfileSchema,
    ProfileValidation,
    ProfileValidationWarning,
    SettingTags,
)
from console.forms.fields import _refuse_constant, _refuse_duplicates
from console.merge_patch import _same
from console.profile_schema import ABSENT, MAX_DEPTH, Badge, badges
from console.reports import field_anchor

LeafKind = Literal[
    "boolean", "enum", "integer", "number", "string", "choice", "enum_list", "lines", "json"
]
Mode = Literal["profile", "defaults"]

SEP = "__"
NULL_PREFIX = "null__"
OTHER = "other_settings"
OTHER_LABEL = "Settings not in this deployment's schema"
BODY_PREFIX = "body."
SCALARS = ("string", "integer", "number", "boolean")
DASH = "\u2013"  # a range's en dash: "1" DASH "500"

CLEARED_NULLABLE = "Tick “null” to set null"
CLEARED = "Enter a value (profile defaults can't be left unset)"
IS_FORM_FIELD = "`{path}` is a form field — set it there"
SCHEMA_CHANGED = "The profile schema changed — check your input"
NOT_REMOVABLE = "Keys can't be removed from profile defaults: {keys}"


# --- the tree -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leaf:
    """One form field: a setting, or a JSON subtree (``kind="json"``). ``path`` is relative to
    the settings document."""

    path: tuple[str, ...]
    kind: LeafKind
    nullable: bool = False
    default: Any = ABSENT  # the schema's ``default``
    title: str = ""
    description: str = ""
    options: tuple[Any, ...] = ()  # enum / enum_list members (``null`` left out)
    item_kind: str = "string"  # lines: the items' type
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    branches: tuple[Mapping[str, Any], ...] = ()  # choice: the non-null branches, in order
    badges: tuple[Badge, ...] = ()
    untagged: bool = False
    purposes: tuple[str, ...] = ()  # a ``models.purposes`` subtree's routable purposes

    @property
    def dotted(self) -> str:
        return ".".join(self.path)

    @property
    def has_bounds(self) -> bool:
        return any(
            b is not None
            for b in (
                self.minimum,
                self.maximum,
                self.exclusive_minimum,
                self.exclusive_maximum,
                self.min_length,
                self.max_length,
            )
        )


@dataclass(frozen=True)
class Group:
    """A fieldset: an object whose ``properties`` the form lays out."""

    path: tuple[str, ...]
    title: str = ""
    description: str = ""
    children: tuple[Leaf | Group, ...] = ()

    @property
    def dotted(self) -> str:
        return ".".join(self.path)

    @property
    def name(self) -> str:
        return self.path[-1]


type Node = Leaf | Group


def schema_digest(schema: ProfileSchema) -> str:
    """The sha256 of the canonical profile schema (schema, tags, delivered, purposes): an edit
    page signs it, and a POST against another schema re-renders instead of writing."""
    canonical = json.dumps(asdict(schema), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pointer(root: Mapping[str, Any], ref: str) -> Mapping[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    target: Any = root
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or token not in target:
            return None
        target = target[token]
    return target if isinstance(target, dict) else None


def _resolve(node: Any, root: Mapping[str, Any], depth: int = 0) -> dict[str, Any]:
    """``node`` with local ``$ref`` followed (its own keys over the target's) and ``allOf``
    merged (``properties`` merged, the outer keys winning). An unresolvable ``$ref`` stays (the
    node then maps to JSON)."""
    if not isinstance(node, dict) or depth > 32:
        return {}
    out = dict(node)
    ref = out.get("$ref")
    if isinstance(ref, str):
        target = _pointer(root, ref)
        if target is None:
            return out
        rest = {k: v for k, v in out.items() if k != "$ref"}
        out = {**_resolve(target, root, depth + 1), **rest}
    parts = out.pop("allOf", None)
    if isinstance(parts, list):
        merged: dict[str, Any] = {}
        properties: dict[str, Any] = {}
        for part in parts:
            resolved = _resolve(part, root, depth + 1)
            properties.update(resolved.get("properties") or {})
            merged.update({k: v for k, v in resolved.items() if k != "properties"})
        own = out.get("properties")
        if isinstance(own, dict):
            properties.update(own)
        out = {**merged, **out}
        if properties:
            out["properties"] = properties
    return out


def _is_null(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null"


BRANCH_KEYS = ("default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")


def _fold(node: dict[str, Any], root: Mapping[str, Any]) -> tuple[dict[str, Any], bool, bool]:
    """``(node, nullable, several)``: an ``anyOf`` with ``null`` folded into its one non-null
    branch (``several`` when it holds more than one), ``type: [X, "null"]`` into X."""
    nullable = False
    declared = node.get("type")
    if isinstance(declared, list):
        kinds = [t for t in declared if t != "null"]
        nullable = len(kinds) < len(declared)
        node = {**node, "type": kinds[0] if len(kinds) == 1 else kinds}
    enum = node.get("enum")
    if isinstance(enum, list) and None in enum:
        nullable = True
    branches = node.get("anyOf")
    if not isinstance(branches, list):
        return node, nullable, False
    resolved = [_resolve(b, root) for b in branches]
    non_null = [b for b in resolved if not _is_null(b)]
    nullable = nullable or len(non_null) < len(resolved)
    outer = {k: v for k, v in node.items() if k != "anyOf"}
    if len(non_null) != 1:
        return {**outer, "anyOf": non_null}, nullable, True
    folded, inner_nullable, several = _fold(non_null[0], root)
    return {**folded, **outer}, nullable or inner_nullable, several


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _first(node: Mapping[str, Any], branches: Sequence[Mapping[str, Any]], key: str) -> Any:
    """``node[key]``, else the first branch's carrying it, else :data:`ABSENT`."""
    if key in node:
        return node[key]
    for branch in branches:
        if key in branch:
            return branch[key]
    return ABSENT


def _scalar_branch(node: Mapping[str, Any]) -> bool:
    kind = node.get("type")
    if isinstance(kind, str) and kind in SCALARS and "properties" not in node:
        return True
    return "enum" in node and "type" not in node


def _tags_under(tags: Mapping[str, SettingTags], dotted: str) -> tuple[Badge, ...]:
    """The union of the badges of every tagged path at or under ``dotted`` (a JSON subtree)."""
    seen: dict[str, Badge] = {}
    for path, tag in tags.items():
        if path == dotted or path.startswith(f"{dotted}."):
            for badge in badges(tag):
                seen.setdefault(badge.text, badge)
    return tuple(seen.values())


class _Builder:
    def __init__(self, schema: ProfileSchema) -> None:
        self.root = schema.schema
        self.tags = schema.tags
        self.purposes = tuple(schema.purposes)

    def node(self, raw: Any, path: tuple[str, ...], depth: int) -> Node:
        node = _resolve(raw, self.root)
        node, nullable, several = _fold(node, self.root)
        title = _text(node, "title")
        description = _text(node, "description")
        dotted = ".".join(path)
        tags = self.tags.get(dotted)
        common: dict[str, Any] = {
            "nullable": nullable,
            "title": title,
            "description": description,
            "badges": badges(tags) if tags is not None else (),
            "untagged": tags is None,
        }
        if several:
            branches = tuple(b for b in node.get("anyOf", []) if isinstance(b, dict))
            if branches and all(_scalar_branch(b) for b in branches):
                return Leaf(
                    path,
                    "choice",
                    default=_first(node, branches, "default"),
                    branches=branches,
                    **common,
                )
            return self.json(path, node, nullable)
        if "$ref" in node or "oneOf" in node:
            return self.json(path, node, nullable)
        kind = node.get("type")
        properties = node.get("properties")
        if kind == "object" or isinstance(properties, dict):
            if (
                not isinstance(properties, dict)
                or not properties
                or isinstance(node.get("patternProperties"), dict)
                or isinstance(node.get("additionalProperties"), dict)
                or "maxProperties" in node
                or depth >= MAX_DEPTH
                or nullable
            ):
                return self.json(path, node, nullable)
            children = tuple(
                self.node(child, (*path, key), depth + 1) for key, child in properties.items()
            )
            return Group(path, title, description, children)
        default = node.get("default", ABSENT)
        enum = node.get("enum")
        if isinstance(enum, list) and enum:
            options = tuple(v for v in enum if v is not None)
            if all(isinstance(v, str | int | float | bool) for v in options):
                return Leaf(path, "enum", default=default, options=options, **common)
            return self.json(path, node, nullable)
        if kind == "array":
            items = _resolve(node.get("items"), self.root)
            items, _, items_several = _fold(items, self.root)
            item_enum = items.get("enum")
            if items_several:
                return self.json(path, node, nullable)
            if isinstance(item_enum, list) and item_enum:
                options = tuple(v for v in item_enum if v is not None)
                return Leaf(path, "enum_list", default=default, options=options, **common)
            item_kind = items.get("type")
            if isinstance(item_kind, str) and item_kind in SCALARS:
                return Leaf(path, "lines", default=default, item_kind=item_kind, **common)
            return self.json(path, node, nullable)
        if kind == "boolean":
            return Leaf(path, "boolean", default=default, **common)
        if kind in ("integer", "number"):
            return Leaf(
                path,
                kind,
                default=default,
                minimum=_number(node.get("minimum")),
                maximum=_number(node.get("maximum")),
                exclusive_minimum=_number(node.get("exclusiveMinimum")),
                exclusive_maximum=_number(node.get("exclusiveMaximum")),
                **common,
            )
        if kind == "string":
            return Leaf(
                path,
                "string",
                default=default,
                min_length=_int(node.get("minLength")),
                max_length=_int(node.get("maxLength")),
                **common,
            )
        return self.json(path, node, nullable)

    def json(self, path: tuple[str, ...], node: Mapping[str, Any], nullable: bool) -> Leaf:
        dotted = ".".join(path)
        shown = _tags_under(self.tags, dotted)
        purposes = self.purposes if path and path[-1] == "purposes" else ()
        return Leaf(
            path,
            "json",
            nullable=False,  # JSON ``null`` is typed in the box itself
            default=node.get("default", ABSENT),
            title=_text(node, "title"),
            description=_text(node, "description"),
            badges=shown,
            untagged=not shown,
            purposes=purposes,
        )


def _text(node: Mapping[str, Any], key: str) -> str:
    value = node.get(key)
    return value.strip() if isinstance(value, str) else ""


def build_tree(schema: ProfileSchema) -> tuple[Node, ...]:
    """The schema's top-level properties, in order, as fieldsets and fields."""
    builder = _Builder(schema)
    properties = _resolve(schema.schema, schema.schema).get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(builder.node(child, (key,), 0) for key, child in properties.items())


def leaves(nodes: Sequence[Node]) -> Iterator[Leaf]:
    for node in nodes:
        if isinstance(node, Leaf):
            yield node
        else:
            yield from leaves(node.children)


def groups(nodes: Sequence[Node]) -> Iterator[Group]:
    for node in nodes:
        if isinstance(node, Group):
            yield node
            yield from groups(node.children)


# --- values -------------------------------------------------------------------------------------


def get_path(document: Any, path: Sequence[str]) -> Any:
    """The value at ``path`` in ``document``, or :data:`ABSENT`."""
    value = document
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return ABSENT
        value = value[key]
    return value


def set_path(document: dict[str, Any], path: Sequence[str], value: Any) -> None:
    target = document
    for key in path[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        target = child
    target[path[-1]] = value


def deep_merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """``extra`` laid over ``base`` (objects merge, anything else replaces)."""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def shown_value(value: Any) -> str:
    """A default as the "default: X" text: strings raw, everything else JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value, ensure_ascii=False)


def _option_value(value: Any) -> str:
    """A select option's value: a string as itself, anything else as JSON."""
    return value if isinstance(value, str) else json.dumps(value)


def _number_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _strict_json(text: str) -> Any:
    return json.loads(text, parse_constant=_refuse_constant, object_pairs_hook=_refuse_duplicates)


def _parse_int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError as exc:
        raise ValidationError("Enter a whole number.") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ValidationError("Enter a whole number.")
    return int(number)


def _parse_number(text: str) -> int | float:
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError as exc:
        raise ValidationError("Enter a number.") from exc
    if not math.isfinite(number):
        raise ValidationError("Enter a finite number.")
    return number


def _parse_bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    raise ValidationError("Enter true or false.")


def _parse_scalar(kind: str, text: str) -> Any:
    if kind == "integer":
        return _parse_int(text)
    if kind == "number":
        return _parse_number(text)
    if kind == "boolean":
        return _parse_bool(text)
    return text


def bounds_text(leaf: Leaf | Mapping[str, Any]) -> str:
    """A leaf's (or a branch's) bounds as "min to max" (an en dash), "≥ min", "> min",
    "length 1 to 120"…"""
    if isinstance(leaf, Leaf):
        lo, hi = leaf.minimum, leaf.maximum
        xlo, xhi = leaf.exclusive_minimum, leaf.exclusive_maximum
        lmin, lmax = leaf.min_length, leaf.max_length
    else:
        lo, hi = _number(leaf.get("minimum")), _number(leaf.get("maximum"))
        xlo, xhi = _number(leaf.get("exclusiveMinimum")), _number(leaf.get("exclusiveMaximum"))
        lmin, lmax = _int(leaf.get("minLength")), _int(leaf.get("maxLength"))
    parts: list[str] = []
    if lo is not None and hi is not None:
        parts.append(f"{_number_text(lo)}{DASH}{_number_text(hi)}")
    elif lo is not None:
        parts.append(f"≥ {_number_text(lo)}")
    elif hi is not None:
        parts.append(f"≤ {_number_text(hi)}")
    if xlo is not None:
        parts.append(f"> {_number_text(xlo)}")
    if xhi is not None:
        parts.append(f"< {_number_text(xhi)}")
    if lmin is not None or lmax is not None:
        parts.append(
            f"length {lmin if lmin is not None else 0}{DASH}{lmax if lmax is not None else '…'}"
        )
    return ", ".join(parts)


def _bounds_error(value: Any, node: Leaf | Mapping[str, Any]) -> str | None:
    """Why ``value`` is outside ``node``'s bounds, or ``None``."""
    shown = bounds_text(node)
    if isinstance(node, Leaf):
        lo, hi = node.minimum, node.maximum
        xlo, xhi = node.exclusive_minimum, node.exclusive_maximum
        lmin, lmax = node.min_length, node.max_length
        options: tuple[Any, ...] = node.options if node.kind == "enum" else ()
    else:
        lo, hi = _number(node.get("minimum")), _number(node.get("maximum"))
        xlo, xhi = _number(node.get("exclusiveMinimum")), _number(node.get("exclusiveMaximum"))
        lmin, lmax = _int(node.get("minLength")), _int(node.get("maxLength"))
        enum = node.get("enum")
        options = tuple(enum) if isinstance(enum, list) else ()
    if options and value not in options:
        return "Choose one of: " + " / ".join(shown_value(o) for o in options)
    number = _number(value)
    if number is not None:
        if (lo is not None and number < lo) or (hi is not None and number > hi):
            return f"Must be within {shown}."
        if (xlo is not None and number <= xlo) or (xhi is not None and number >= xhi):
            return f"Must be within {shown}."
    too_short = isinstance(value, str) and lmin is not None and len(value) < lmin
    too_long = isinstance(value, str) and lmax is not None and len(value) > lmax
    if too_short or too_long:
        return f"Must be {shown} characters."
    return None


def choice_help(branches: Sequence[Mapping[str, Any]]) -> str:
    """An ``anyOf`` of scalars as help: enum values joined " / ", numeric ranges "min to max",
    branches joined ", or "."""
    shown: list[str] = []
    for branch in branches:
        enum = branch.get("enum")
        if isinstance(enum, list):
            shown.append(" / ".join(shown_value(v) for v in enum))
            continue
        kind = branch.get("type", "value")
        bounds = bounds_text(branch)
        shown.append(f"{bounds} ({kind})" if bounds else str(kind))
    return ", or ".join(shown)


def _parse_choice(leaf: Leaf, text: str) -> tuple[Any, Mapping[str, Any] | None]:
    """``text`` parsed against each branch in order: the first that parses and holds within
    its bounds; else the first that parses (with that branch, for the bounds check)."""
    candidates: list[tuple[Any, Mapping[str, Any]]] = []
    for branch in leaf.branches:
        enum = branch.get("enum")
        kind = branch.get("type")
        try:
            if isinstance(enum, list):
                matched = [v for v in enum if _option_value(v) == text]
                if not matched:
                    continue
                value: Any = matched[0]
            elif isinstance(kind, str):
                value = _parse_scalar(kind, text)
            else:
                continue
        except ValidationError:
            continue
        if _bounds_error(value, branch) is None:
            return value, None
        candidates.append((value, branch))
    if candidates:
        return candidates[0]
    raise ValidationError(f"Enter {choice_help(leaf.branches)}.")


# --- the form -----------------------------------------------------------------------------------


class _JsonSubtree(forms.CharField):
    """A JSON textarea for a subtree the generator can't map: any JSON value, strict parse
    (no ``NaN``, no duplicate keys); blank = unset."""

    widget = forms.Textarea(attrs={"rows": 6, "cols": 80, "spellcheck": "false"})


def _placeholder(text: str) -> dict[str, str]:
    return {"placeholder": text} if text else {}


@dataclass
class ShownLeaf:
    """One field as the template renders it."""

    leaf: Leaf
    field: forms.BoundField
    null_field: forms.BoundField | None
    default_text: str
    help: str
    warnings: list[str]
    anchor: str
    is_set: bool

    @property
    def errors(self) -> Any:
        return self.field.errors

    @property
    def has_error(self) -> bool:
        return bool(self.field.errors) or bool(self.null_field and self.null_field.errors)

    @property
    def is_group(self) -> bool:
        return False


@dataclass
class ShownGroup:
    """One fieldset as the template renders it: open when anything inside is set or wrong."""

    group: Group
    children: list[ShownLeaf | ShownGroup]
    errors: list[str]
    anchor: str
    open: bool = False

    @property
    def is_group(self) -> bool:
        return True


@dataclass
class PlacedStories:
    """A 422 ``invalid_profile`` naming stories (or profile versions) that could not run."""

    stories: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    count: int | None = None
    extra: list[tuple[str, str]] = field(default_factory=list)


class SchemaSettingsForm(forms.Form):
    """The schema's settings as a form (see the module docstring).

    ``root`` is the document's key in the request body (``settings``, ``profile_defaults``,
    #85's ``overrides``): it prefixes field names and is stripped from Engine paths. ``stored``
    is the document as read (the version's settings, or ``profile_defaults``; ``None`` on a
    create). ``defaults`` is the deployment's ``profile_defaults`` (``None`` when unread) for
    the "default: X" labels in ``profile`` mode.
    """

    def __init__(
        self,
        data: Any = None,
        *,
        schema: ProfileSchema,
        root: str = "settings",
        stored: Mapping[str, Any] | None = None,
        defaults: Mapping[str, Any] | None = None,
        mode: Mode = "profile",
        other: bool | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.schema = schema
        self.root = root
        self.mode: Mode = mode
        self.stored: dict[str, Any] = dict(stored or {})
        self.defaults = defaults
        self.tree = build_tree(schema)
        self.leaf_by_name: dict[str, Leaf] = {}
        self.leaf_by_path: dict[str, Leaf] = {}
        self.group_by_path: dict[str, Group] = {}
        self.group_errors: dict[str, list[str]] = {}
        self.field_warnings: dict[str, list[str]] = {}
        self.top_warnings: list[str] = []
        self._values: dict[str, Any] = {}
        self.has_other = (mode == "profile") if other is None else other
        for group in groups(self.tree):
            self.group_by_path[group.dotted] = group
        for leaf in leaves(self.tree):
            self._add_leaf(leaf)
        if self.has_other:
            leftover = self.undescribed(self.stored)
            self.fields[OTHER] = forms.CharField(
                required=False,
                label=OTHER_LABEL,
                widget=forms.Textarea(attrs={"rows": 4, "cols": 80, "spellcheck": "false"}),
                help_text="A JSON object laid over the fields above, sent as entered.",
                initial=json.dumps(leftover, indent=2, ensure_ascii=False) if leftover else "",
            )

    # --- building ---------------------------------------------------------------------------

    def field_name(self, leaf: Leaf) -> str:
        return SEP.join((self.root, *leaf.path))

    def null_name(self, leaf: Leaf) -> str:
        return f"{NULL_PREFIX}{self.field_name(leaf)}"

    def default_of(self, leaf: Leaf) -> tuple[Any, str]:
        """``(value, label)`` of what an unset field gets: the deployment's ``profile_defaults``
        value ("default"), else the schema's ("schema default"); ``ABSENT`` when neither."""
        if self.mode == "profile" and self.defaults is not None:
            value = get_path(self.defaults, leaf.path)
            if value is not ABSENT:
                return value, "default"
        if leaf.default is not ABSENT:
            return leaf.default, "schema default"
        return ABSENT, ""

    def default_text(self, leaf: Leaf) -> str:
        value, label = self.default_of(leaf)
        return f"{label}: {shown_value(value)}" if value is not ABSENT else ""

    def _add_leaf(self, leaf: Leaf) -> None:
        name = self.field_name(leaf)
        self.leaf_by_name[name] = leaf
        self.leaf_by_path[leaf.dotted] = leaf
        default = self.default_text(leaf)
        unset_label = (
            "(not set)" if self.mode == "defaults" else f"({default})" if default else "(default)"
        )
        stored = get_path(self.stored, leaf.path)
        initial: Any = None
        if stored is not ABSENT and stored is not None:
            initial = self._initial(leaf, stored)
        attrs: dict[str, Any] = {}
        widget: forms.Widget
        built: forms.Field
        if leaf.kind == "boolean":
            choices = [("", unset_label), ("true", "Yes"), ("false", "No")]
            built = forms.ChoiceField(choices=choices, required=False)
        elif leaf.kind == "enum":
            options = [(_option_value(v), shown_value(v)) for v in leaf.options]
            if initial is not None and initial not in {v for v, _ in options}:
                options.append((initial, f"{initial} (not in schema)"))
            built = forms.ChoiceField(choices=[("", unset_label), *options], required=False)
        elif leaf.kind == "enum_list":
            options = [(_option_value(v), shown_value(v)) for v in leaf.options]
            built = forms.MultipleChoiceField(
                choices=options, required=False, widget=forms.SelectMultiple
            )
        elif leaf.kind in ("integer", "number"):
            attrs = {"step": "1" if leaf.kind == "integer" else "any", **_placeholder(default)}
            if leaf.minimum is not None:
                attrs["min"] = _number_text(leaf.minimum)
            if leaf.maximum is not None:
                attrs["max"] = _number_text(leaf.maximum)
            widget = forms.NumberInput(attrs=attrs)
            built = forms.CharField(required=False, widget=widget, strip=True)
        elif leaf.kind in ("string", "choice"):
            widget = forms.TextInput(attrs=_placeholder(default))
            built = forms.CharField(required=False, widget=widget, strip=leaf.kind == "choice")
        elif leaf.kind == "lines":
            widget = forms.Textarea(attrs={"rows": 3, "cols": 40, **_placeholder(default)})
            built = forms.CharField(required=False, widget=widget)
        else:
            built = _JsonSubtree(required=False, strip=True)
            if default:
                built.widget.attrs.update(_placeholder(default))
        built.label = leaf.path[-1]
        built.initial = initial
        built.help_text = self._help(leaf)
        self.fields[name] = built
        if leaf.nullable:
            self.fields[self.null_name(leaf)] = forms.BooleanField(
                required=False, label="null", initial=stored is None
            )

    def _initial(self, leaf: Leaf, stored: Any) -> Any:
        if leaf.kind == "boolean":
            return "true" if stored is True else "false" if stored is False else None
        if leaf.kind == "enum":
            return _option_value(stored)
        if leaf.kind == "enum_list":
            return [_option_value(v) for v in stored] if isinstance(stored, list) else None
        if leaf.kind in ("integer", "number"):
            return _number_text(stored) if _number(stored) is not None else json.dumps(stored)
        if leaf.kind == "string":
            return stored if isinstance(stored, str) else json.dumps(stored)
        if leaf.kind == "choice":
            return stored if isinstance(stored, str) else _number_text(stored)
        if leaf.kind == "lines":
            if isinstance(stored, list):
                return "\n".join(shown_value(v) for v in stored)
            return json.dumps(stored)
        return json.dumps(stored, indent=2, ensure_ascii=False)

    def _help(self, leaf: Leaf) -> str:
        parts = [leaf.description] if leaf.description else []
        if leaf.kind == "choice":
            parts.append(choice_help(leaf.branches))
        elif leaf.has_bounds:
            parts.append(bounds_text(leaf))
        if leaf.kind == "lines":
            parts.append(f"One {leaf.item_kind} per line.")
        if leaf.purposes:
            parts.append("Purposes: " + ", ".join(leaf.purposes))
        return " · ".join(parts)

    def undescribed(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """The parts of ``document`` the schema does not describe (no field holds them)."""
        out: dict[str, Any] = {}

        def walk(value: Any, nodes: Sequence[Node], path: tuple[str, ...]) -> None:
            if not isinstance(value, dict):
                return
            by_key = {node.path[-1]: node for node in nodes}
            for key, item in value.items():
                node = by_key.get(key)
                if node is None:
                    set_path(out, (*path, key), item)
                elif isinstance(node, Group):
                    if isinstance(item, dict):
                        walk(item, node.children, (*path, key))
                    else:
                        set_path(out, (*path, key), item)

        walk(document, self.tree, ())
        return out

    # --- cleaning ---------------------------------------------------------------------------

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        self._values = {}
        for name, leaf in self.leaf_by_name.items():
            if name in self.errors:
                continue
            try:
                value, branch = self._parse(name, leaf)
            except ValidationError as exc:
                self.add_error(name, exc)
                continue
            self._values[name] = value
            self._check(name, leaf, value, branch)
        if self.has_other:
            self._clean_other()
        return cleaned

    def _raw(self, name: str) -> Any:
        return self.cleaned_data.get(name)

    def _parse(self, name: str, leaf: Leaf) -> tuple[Any, Mapping[str, Any] | None]:
        """The field's value parsed to its schema type, or :data:`ABSENT` (unset)."""
        if leaf.nullable and self.cleaned_data.get(self.null_name(leaf)):
            return None, None
        raw = self._raw(name)
        if leaf.kind == "enum_list":
            if not raw:
                return ABSENT, None
            by_value = {_option_value(v): v for v in leaf.options}
            return [by_value[v] for v in raw if v in by_value], None
        if raw is None or raw == "":
            return ABSENT, None
        text = str(raw)
        if leaf.kind == "boolean":
            return text == "true", None
        if leaf.kind == "enum":
            by_value = {_option_value(v): v for v in leaf.options}
            if text in by_value:
                return by_value[text], None
            stored = get_path(self.stored, leaf.path)
            if stored is not ABSENT and _option_value(stored) == text:
                return stored, None  # a stored value no longer offered, kept as stored
            raise ValidationError("Choose one of the listed values.")
        if leaf.kind == "integer":
            return _parse_int(text.strip()), None
        if leaf.kind == "number":
            return _parse_number(text.strip()), None
        if leaf.kind == "string":
            return text, None
        if leaf.kind == "choice":
            return _parse_choice(leaf, text.strip())
        if leaf.kind == "lines":
            items = list(dict.fromkeys(line.strip() for line in text.splitlines() if line.strip()))
            if not items:
                return ABSENT, None
            return [_parse_scalar(leaf.item_kind, item) for item in items], None
        try:
            return _strict_json(text), None
        except ValueError as exc:
            raise ValidationError(f"Enter valid JSON: {exc}") from exc

    def _check(self, name: str, leaf: Leaf, value: Any, branch: Mapping[str, Any] | None) -> None:
        """Bounds on a changed value (an error); an unchanged one only warns. In ``defaults``
        mode a cleared stored value is an error."""
        stored = get_path(self.stored, leaf.path)
        if value is ABSENT:
            if self.mode == "defaults" and stored is not ABSENT:
                self.add_error(name, CLEARED_NULLABLE if leaf.nullable else CLEARED)
            return
        if value is None or leaf.kind in ("json", "enum_list", "lines"):
            return
        reason = _bounds_error(value, branch if branch is not None else leaf)
        if reason is None:
            return
        if stored is not ABSENT and _same(stored, value):
            bounds = bounds_text(branch if branch is not None else leaf)
            self.field_warnings.setdefault(name, []).append(
                f"Outside this deployment's bounds ({bounds})"
            )
            return
        self.add_error(name, reason)

    def _clean_other(self) -> None:
        text = (self.cleaned_data.get(OTHER) or "").strip()
        if not text:
            self._values[OTHER] = {}
            return
        try:
            parsed = _strict_json(text)
        except ValueError as exc:
            self.add_error(OTHER, f"Enter valid JSON: {exc}")
            return
        if not isinstance(parsed, dict):
            self.add_error(OTHER, "Enter a JSON object ({…}).")
            return
        for dotted in _paths(parsed):
            if dotted in self.leaf_by_path:
                self.add_error(OTHER, IS_FORM_FIELD.format(path=dotted))
                return
        self._values[OTHER] = parsed

    # --- output -----------------------------------------------------------------------------

    def value_of(self, leaf: Leaf) -> Any:
        return self._values.get(self.field_name(leaf), ABSENT)

    def settings(self) -> dict[str, Any]:
        """The sparse document: every set field, then the "not in schema" object over it."""
        document: dict[str, Any] = {}
        for leaf in leaves(self.tree):
            value = self.value_of(leaf)
            if value is not ABSENT:
                set_path(document, leaf.path, value)
        other = self._values.get(OTHER)
        if isinstance(other, dict) and other:
            document = deep_merge(document, other)
        return document

    def defaults_patch(self) -> dict[str, Any]:
        """``defaults`` mode: the merge patch of changed leaves (nested objects; ``null`` is a
        value). A JSON subtree sends its changed leaves; removing a key from it is a form error
        (merge patch can't express it here). Call after ``is_valid()``."""
        patch: dict[str, Any] = {}
        for leaf in leaves(self.tree):
            value = self.value_of(leaf)
            if value is ABSENT:
                continue
            stored = get_path(self.stored, leaf.path)
            if stored is not ABSENT and _same(stored, value):
                continue
            if leaf.kind == "json" and isinstance(stored, dict) and isinstance(value, dict):
                removed = _removed(stored, value)
                if removed:
                    self.add_error(
                        self.field_name(leaf), NOT_REMOVABLE.format(keys=", ".join(removed))
                    )
                    continue
                changed = _changed_leaves(stored, value)
                if changed:
                    set_path(patch, leaf.path, changed)
                continue
            set_path(patch, leaf.path, value)
        return patch

    # --- placement --------------------------------------------------------------------------

    def _strip(self, path: str) -> str:
        if path.startswith(BODY_PREFIX):
            path = path[len(BODY_PREFIX) :]
        root = f"{self.root}."
        if path.startswith(root):
            path = path[len(root) :]
        elif path == self.root:
            path = ""
        return path

    def locate(self, path: str) -> tuple[str, str, str]:
        """Where an Engine path goes: ``("field", name, sub-path)``, ``("group", path, "")``,
        ``("other", OTHER, path)`` or ``("form", "", path)``."""
        dotted = self._strip(path).replace("[", ".").replace("]", "")
        if dotted in self.leaf_by_path:
            return "field", self.field_name(self.leaf_by_path[dotted]), ""
        parts = dotted.split(".") if dotted else []
        for end in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:end])
            leaf = self.leaf_by_path.get(prefix)
            if leaf is not None:
                return "field", self.field_name(leaf), ".".join(parts[end:])
        if dotted in self.group_by_path:
            return "group", dotted, ""
        if self.has_other and dotted:
            return "other", OTHER, dotted
        return "form", "", dotted

    def place(self, path: str, message: str) -> None:
        """Put ``message`` where ``path`` (an Engine path) belongs."""
        where, target, sub = self.locate(path)
        if where == "field":
            self.add_error(target, f"{sub}: {message}" if sub else message)
        elif where == "group":
            self.group_errors.setdefault(target, []).append(message)
        elif where == "other":
            self.add_error(OTHER, f"{sub}: {message}")
        else:
            self.add_error(None, f"{sub}: {message}" if sub else message)

    def warn(self, path: str, message: str) -> None:
        where, target, sub = self.locate(path)
        if where == "field":
            self.field_warnings.setdefault(target, []).append(
                f"{sub}: {message}" if sub else message
            )
        elif where == "other":
            self.field_warnings.setdefault(OTHER, []).append(f"{sub}: {message}")
        else:
            self.top_warnings.append(f"{sub}: {message}" if sub else message)

    def module_group(self, module: str) -> Group | None:
        """The first fieldset (schema order) whose path's last segment is ``module``."""
        for group in groups(self.tree):
            if group.name == module:
                return group
        return None

    def place_missing(self, item: MissingDependency) -> None:
        message = missing_text(item)
        group = self.module_group(item.module)
        if group is not None:
            self.group_errors.setdefault(group.dotted, []).append(message)
            return
        leaf = self.leaf_by_path.get(item.module)
        if leaf is not None:
            self.add_error(self.field_name(leaf), message)
            return
        self.add_error(None, message)

    def place_validation(self, result: ProfileValidation) -> None:
        """A validate result: dependencies atop their module's fieldset, undelivered settings
        and warnings at their ``setting``."""
        for missing in result.missing_dependencies:
            self.place_missing(missing)
        for undelivered in result.undelivered:
            self.place(undelivered.setting, undelivered_text(undelivered.module, undelivered.level))
        for warning in result.warnings:
            self.warn(warning.setting, warning_text(warning))

    def place_invalid_profile(self, exc: EngineError) -> PlacedStories:
        """A 422 ``invalid_profile``: ``details.undelivered`` and ``missing_dependencies``
        placed; ``details.stories``/``profiles`` (and other keys) returned for the panel."""
        details = exc.details
        result = ProfileValidation.from_json(
            {
                "valid": False,
                "missing_dependencies": details.get("missing_dependencies") or [],
                "undelivered": details.get("undelivered") or [],
            }
        )
        if result is not None:
            self.place_validation(result)
        placed = PlacedStories()
        stories = details.get("stories")
        profiles = details.get("profiles")
        placed.stories = (
            [s for s in stories if isinstance(s, str)] if isinstance(stories, list) else []
        )
        placed.profiles = (
            [p for p in profiles if isinstance(p, str)] if isinstance(profiles, list) else []
        )
        count = details.get("count")
        placed.count = count if isinstance(count, int) and not isinstance(count, bool) else None
        known = {"stories", "profiles", "count", "missing_dependencies", "undelivered"}
        placed.extra = [
            (key, shown_value(value)) for key, value in details.items() if key not in known
        ]
        nothing_placed = result is None or not (result.missing_dependencies or result.undelivered)
        if nothing_placed and not placed.stories and not placed.profiles:
            self.add_error(None, exc.message)
        return placed

    def place_validation_error(self, exc: EngineError) -> int:
        """A 422 ``validation_error``: each ``details.errors[]``/``details.field`` placed."""
        from console.engine_form_errors import engine_field_errors

        found = engine_field_errors(exc)
        for failure in found:
            self.place(failure.path, failure.message or exc.message)
        return len(found)

    # --- rendering --------------------------------------------------------------------------

    def _is_set(self, name: str, leaf: Leaf) -> bool:
        bound = self[name]
        value = bound.value()
        if leaf.nullable and self[self.null_name(leaf)].value():
            return True
        return bool(value) and value != [""]

    def shown(self) -> list[ShownLeaf | ShownGroup]:
        """The tree with its bound fields, errors and warnings, for the template."""

        def node(item: Node) -> ShownLeaf | ShownGroup:
            if isinstance(item, Leaf):
                name = self.field_name(item)
                null = self[self.null_name(item)] if item.nullable else None
                return ShownLeaf(
                    item,
                    self[name],
                    null,
                    self.default_text(item),
                    str(self.fields[name].help_text),
                    self.field_warnings.get(name, []),
                    field_anchor(item.dotted),
                    self._is_set(name, item),
                )
            children = [node(child) for child in item.children]
            errors = self.group_errors.get(item.dotted, [])
            opened = bool(errors) or any(
                (c.open or bool(c.errors)) if isinstance(c, ShownGroup) else c.is_set or c.has_error
                for c in children
            )
            return ShownGroup(item, children, errors, field_anchor(item.dotted), opened)

        return [node(item) for item in self.tree]

    @property
    def other_field(self) -> forms.BoundField | None:
        return self[OTHER] if self.has_other else None

    @property
    def other_warnings(self) -> list[str]:
        return self.field_warnings.get(OTHER, [])

    @property
    def problem_count(self) -> int:
        """Errors on the form (fields, fieldsets and the form itself)."""
        return sum(len(e) for e in self.errors.values()) + sum(
            len(e) for e in self.group_errors.values()
        )

    @property
    def warning_count(self) -> int:
        return sum(len(w) for w in self.field_warnings.values()) + len(self.top_warnings)


def _paths(document: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    """Every dotted path in ``document`` (objects recursed)."""
    for key, value in document.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        yield dotted
        if isinstance(value, Mapping):
            yield from _paths(value, dotted)


def _removed(old: Mapping[str, Any], new: Mapping[str, Any], prefix: str = "") -> list[str]:
    found: list[str] = []
    for key, value in old.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if key not in new:
            found.append(dotted)
        elif isinstance(value, Mapping) and isinstance(new[key], Mapping):
            found.extend(_removed(value, new[key], dotted))
    return found


def _changed_leaves(old: Mapping[str, Any], new: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in new.items():
        before = old.get(key, ABSENT)
        if isinstance(before, Mapping) and isinstance(value, Mapping):
            nested = _changed_leaves(before, value)
            if nested:
                out[key] = nested
        elif before is ABSENT or not _same(before, value):
            out[key] = value
    return out


# --- texts ----------------------------------------------------------------------------------------


def _levels(levels: Sequence[str]) -> str:
    if not levels:
        return "any level"
    return " or ".join(f"`{level}`" for level in levels)


def missing_text(item: MissingDependency) -> str:
    """ "`<module>` at `<level>` needs `<req>` at <levels>" (several joined with "and")."""
    needs = " and ".join(f"`{r.module}` at {_levels(r.levels)}" for r in item.requires)
    return f"`{item.module}` at `{item.level}` needs {needs or 'something not delivered'}"


def undelivered_text(module: str, level: str | None) -> str:
    if level:
        return f"Not delivered on this deployment: `{module}` at `{level}`"
    return f"Not delivered on this deployment: `{module}`"


def warning_text(warning: ProfileValidationWarning) -> str:
    """ "Below safety minimum: `model_calls` N; a turn needs at least M (reservation: k=v, …)"."""
    if warning.code != "below_safety_minimum":
        return f"{warning.code}: {warning.setting}"
    reservation = ", ".join(f"{k}={v}" for k, v in warning.reservation.items())
    return (
        f"Below safety minimum: `model_calls` {warning.model_calls}; a turn needs at least "
        f"{warning.minimum_required} (reservation: {reservation or '—'})"
    )


def validation_banner(result: ProfileValidation, problems: int) -> str:
    """ "Valid", "Valid, with N warnings" or "Not valid — N problems"."""
    if not result.valid:
        count = problems or len(result.missing_dependencies) + len(result.undelivered)
        return f"Not valid — {count} problem{'s' if count != 1 else ''}"
    if result.warnings:
        n = len(result.warnings)
        return f"Valid, with {n} warning{'s' if n != 1 else ''}"
    return "Valid"
