"""The guarantee table's wording (#57, FR-VERIFY-15, FR-PROF-12), for
``templates/console/_guarantees.html``.

The table is read whole from the Engine (``GET /engine-profiles/{id}/guarantees`` on the profile
page, ``GET /stories/{id}/guarantees`` on a story's Config page) and shown in its order. Text
here carries backtick spans that the partial renders as ``<code>`` (``code_spans``). Nothing here
claims an output was checked in full (FR-RESP-1): only the claims detected in an output are
checked.

A row may also need a story setting (``story_setting``, #169: audience filtering needs
``player_view = character``). A profile's table can't know a story's value, so its Needs says the
table assumes the default; a story's Config page passes the story's ``settings`` (from the read
it already makes) as ``story_settings``, and Needs and Status name the story's value.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from django.http import HttpRequest

from console.engine.client import EngineClientError, EngineError
from console.engine.models import GuaranteeStatus, GuaranteeTable, RequiredSetting
from console.engine_errors import log_engine_error, panel_for

DOCS_URL = "https://github.com/nathanpond/Epistrel/blob/main/docs/guarantees.md"
DOCS_LABEL = "Guarantee table (Engine docs, main)"
NO_TABLE = "No guarantee table for that version"
CONFIG_SCOPE = "this story's effective configuration, overrides included"
STORY_SETTING_PREFIX = "story setting "
PROFILE_STORY_SETTING_NOTE = "each story's own setting; this table assumes the default"
STORY_VALUE_NOTE = "this story: `{}`"
STORY_SETTING_REASON = "this story's `{}` is `{}`"
ANY_VALUE = " (any value)"

Mode = Literal["profile", "story"]

KIND_LABELS = {"deterministic": "By rules", "model_dependent": "By a model"}

CAVEATS = (
    "The model proposes; the engine validates structured claims, effects, and permissions.",
    "A model-dependent step is only as good as its metric.",
    "Only the claims detected in an output are checked.",
)
# Shown only when the table has the row with ``provided`` exactly ``False``, in this order.
ROW_CAVEATS = (
    (
        "structural_knowledge_separation",
        "Structural knowledge separation isn't provided: characters' knowledge is separated "
        "only by instructions in a shared prompt, so secrets seeded from the catalog or "
        "scenario are present in the shared context.",
    ),
    (
        "audience_filtering",
        "Audience filtering isn't provided: a character viewpoint still never receives any "
        "character's `think` or `latent` content, and every other beat is returned in full.",
    ),
)


def _value(value: Any) -> str:
    """A setting value: a string as itself, anything else as compact JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _either(items: Sequence[str]) -> str:
    """``a``, ``a or b``, ``a, b or c``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} or {items[-1]}"


def setting_text(setting: RequiredSetting) -> str:
    """ "`path` = `v1` or `v2`", or "`path` (any value)" when ``values`` is empty."""
    if not setting.values:
        return f"`{setting.path}` (any value)"
    return f"`{setting.path}` = {_either([f'`{_value(v)}`' for v in setting.values])}"


def story_setting_text(setting: RequiredSetting) -> str:
    """ "story setting `path` = `v`": :func:`setting_text` with the story-setting prefix."""
    return STORY_SETTING_PREFIX + setting_text(setting)


def _story_value(setting: RequiredSetting, story_settings: Mapping[str, Any] | None) -> str | None:
    """The story's value for ``setting`` as :func:`_value` text (the flat key ``path``, as the
    Engine's ``met_by`` reads it), or ``None`` when unknown: no settings read, or no such key."""
    if story_settings is None or setting.path not in story_settings:
        return None
    return _value(story_settings[setting.path])


def _with_note(clause: str, note: str) -> str:
    """``clause`` with `` (note)``, merged into a trailing "(any value)" as "(any value; note)"."""
    if not note:
        return clause
    if clause.endswith(ANY_VALUE):
        return f"{clause[: -len(ANY_VALUE)]} (any value; {note})"
    return f"{clause} ({note})"


def needs_text(
    row: GuaranteeStatus,
    *,
    mode: Mode | None = None,
    story_settings: Mapping[str, Any] | None = None,
) -> str:
    """What the step needs: each module ("`m`", "`m` at `l` or above", "`m` at `l`" when
    exact), then the setting, then the story setting, joined with "; ". Nothing needed reads
    "none (every profile)", or "none (outside stories)" for a step that runs outside stories.

    The story-setting clause ends, on the profile page (``mode="profile"``), with the note that
    the table assumes the default; on a story's Config page (``"story"``), with the story's
    value from ``story_settings`` when known; with no ``mode``, with nothing."""
    parts: list[str] = []
    for required in row.requires:
        if required.level is None:
            parts.append(f"`{required.module}`")
        elif required.exact:
            parts.append(f"`{required.module}` at `{required.level}`")
        else:
            parts.append(f"`{required.module}` at `{required.level}` or above")
    if row.setting is not None:
        parts.append(setting_text(row.setting))
    if row.story_setting is not None:
        note = ""
        if mode == "profile":
            note = PROFILE_STORY_SETTING_NOTE
        elif mode == "story":
            value = _story_value(row.story_setting, story_settings)
            note = "" if value is None else STORY_VALUE_NOTE.format(value)
        parts.append(_with_note(story_setting_text(row.story_setting), note))
    if parts:
        return "; ".join(parts)
    return "none (outside stories)" if row.provided is None else "none (every profile)"


def status_text(row: GuaranteeStatus, *, story_settings: Mapping[str, Any] | None = None) -> str:
    """``provided`` decides first: "Outside stories" (null), "Provided" (true), else "Not
    provided", naming the milestone when ``delivered_in`` is set, else the story setting when
    the story's known value (``story_settings``) is outside the row's non-empty ``values``.

    The comparison is by :func:`_value` text, not by Python equality as the Engine's ``met_by``
    is; they differ only for values like ``1``/``true``, which ``player_view`` (a string enum)
    never has. When modules are unmet too, the reason still names the story setting (accurate,
    not exhaustive: Needs lists every need)."""
    if row.provided is None:
        return "Outside stories"
    if row.provided:
        return "Provided"
    if row.delivered_in is not None:
        return f"Not provided (delivered in {row.delivered_in})"
    setting = row.story_setting
    if setting is not None and setting.values:
        value = _story_value(setting, story_settings)
        if value is not None and value not in {_value(v) for v in setting.values}:
            return f"Not provided ({STORY_SETTING_REASON.format(setting.path, value)})"
    return "Not provided"


def caveats(table: GuaranteeTable) -> list[str]:
    """The caveat sentences: the fixed three, then one per :data:`ROW_CAVEATS` row present with
    ``provided`` exactly ``False`` (the first row of an id decides)."""
    provided: dict[str, bool | None] = {}
    for row in table.rows:
        provided.setdefault(row.id, row.provided)
    return [*CAVEATS, *(text for key, text in ROW_CAVEATS if provided.get(key) is False)]


@dataclass(frozen=True)
class GuaranteeRow:
    """One table row as the partial shows it. ``anchor`` is the row id, ``None`` on a duplicate
    after the first."""

    row: GuaranteeStatus
    anchor: str | None
    kind_label: str
    needs: str
    status: str
    muted: bool


def rows(
    table: GuaranteeTable, *, mode: Mode, story_settings: Mapping[str, Any] | None = None
) -> list[GuaranteeRow]:
    """The rows in the table's order. ``story_settings`` counts only in story mode, so a
    profile page never shows a story's value or reason."""
    if mode != "story":
        story_settings = None
    seen: set[str] = set()
    shown: list[GuaranteeRow] = []
    for row in table.rows:
        anchor = None if row.id in seen else row.id
        seen.add(row.id)
        shown.append(
            GuaranteeRow(
                row=row,
                anchor=anchor,
                kind_label=KIND_LABELS[row.kind],
                needs=needs_text(row, mode=mode, story_settings=story_settings),
                status=status_text(row, story_settings=story_settings),
                muted=row.provided is not True,
            )
        )
    return shown


def race_note(table: GuaranteeTable, shown_version: int) -> str:
    """The profile page's version race (no ``?version=``): the table answered another version
    than the page shows."""
    if table.version == shown_version:
        return ""
    return (
        f"(the table is for version {table.version}; the profile changed while this page "
        "loaded — reload)"
    )


def section(
    request: HttpRequest,
    result: GuaranteeTable | EngineClientError,
    mode: Literal["profile", "story"],
    *,
    profile_name: str = "",
    note: str = "",
    story_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The Guarantees section's context for ``_guarantees.html``: the table and its rows and
    caveats, or the failure kept inside the section (logged, no page banner). On the profile
    route a 404 is "No guarantee table for that version"; any other failure, on either route,
    is #43's panel. ``note`` follows the caption (the Config scope, the version race).
    ``story_settings`` (the Config page's story read, ``None`` when it failed) only builds the
    rows (#169)."""
    context: dict[str, Any] = {
        "guarantees": None,
        "guarantees_error": None,
        "guarantees_missing": False,
        "guarantees_mode": mode,
        "profile_name": profile_name,
        "guarantees_note": note,
        "guarantees_docs_url": DOCS_URL,
        "guarantees_docs_label": DOCS_LABEL,
        "guarantees_missing_text": NO_TABLE,
    }
    if isinstance(result, EngineClientError):
        log_engine_error(request, result)
        if mode == "profile" and isinstance(result, EngineError) and result.http_status == 404:
            context["guarantees_missing"] = True
        else:
            context["guarantees_error"] = panel_for(result)
        return context
    context["guarantees"] = result
    context["guarantee_rows"] = rows(result, mode=mode, story_settings=story_settings)
    context["guarantee_caveats"] = caveats(result) if result.rows else []
    return context
