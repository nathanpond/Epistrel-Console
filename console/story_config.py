"""A story's effective configuration laid out for its Config page and its header (#85).

``GET /stories/{id}/effective-config`` answers four settings documents: the deployment's
defaults, the story's overrides and the effective result (the profile version's settings come
from one extra ``GET /engine-profiles/{id}?version=N``). :func:`config_layers` lays them out as
one :class:`ConfigRow` per leaf of ``effective``, in the profile schema's order (#84's
:func:`~console.forms.profile_schema_form.build_tree`: a subtree the schema form edits as JSON is
one row), grouped by top-level module, with #78's tag badges and the schema default. Leaves the
schema does not describe go to "Other settings", one row per flattened leaf. Without a schema,
every leaf of ``effective`` is a row, in ``effective``'s order, without badges or defaults.

A row's **source** is the topmost layer holding the key (the override, else the profile version,
else the deployment default), whatever the values: equal values don't change the rule. When the
profile could not be read, a key the story does not override is "profile or default".

:class:`ConfigBadge` is the header's block (#48's story header): the short fingerprint (the
first 12 hex digits after ``sha256:``, the full value in ``title``) and the quality status,
linked to the Config page. Pages build it from an effective-config read (:func:`badge_for`) or
from a turn's report (:func:`badge_from_report`) for the out-of-band refresh after a turn.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from django.urls import reverse

from console.engine.client import EngineClient, EngineClientError
from console.engine.models import EffectiveConfig, ProfileSchema, TurnReport
from console.forms.profile_schema_form import Leaf, Node, build_tree, get_path
from console.impact import quality_class, short_fingerprint
from console.profile_schema import ABSENT, Badge, Cell, _cell
from console.reports import field_anchor

Source = Literal["override", "profile", "default", "profile or default", ""]

OVERRIDE: Source = "override"
PROFILE: Source = "profile"
DEFAULT: Source = "default"
EITHER: Source = "profile or default"
UNAVAILABLE = "config unavailable"
NO_VALUE = Cell("absent")


# --- the three layers -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigRow:
    """One setting: its value in each layer (``—`` where a layer leaves it out), the effective
    value, the layer it comes from, the schema default and the tags."""

    path: str
    default: Cell
    profile: Cell
    override: Cell
    effective: Cell
    source: Source
    schema_default: Cell = NO_VALUE
    title: str = ""
    description: str = ""
    badges: tuple[Badge, ...] = ()
    untagged: bool = False

    @property
    def anchor(self) -> str:
        return field_anchor(self.path)


@dataclass(frozen=True)
class ConfigBlock:
    """One top-level module (or ``pipeline``) and its rows."""

    name: str
    rows: tuple[ConfigRow, ...]


@dataclass(frozen=True)
class ConfigLayers:
    blocks: tuple[ConfigBlock, ...] = ()
    other: tuple[ConfigRow, ...] = ()
    schema_ok: bool = True
    profile_ok: bool = True


def source_of(path: Sequence[str], overrides: Any, profile: Mapping[str, Any] | None) -> Source:
    """The topmost layer holding ``path``: ``override``, ``profile``, else ``default``; a key the
    story does not override is ``profile or default`` when the profile is unknown."""
    if get_path(overrides, path) is not ABSENT:
        return OVERRIDE
    if profile is None:
        return EITHER
    if get_path(profile, path) is not ABSENT:
        return PROFILE
    return DEFAULT


def _flatten(value: Any, path: tuple[str, ...]) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every leaf under ``value`` (objects recursed; an empty object is a leaf)."""
    if isinstance(value, dict) and value:
        for key, item in value.items():
            yield from _flatten(item, (*path, str(key)))
    else:
        yield path, value


class _Layout:
    def __init__(self, config: EffectiveConfig, profile: Mapping[str, Any] | None) -> None:
        self.config = config
        self.profile = profile

    def row(self, path: tuple[str, ...], leaf: Leaf | None = None) -> ConfigRow:
        raw = leaf is not None and leaf.kind == "json"
        overrides = self.config.overrides.settings
        profile_cell = (
            _cell(get_path(self.profile, path), missing="absent", raw=raw)
            if self.profile is not None
            else Cell("plain", "unavailable")
        )
        return ConfigRow(
            path=".".join(path),
            default=_cell(
                get_path(self.config.deployment_defaults, path), missing="absent", raw=raw
            ),
            profile=profile_cell,
            override=_cell(get_path(overrides, path), missing="absent", raw=raw),
            effective=_cell(get_path(self.config.effective, path), missing="absent", raw=raw),
            source=source_of(path, overrides, self.profile),
            schema_default=(
                _cell(leaf.default, missing="absent", raw=raw) if leaf is not None else NO_VALUE
            ),
            title=leaf.title if leaf is not None else "",
            description=leaf.description if leaf is not None else "",
            badges=leaf.badges if leaf is not None else (),
            untagged=leaf.untagged if leaf is not None else False,
        )

    def described(self, nodes: Sequence[Node]) -> tuple[list[ConfigRow], list[ConfigRow]]:
        """Rows for every schema leaf ``effective`` holds, and for what it holds beyond them."""
        rows: list[ConfigRow] = []
        other: list[ConfigRow] = []

        def walk(value: Any, children: Sequence[Node], path: tuple[str, ...]) -> None:
            by_key = {node.path[-1]: node for node in children}
            for node in children:
                key = node.path[-1]
                if not isinstance(value, dict) or key not in value:
                    continue
                if isinstance(node, Leaf):
                    rows.append(self.row(node.path, node))
                elif isinstance(value[key], dict):
                    walk(value[key], node.children, node.path)
                else:
                    other.append(self.row(node.path))
            if isinstance(value, dict):
                for key, item in value.items():
                    if key not in by_key:
                        other.extend(self.row(p) for p, _ in _flatten(item, (*path, key)))

        walk(self.config.effective, nodes, ())
        return rows, other


def config_layers(
    config: EffectiveConfig,
    profile: Mapping[str, Any] | None,
    schema: ProfileSchema | None,
) -> ConfigLayers:
    """``config`` laid out beside ``profile`` (the running version's settings; ``None`` when
    the read failed) by ``schema`` (``None`` when unread)."""
    layout = _Layout(config, profile)
    if schema is None:
        blocks = [
            ConfigBlock(key, tuple(layout.row(p) for p, _ in _flatten(value, (key,))))
            for key, value in config.effective.items()
        ]
        return ConfigLayers(tuple(blocks), (), schema_ok=False, profile_ok=profile is not None)
    tree = build_tree(schema)
    rows, other = layout.described(tree)
    order = [node.path[0] for node in tree]
    grouped: dict[str, list[ConfigRow]] = {}
    for row in rows:
        grouped.setdefault(row.path.split(".", 1)[0], []).append(row)
    blocks = [ConfigBlock(name, tuple(grouped[name])) for name in order if name in grouped]
    return ConfigLayers(tuple(blocks), tuple(other), profile_ok=profile is not None)


# --- the header block -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigBadge:
    """The story header's configuration block: fingerprint and quality status, linked to the
    Config page; ``unavailable`` when the effective configuration could not be read."""

    story_id: str
    fingerprint: str = ""
    quality: str = ""
    profile_id: str = ""
    profile_version: int | None = None
    unavailable: bool = False

    @property
    def short(self) -> str:
        return short_fingerprint(self.fingerprint)

    @property
    def url(self) -> str:
        return config_url(self.story_id)

    @property
    def quality_class(self) -> str:
        return quality_class(self.quality)


def config_url(story_id: str) -> str:
    return reverse("stories:config", args=[story_id])


def badge_for(config: EffectiveConfig) -> ConfigBadge:
    return ConfigBadge(
        config.story_id,
        config.fingerprint,
        config.quality_status.status,
        config.profile.id,
        config.profile.version,
    )


def read_badge(client: EngineClient, story_id: str) -> ConfigBadge:
    """The header block from an effective-config read (3 s); a failed read degrades to
    "config unavailable"."""
    try:
        return badge_for(client.get_effective_config(story_id))
    except EngineClientError:
        return ConfigBadge(story_id, unavailable=True)


def badge_from_report(story_id: str, report: TurnReport | None) -> ConfigBadge | None:
    """The header block from a turn's report (respond or regenerate); ``None`` when it carries
    no fingerprint (a replay from before the Engine added it), so the caller re-reads."""
    if report is None or not report.fingerprint:
        return None
    return ConfigBadge(
        story_id,
        report.fingerprint,
        report.quality_status.status if report.quality_status else "",
        report.profile.id if report.profile else "",
        report.profile.version if report.profile else None,
    )
