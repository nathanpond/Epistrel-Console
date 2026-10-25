"""A story's settings as the settings page shows them (#54): which fields the Console edits, what
each one's default is, and whether the story overrides it.

The Engine's settings read fills defaults in and carries no override marker, so the marker is
worked out here (planner, replan 2026-10-24):

- a nullable setting (:data:`DEPLOYMENT_DEFAULTS`, plus ``default_output_viewpoint``) is
  "default" while ``null``: "default (N)" with N the deployment's own setting from
  ``GET /admin/settings`` ("unknown" when that read failed), plain "default" for the viewpoint;
  any value is "story";
- the others compare against :data:`CONSTANT_DEFAULTS` (Engine ``core/settings_schema.py``): a
  value equal to the default reads "default" (an override with the same value cannot be told
  apart), anything else "story".

The six engine dials that moved to engine-profile overrides in Engine M3 (:data:`SUPERSEDED`)
are never edited here: the page links to the story's Config page (M3 #85) instead. Any key the
read returns beyond :data:`SETTABLE` is listed read-only under "Other settings" and never sent.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# The settable ``StorySettingsPatch`` properties, in page order (``policies`` has its own form).
SETTABLE = (
    "turn_concurrency",
    "turn_queue_max",
    "default_output_viewpoint",
    "regenerate_retry_budget",
    "lite_summary_chunk",
    "audit_retention_days",
    "allow_new_arrivals",
)
POLICIES = "policies"
# Defaults the Engine fills in itself (Engine ``core/settings_schema.py``).
CONSTANT_DEFAULTS: dict[str, Any] = {
    "turn_concurrency": "reject",
    "regenerate_retry_budget": 2,
    "allow_new_arrivals": True,
}
# Nullable settings whose ``null`` means the deployment's setting of the same name.
DEPLOYMENT_DEFAULTS = ("turn_queue_max", "lite_summary_chunk", "audit_retention_days")
NULLABLE = (*DEPLOYMENT_DEFAULTS, "default_output_viewpoint")
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


def config_url(story_id: str) -> str:
    """The story's engine-profile overrides page (M3 #85)."""
    return f"/stories/{story_id}/config/"


@dataclass(frozen=True)
class Marker:
    """Whether a setting is the story's own or the default, as the page shows it."""

    source: str  # STORY, DEFAULT or UNKNOWN
    text: str  # "story", "default", "default (5)", "unknown"

    @property
    def overridden(self) -> bool:
        return self.source == STORY


def marker(name: str, value: Any, deployment: Mapping[str, Any] | None) -> Marker:
    """``name``'s marker for its read ``value``; ``deployment`` is ``GET /admin/settings``
    (``None`` when the read failed)."""
    if name in NULLABLE:
        if value is not None:
            return Marker(STORY, STORY)
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
RATING_AND_PROFILE = ("content_rating_id", "engine_profile_id")


def setting_groups(names: Iterable[str]) -> list[tuple[str, list[tuple[str, str]]]]:
    """The recorded ``setting`` enum as select groups: current settings, historical ones (the
    superseded dials), profile overrides, and the story's rating and profile. A name the groups
    do not expect goes to "Other"."""
    groups: dict[str, list[tuple[str, str]]] = {
        "Settings": [],
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
