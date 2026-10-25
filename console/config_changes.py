"""Configuration-change lists (#50 the deployment's, #54 a story's): grouping by config revision,
value text, actor links and the ``setting`` filter the recorded schema allows.

Both pages render ``templates/console/_config_changes.html`` with ``change_groups`` from
:func:`revision_groups`. The rows of one config revision sit under one header row, so one
revision's whole change reads as a diff (owner, round two). Each row shows ``old → new``.

The actor is shown verbatim. ``console:<username>`` links to this Console's operation log for
that operator (#44's ``?operator=`` filter) when such a user exists here; anything else is text.
"""

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.contrib.auth import get_user_model

from console.engine.models import ConfigChange

CONSOLE_ACTOR = "console:"
# #43's rendering: JSON in tables is cut at 120 characters, the full value in ``<details>``.
VALUE_MAX = 120


@dataclass(frozen=True)
class ValueText:
    """One old or new value: compact JSON, cut to ``VALUE_MAX`` with the full value kept."""

    short: str
    full: str
    truncated: bool


@dataclass(frozen=True)
class ChangeRow:
    change: ConfigChange
    old: ValueText
    new: ValueText
    actor_url: str  # "" when the actor is not a known Console operator


@dataclass(frozen=True)
class RevisionGroup:
    config_revision: int
    rows: list[ChangeRow]

    @property
    def at(self) -> str:
        return self.rows[0].change.at

    @property
    def actors(self) -> list[str]:
        """The distinct actors of the revision, in row order."""
        return list(dict.fromkeys(row.change.actor for row in self.rows))


def value_text(value: Any) -> ValueText:
    """``value`` as compact JSON (``null`` included), cut at ``VALUE_MAX`` characters."""
    compact = json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
    if len(compact) <= VALUE_MAX:
        return ValueText(compact, compact, False)
    full = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return ValueText(compact[: VALUE_MAX - 1] + "…", full, True)


def operator_name(actor: str) -> str | None:
    """The Console username in a ``console:<username>`` actor, else ``None``."""
    if actor.startswith(CONSOLE_ACTOR) and len(actor) > len(CONSOLE_ACTOR):
        return actor[len(CONSOLE_ACTOR) :]
    return None


def operator_log_url(username: str) -> str:
    """This Console's operation log, filtered to one operator."""
    return f"/operations/?{urlencode({'operator': username})}"


def _known_operators(changes: Iterable[ConfigChange]) -> set[str]:
    names = {name for c in changes if (name := operator_name(c.actor)) is not None}
    if not names:
        return set()
    users = get_user_model().objects.filter(username__in=names)
    return set(users.values_list("username", flat=True))


def revision_groups(changes: Sequence[ConfigChange]) -> list[RevisionGroup]:
    """``changes`` (one page, Engine order) grouped by consecutive ``config_revision``."""
    known = _known_operators(changes)
    groups: list[RevisionGroup] = []
    for change in changes:
        name = operator_name(change.actor)
        row = ChangeRow(
            change=change,
            old=value_text(change.old),
            new=value_text(change.new),
            actor_url=operator_log_url(name) if name is not None and name in known else "",
        )
        if groups and groups[-1].config_revision == change.config_revision:
            groups[-1].rows.append(row)
        else:
            groups.append(RevisionGroup(change.config_revision, [row]))
    return groups


# --- the ``setting`` filter -----------------------------------------------------------------------


@dataclass(frozen=True)
class SettingFilter:
    """What a config-change list's ``setting`` query accepts in the recorded schema: named
    settings (current keys, historical ones and every ``profile_defaults.<path>``) and a
    pattern for prefixed keys (``policy:<id>`` …)."""

    names: tuple[str, ...]
    prefixed: re.Pattern[str] | None

    def accepts_prefixed(self, value: str) -> bool:
        return self.prefixed is not None and self.prefixed.fullmatch(value) is not None


def setting_filter(doc: dict[str, Any], path: str) -> SettingFilter:
    """The ``setting`` parameter of ``GET <path>`` in ``doc`` (the recorded OpenAPI): its enum
    and its prefixed-key pattern. Empty when the document does not describe it."""
    names: list[str] = []
    pattern: re.Pattern[str] | None = None
    operation = doc.get("paths", {}).get(path, {}).get("get", {})
    for parameter in operation.get("parameters", []):
        if parameter.get("name") != "setting":
            continue
        for option in _options(parameter.get("schema", {})):
            if option.get("type") == "array":
                for item in _options(option.get("items", {})):
                    names.extend(v for v in item.get("enum", []) if isinstance(v, str))
                    if isinstance(item.get("pattern"), str):
                        pattern = re.compile(item["pattern"])
    return SettingFilter(tuple(names), pattern)


def _options(schema: dict[str, Any]) -> list[dict[str, Any]]:
    options = schema.get("anyOf")
    return [o for o in options if isinstance(o, dict)] if isinstance(options, list) else [schema]
