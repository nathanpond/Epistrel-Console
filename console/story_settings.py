"""A story's settings as the settings page shows them (#54): which fields the Console edits, what
each one's default is, and whether the story overrides it.

The page edits thirteen settable settings: #54's seven and Engine M5's six (#197: player
authority, player view and the multi-character settings). The Engine's settings read fills
defaults in and carries no override marker, so the marker is worked out here (planner, replan
2026-10-24):

- a nullable setting (:data:`NULLABLE`) is "default" while ``null``: "default (N)" with N the
  deployment's own setting from ``GET /admin/settings`` for :data:`DEPLOYMENT_DEFAULTS`
  ("unknown" when that read failed); "default (sequential)" for interaction mode, or
  "default (scenario: <value>)" when genesis kept the scenario's mode inactive
  (:func:`scenario_mode`); "default (request order)" for sequence order and tie-break; plain
  "default" for the viewpoint. Any value is "story" (a stored ``state:<name>`` tie-break too);
- the others compare against :data:`CONSTANT_DEFAULTS` (Engine ``core/settings_schema.py``): a
  value equal to the default reads "default" (an override with the same value cannot be told
  apart), anything else "story".

C7 (#197) is the M5 choice-field pattern every M5 form with an Engine-defaulted select follows:
:data:`ENGINE_DEFAULT` / :func:`engine_default_choice` first (it sends nothing), explicit choices
from :data:`SETTING_CHOICES` (an explicit choice equal to the default *is* sent), a stored string
the list lacks kept by :func:`kept_choices`, and booleans that may stay unset as a select of
:data:`BOOLEAN_SELECT` read with :func:`select_bool`, never a checkbox.

The six engine dials that moved to engine-profile overrides in Engine M3 (:data:`SUPERSEDED`)
are never edited here: the page links to the story's Config page (M3 #85) instead. Any key the
read returns beyond :data:`SETTABLE` is listed read-only under "Other settings" and never sent.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django.urls import reverse

from console.wording import humanised

# The settable ``StorySettingsPatch`` properties, in page order (``policies`` has its own form).
SETTABLE = (
    "turn_concurrency",
    "turn_queue_max",
    "default_output_viewpoint",
    "regenerate_retry_budget",
    "lite_summary_chunk",
    "audit_retention_days",
    "allow_new_arrivals",
    "player_authority",
    "player_view",
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "reactive_interjections",
)
POLICIES = "policies"
# Defaults the Engine fills in itself (Engine ``core/settings_schema.py``).
CONSTANT_DEFAULTS: dict[str, Any] = {
    "turn_concurrency": "reject",
    "regenerate_retry_budget": 2,
    "allow_new_arrivals": True,
    "player_authority": "own_character",
    "player_view": "character",
    "reactive_interjections": False,
}
# Nullable settings whose ``null`` means the deployment's setting of the same name.
DEPLOYMENT_DEFAULTS = ("turn_queue_max", "lite_summary_chunk", "audit_retention_days")
# Engine M5's nullable multi-character settings (``null``: the Engine's default, below).
MULTI_CHARACTER = ("interaction_mode", "sequence_order", "simultaneous_tie_break")
NULLABLE = (*DEPLOYMENT_DEFAULTS, "default_output_viewpoint", *MULTI_CHARACTER)
# Story settings that became engine-profile overrides (Engine M3; 422 ``superseded``).
SUPERSEDED = (
    "generation_settings",
    "lite_window_messages",
    "lite_summary_max_tokens",
    "max_model_calls_per_turn",
    "turn_deadline",
    "guardrail_retry_budget",
)
LABELS = {
    "turn_concurrency": "Turn concurrency",
    "turn_queue_max": "Queue size",
    "default_output_viewpoint": "Default output viewpoint",
    "regenerate_retry_budget": "Regenerate retry budget",
    "lite_summary_chunk": "Lite summary chunk",
    "audit_retention_days": "Audit retention (days)",
    "allow_new_arrivals": "Allow new arrivals — used by structured-pipeline stories",
    "player_authority": "Player authority",
    "player_view": "Player view (where perception is on)",
    "interaction_mode": "Interaction mode",
    "sequence_order": "Sequence order (sequential turns)",
    "simultaneous_tie_break": "Tie-break (simultaneous turns)",
    "reactive_interjections": "Reactive interjections",
}
STORY = "story"
DEFAULT = "default"
UNKNOWN = "unknown"
# The caps the pinned and instructions forms enforce when ``GET /admin/settings`` fails.
FALLBACK_MAX_PINNED = 50
FALLBACK_MAX_INSTRUCTIONS = 4000
PINNED_ITEM_MAX = 2000  # code points after NFC + strip (``PinnedItemIn``, Engine-side)
BUDGET_SOURCES = {
    "story": "story override",
    "profile": "profile",
    "deployment": "deployment",
    "formula": "9 + G formula",
}


# --- C7: choice fields (#197) --------------------------------------------------------------------

ENGINE_DEFAULT = ""
BOOLEAN_SELECT = [("false", "Off"), ("true", "On")]
# Each M5 setting's explicit choices (never an Engine-default entry); #198 reuses them.
SETTING_CHOICES: dict[str, list[tuple[str, str]]] = {
    "player_authority": [
        ("own_character", "Own character only"),
        ("co_author", "Co-author (may narrate others' observable actions)"),
    ],
    "player_view": [
        ("character", "Character (audience-filtered)"),
        ("story", "Story (every beat in full)"),
    ],
    "interaction_mode": [("sequential", "Sequential"), ("simultaneous", "Simultaneous")],
    "sequence_order": [("request", "Request order"), ("director", "Director")],
    "simultaneous_tie_break": [("request_order", "Request order"), ("director", "Director")],
    "reactive_interjections": BOOLEAN_SELECT,
}
# The Engine's ``state:<name>`` tie-break (M6; 422 ``not_yet`` until then).
STATE_TIE_BREAK = re.compile(r"^state:[a-z][a-z0-9_]{0,63}$")
INTERACTION_MODES = ("sequential", "simultaneous")
# What a null multi-character setting means, as "default (<text>)" and "Engine default (<text>)".
NULL_DEFAULT_TEXT = {
    "interaction_mode": "sequential",
    "sequence_order": "request order",
    "simultaneous_tie_break": "request order",
}


def engine_default_choice(text: str) -> tuple[str, str]:
    """C7's first choice: "Engine default (<text>)", ``text`` the default's label in lower
    case. Picking it sends nothing (``null`` on a merge-patch form over a stored value)."""
    return (ENGINE_DEFAULT, f"Engine default ({text})")


def kept_choices(
    choices: Sequence[tuple[str, str]],
    stored: object,
    label: Callable[[str], str] = humanised,
) -> list[tuple[str, str]]:
    """``choices`` plus a stored string they lack, as ``(stored, label(stored))`` (C3: no
    "(unknown)" suffix). ``None`` or a non-string adds nothing (C15)."""
    kept = list(choices)
    if isinstance(stored, str) and stored not in {value for value, _ in kept}:
        kept.append((stored, label(stored)))
    return kept


def tie_break_label(value: str) -> str:
    """A kept tie-break: "state:<name> (M6: request order applies)" for the Engine's
    ``state:<name>``, else :func:`humanised`."""
    if STATE_TIE_BREAK.match(value):
        return f"{value} (M6: request order applies)"
    return humanised(value)


def select_bool(value: str) -> bool | None:
    """A :data:`BOOLEAN_SELECT` value: ``"true"`` → ``True``, ``"false"`` → ``False``, anything
    else (``""`` included) → ``None``."""
    return {"true": True, "false": False}.get(value)


def scenario_mode(inactive: object) -> str | None:
    """The scenario's interaction mode genesis kept inactive (``settings.inactive``, a
    ``{name: {value, capability}}`` map), when it is one of the two modes; else ``None``."""
    if not isinstance(inactive, Mapping):
        return None
    entry = inactive.get("interaction_mode")
    if not isinstance(entry, Mapping):
        return None
    value = entry.get("value")
    return value if isinstance(value, str) and value in INTERACTION_MODES else None


def null_default_text(name: str, inactive: object = None) -> str:
    """What a null multi-character setting means: "sequential", "scenario: <mode>",
    "request order"."""
    if name == "interaction_mode":
        mode = scenario_mode(inactive)
        if mode is not None:
            return f"scenario: {mode}"
    return NULL_DEFAULT_TEXT[name]


def flash_name(label: str) -> str:
    """A row label as a flash names it: cut at its first " (" or " — "."""
    for cut in (" (", " — "):
        label = label.split(cut, 1)[0]
    return label


def config_url(story_id: str) -> str:
    """The story's Config page (#85): its effective configuration and profile overrides."""
    return reverse("stories:config", args=[story_id])


@dataclass(frozen=True)
class Marker:
    """Whether a setting is the story's own or the default, as the page shows it."""

    source: str  # STORY, DEFAULT or UNKNOWN
    text: str  # "story", "default", "default (5)", "unknown"

    @property
    def overridden(self) -> bool:
        return self.source == STORY


def marker(
    name: str,
    value: Any,
    deployment: Mapping[str, Any] | None,
    *,
    inactive: Mapping[str, Any] | None = None,
) -> Marker:
    """``name``'s marker for its read ``value``; ``deployment`` is ``GET /admin/settings``
    (``None`` when the read failed); ``inactive`` the read's ``settings["inactive"]``."""
    if name in NULLABLE:
        if value is not None:
            return Marker(STORY, STORY)
        if name in MULTI_CHARACTER:
            return Marker(DEFAULT, f"default ({null_default_text(name, inactive)})")
        if name not in DEPLOYMENT_DEFAULTS:
            return Marker(DEFAULT, DEFAULT)
        if deployment is None or deployment.get(name) is None:
            return Marker(UNKNOWN, UNKNOWN)
        return Marker(DEFAULT, f"default ({deployment[name]})")
    default = CONSTANT_DEFAULTS.get(name)
    if same_value(value, default):
        return Marker(DEFAULT, DEFAULT)
    return Marker(STORY, STORY)


def same_value(a: Any, b: Any) -> bool:
    """Equal as JSON values, never ``True`` and ``1``."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return bool(a == b)


def other_settings(settings: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """The keys the read returns that the form does not edit (old keys an M2-era story still
    carries), in the Engine's order: shown read-only, never sent."""
    return [(key, value) for key, value in settings.items() if key not in SETTABLE]


def budget_source(source: str) -> str:
    """``effective_budget.source`` for display (the formula reads "9 + G formula")."""
    return BUDGET_SOURCES.get(source, source)


# --- the config-change ``setting`` filter ---------------------------------------------------------

PROFILE_OVERRIDES = "profile_overrides"
RATING_AND_PROFILE = ("content_rating_id", "engine_profile_id", "engine_profile_version_pin")


def setting_groups(
    names: Iterable[str], *, participation: Iterable[tuple[str, str]] = ()
) -> list[tuple[str, list[tuple[str, str]]]]:
    """The recorded ``setting`` enum as select groups: current settings, the
    ``participation:<id>`` keys given as ``(name, label)`` (#197; the enum lists none), historical
    ones (the superseded dials), profile overrides, and the story's rating and profile. A name
    the groups do not expect goes to "Other"."""
    groups: dict[str, list[tuple[str, str]]] = {
        "Settings": [],
        "Participation": list(participation),
        "Historical": [],
        "Profile overrides": [],
        "Rating and profile": [],
        "Other": [],
    }
    for name in names:
        root = name.split(".", 1)[0]
        if name in SETTABLE:
            group = "Settings"
        elif root in SUPERSEDED:
            group = "Historical"
        elif root == PROFILE_OVERRIDES:
            group = "Profile overrides"
        elif name in RATING_AND_PROFILE:
            group = "Rating and profile"
        else:
            group = "Other"
        groups[group].append((name, name))
    return [(title, options) for title, options in groups.items() if options]
