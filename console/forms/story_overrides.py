"""A story's overrides of its profile's dials as a form (#85), on #84's schema form.

:class:`OverridesForm` is :class:`~console.forms.profile_schema_form.SchemaSettingsForm` rooted
at ``overrides`` (field names ``overrides__memory__rolling_summary__window_messages``), with
the story's overrides pre-filled:

- An unset input means no change. A field without an override shows what it inherits as its
  placeholder, "inherits: 30 (profile)" (or "(default)", from the deployment's defaults, else
  the schema's).
- "remove override" (``remove__<field>``, offered where the story overrides the setting) is the
  only removal and sends ``null``; emptying a pre-filled input is the error "Tick 'remove
  override' to remove it".
- Booleans without an override are tri-state ("— no override —" / true / false); with one,
  only true / false, pre-selected.
- A nullable setting can't be overridden to ``null`` (a merge patch's ``null`` removes): no
  "null" box, and the help says "To set null, edit the profile".
- A JSON subtree is diffed per RFC 7396 against the current override subtree (a removed key is
  sent as ``null``); ``{}`` is no change. So is the "Settings not in this deployment's schema"
  textarea, pre-filled with the overrides the schema does not describe.

:meth:`OverridesForm.patch` is the merge patch for ``impact-preview``'s ``overrides`` and
``PATCH …/profile-overrides``'s ``settings``; ``{}`` means nothing changed (no Engine call).
Engine paths ``body.settings.<path>``, ``body.overrides.<path>`` and ``settings.<path>`` are
placed at their field.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

from django import forms

from console.forms.profile_schema_form import (
    OTHER,
    SEP,
    Leaf,
    Node,
    SchemaSettingsForm,
    ShownGroup,
    ShownLeaf,
    deep_merge,
    get_path,
    leaves,
    set_path,
    shown_value,
)
from console.merge_patch import _same, diff
from console.profile_schema import ABSENT

ROOT = "overrides"
REMOVE_PREFIX = "remove__"
NO_OVERRIDE = "— no override —"
TICK_REMOVE = "Tick 'remove override' to remove it"
SET_NULL_HELP = "To set null, edit the profile"
SETTINGS_PREFIX = "settings."


@dataclass
class ShownOverride(ShownLeaf):
    """A field as the overrides template renders it, with its "remove override" box."""

    remove_field: forms.BoundField | None = None


class OverridesForm(SchemaSettingsForm):
    """The story's overrides (see the module docstring). ``overrides`` is the story's current
    overrides; ``profile`` the running profile version's settings (``None`` when unread);
    ``defaults`` the deployment's defaults from the effective-config read."""

    def __init__(
        self,
        data: Any = None,
        *,
        schema: Any,
        overrides: Mapping[str, Any],
        profile: Mapping[str, Any] | None,
        defaults: Mapping[str, Any] | None,
        **kwargs: Any,
    ) -> None:
        self.profile_settings = profile
        self._removed: set[str] = set()
        super().__init__(
            data,
            schema=schema,
            root=ROOT,
            stored=overrides,
            defaults=defaults,
            mode="profile",
            other=True,
            **kwargs,
        )
        self.tree = _without_null(self.tree)

    # --- building ---------------------------------------------------------------------------

    def remove_name(self, leaf: Leaf) -> str:
        return f"{REMOVE_PREFIX}{self.field_name(leaf)}"

    def overridden(self, leaf: Leaf) -> bool:
        return get_path(self.stored, leaf.path) is not ABSENT

    def default_of(self, leaf: Leaf) -> tuple[Any, str]:
        """What the setting inherits: the profile version's value, else the deployment's
        default, else the schema's."""
        if self.profile_settings is not None:
            value = get_path(self.profile_settings, leaf.path)
            if value is not ABSENT:
                return value, "profile"
        if self.defaults is not None:
            value = get_path(self.defaults, leaf.path)
            if value is not ABSENT:
                return value, "default"
        if leaf.default is not ABSENT:
            return leaf.default, "schema default"
        return ABSENT, ""

    def default_text(self, leaf: Leaf) -> str:
        value, label = self.default_of(leaf)
        return f"inherits: {shown_value(value)} ({label})" if value is not ABSENT else ""

    def _add_leaf(self, leaf: Leaf) -> None:
        nullable = leaf.nullable
        super()._add_leaf(replace(leaf, nullable=False))  # merge patch's null removes
        name = self.field_name(leaf)
        built = self.fields[name]
        overridden = self.overridden(leaf)
        if leaf.kind == "boolean" and isinstance(built, forms.ChoiceField):
            values = [("true", "true"), ("false", "false")]
            built.choices = values if overridden else [("", NO_OVERRIDE), *values]
        if nullable:
            built.help_text = " · ".join(p for p in (str(built.help_text), SET_NULL_HELP) if p)
        if overridden:
            self.fields[self.remove_name(leaf)] = forms.BooleanField(
                required=False, label="remove override"
            )

    # --- cleaning ---------------------------------------------------------------------------

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        self._removed = set()
        for name, leaf in self.leaf_by_name.items():
            if not self.overridden(leaf):
                continue
            if self.cleaned_data.get(self.remove_name(leaf)):
                self._removed.add(name)
                self.errors.pop(name, None)  # the value no longer matters
                continue
            if name not in self.errors and self.value_of(leaf) is ABSENT:
                self.add_error(name, TICK_REMOVE)
        return cleaned

    def _strip(self, path: str) -> str:
        path = super()._strip(path)
        return path.removeprefix(SETTINGS_PREFIX)

    # --- output -----------------------------------------------------------------------------

    def patch(self) -> dict[str, Any]:
        """The RFC 7396 merge patch over the story's overrides (call after ``is_valid()``)."""
        out: dict[str, Any] = {}
        for leaf in leaves(self.tree):
            name = self.field_name(leaf)
            if name in self._removed:
                set_path(out, leaf.path, None)
                continue
            value = self.value_of(leaf)
            if value is ABSENT:
                continue
            stored = get_path(self.stored, leaf.path)
            if stored is not ABSENT and _same(stored, value):
                continue
            if leaf.kind == "json" and isinstance(value, dict):
                if not value:
                    continue  # ``{}`` is no change
                if isinstance(stored, dict):
                    changed = diff(stored, value)
                    if changed:
                        set_path(out, leaf.path, changed)
                    continue
            set_path(out, leaf.path, value)
        other = self._values.get(OTHER)
        if isinstance(other, dict):
            changed = diff(self.undescribed(self.stored), other)
            if changed:
                out = deep_merge(out, changed)
        return out

    # --- rendering --------------------------------------------------------------------------

    def shown(self) -> list[ShownLeaf | ShownGroup]:
        def convert(node: ShownLeaf | ShownGroup) -> ShownLeaf | ShownGroup:
            if isinstance(node, ShownGroup):
                node.children = [convert(c) for c in node.children]
                return node
            name = self.remove_name(node.leaf)
            values = {f.name: getattr(node, f.name) for f in fields(ShownLeaf)}
            remove = self[name] if name in self.fields else None
            return ShownOverride(**values, remove_field=remove)

        return [convert(node) for node in super().shown()]


def _without_null(nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    """The tree with no nullable leaf (the form offers no "null" box)."""
    return tuple(
        replace(node, nullable=False)
        if isinstance(node, Leaf)
        else replace(node, children=_without_null(node.children))
        for node in nodes
    )


def field_of(path: str) -> str:
    """The form field name of a dotted overrides path (tests and anchors)."""
    return SEP.join((ROOT, *path.split(".")))
