"""A story's settings as the settings page shows them (#54): which fields the Console edits, what
each one's default is, and whether the story overrides it.

The page edits seventeen settable settings: #54's seven, Engine M5's six (#197: player
authority, player view and the multi-character settings), Engine M6's one (#233: co-author
knowledge, FR-RESP-14) and Engine M7's three (#318: the prune policy, FR-STORE-32; the backfill
conflict policy, FR-PROF-10; ambiguous retcon timing, FR-STORE-20). The Engine's settings read fills
defaults in and carries no override marker, so the marker is worked out here (planner, replan
2026-10-24):

- a nullable setting (:data:`NULLABLE`) is "default" while ``null``: "default (N)" with N the
  deployment's own setting from ``GET /admin/settings`` for :data:`DEPLOYMENT_DEFAULTS`
  ("unknown" when that read failed); "default (sequential)" for interaction mode, or
  "default (scenario: <value>)" when genesis kept the scenario's mode inactive
  (:func:`scenario_mode`); "default (request order)" for sequence order and tie-break;
  "default (off)" for the prune policy (:data:`NULL_MARKER_TEXT`); plain "default" for the
  viewpoint. Any value is "story" (a stored ``state:<name>`` tie-break too);
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
    "co_author_knowledge",
    "player_view",
    "interaction_mode",
    "sequence_order",
    "simultaneous_tie_break",
    "reactive_interjections",
    # Engine M7 (#318).
    "prune_policy",
    "backfill_conflict_policy",
    "ambiguous_retcon_timing",
)
POLICIES = "policies"
# Defaults the Engine fills in itself (Engine ``core/settings_schema.py``).
CONSTANT_DEFAULTS: dict[str, Any] = {
    "turn_concurrency": "reject",
    "regenerate_retry_budget": 2,
    "allow_new_arrivals": True,
    "player_authority": "own_character",
    "co_author_knowledge": "confirm",
    "player_view": "character",
    "reactive_interjections": False,
    "backfill_conflict_policy": "last_mention",
    "ambiguous_retcon_timing": "include_if_possible",
}
# Nullable settings whose ``null`` means the deployment's setting of the same name.
DEPLOYMENT_DEFAULTS = ("turn_queue_max", "lite_summary_chunk", "audit_retention_days")
# Engine M5's nullable multi-character settings (``null``: the Engine's default, below).
MULTI_CHARACTER = ("interaction_mode", "sequence_order", "simultaneous_tie_break")
NULLABLE = (*DEPLOYMENT_DEFAULTS, "default_output_viewpoint", *MULTI_CHARACTER, "prune_policy")
# What a null setting means where the marker names it: "default (<text>)" (#318).
NULL_MARKER_TEXT = {"prune_policy": "off"}
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
    "co_author_knowledge": "Co-author knowledge",
    "player_view": "Player view (where perception is on)",
    "interaction_mode": "Interaction mode",
    "sequence_order": "Sequence order (sequential turns)",
    "simultaneous_tie_break": "Tie-break (simultaneous turns)",
    "reactive_interjections": "Reactive interjections",
    "prune_policy": "Automatic pruning (days)",
    "backfill_conflict_policy": "Backfill conflicts",
    "ambiguous_retcon_timing": "Ambiguous retcon timing",
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
# Each of M5's, M6's and M7's settings' explicit choices (never an Engine-default entry); #198,
# #233 and #318 reuse them.
SETTING_CHOICES: dict[str, list[tuple[str, str]]] = {
    "player_authority": [
        ("own_character", "Own character only"),
        ("co_author", "Co-author (may narrate others' observable actions)"),
    ],
    "co_author_knowledge": [
        ("confirm", "Confirm (accept, with a notice)"),
        ("free", "Free (accept, no notice)"),
    ],
    "player_view": [
        ("character", "Character (audience-filtered)"),
        ("story", "Story (every beat in full)"),
    ],
    "interaction_mode": [("sequential", "Sequential"), ("simultaneous", "Simultaneous")],
    "sequence_order": [("request", "Request order"), ("director", "Director")],
    "simultaneous_tie_break": [("request_order", "Request order"), ("director", "Director")],
    "reactive_interjections": BOOLEAN_SELECT,
    "backfill_conflict_policy": [
        ("last_mention", "Latest statement wins"),
        ("first_mention", "Earliest statement wins"),
        ("ask", "Ask me (the story stays paused until I resolve them)"),
    ],
    "ambiguous_retcon_timing": [
        ("include_if_possible", "Include it wherever it might apply (marked timing uncertain)"),
        ("ask_author", "Ask me when the rewrite is planned"),
    ],
}
# When co-author knowledge matters (#233): the settings, new-story and scenario forms' help.
CO_AUTHOR_KNOWLEDGE_HELP = (
    "Only under Co-author player authority with per-character partitions on; no effect under Lite."
)
# Engine M7's three (#318): the settings page's help.
PRUNE_POLICY_HELP = (
    "Prune this story's discarded tails and dormant continuations older than this many days, in "
    "the Engine's daily run (1 to 3650). Blank: never prune automatically. Setting it deletes "
    "nothing by itself."
)
BACKFILL_CONFLICT_HELP = (
    "How a backfill settles the story's old text contradicting itself; read when a backfill "
    "starts. The losing statements stay in the transcript as in-world claims."
)
RETCON_TIMING_HELP = (
    "How a rewrite places a correction whose time the story clock can't decide. No effect under "
    "Lite."
)
# ``PrunePolicy.older_than_days`` (the recorded bounds).
PRUNE_MIN_DAYS = 1
PRUNE_MAX_DAYS = 3650
PRUNE_DAYS = "older_than_days"
# The Engine's ``state:<name>`` tie-break (M6; 422 ``not_yet`` until then).
STATE_TIE_BREAK = re.compile(r"^state:[a-z][a-z0-9_]{0,63}$")
INTERACTION_MODES = ("sequential", "simultaneous")
# What a null multi-character setting means, as "default (<text>)" and "Engine default (<text>)".
NULL_DEFAULT_TEXT = {
    "interaction_mode": "sequential",
    "sequence_order": "request order",
    "simultaneous_tie_break": "request order",
}


def policy_days(value: object) -> int | None:
    """A stored prune policy's day count when it is well formed: exactly ``{older_than_days}``,
    a non-bool int within 1 to 3650. ``None`` for ``null`` and for any other value (an odd policy,
    which the page renders blank and never resends unless changed)."""
    if not isinstance(value, Mapping) or set(value) != {PRUNE_DAYS}:
        return None
    days = value[PRUNE_DAYS]
    if isinstance(days, bool) or not isinstance(days, int):
        return None
    return days if PRUNE_MIN_DAYS <= days <= PRUNE_MAX_DAYS else None


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


def setting_label(name: str, value: object) -> str:
    """A stored M5 setting as a read-only page shows it (#198's scenario detail): "—" for
    ``None``, "On" / "Off" for a boolean, the tie-break through :func:`tie_break_label`, else
    its :data:`SETTING_CHOICES` label (an unknown value :func:`humanised`, C3)."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "On" if value else "Off"
    text = str(value)
    if name == "simultaneous_tie_break":
        return tie_break_label(text)
    return dict(SETTING_CHOICES.get(name, [])).get(text) or humanised(text)


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
        if name in NULL_MARKER_TEXT:
            return Marker(DEFAULT, f"default ({NULL_MARKER_TEXT[name]})")
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
PROVIDERS = ("required_providers",)
"""Creation-time settings no PATCH sets: the providers a story requires (M6)."""


def setting_groups(
    names: Iterable[str], *, participation: Iterable[tuple[str, str]] = ()
) -> list[tuple[str, list[tuple[str, str]]]]:
    """The recorded ``setting`` enum as select groups: current settings, the
    ``participation:<id>`` keys given as ``(name, label)`` (#197; the enum lists none), historical
    ones (the superseded dials), profile overrides, the story's rating and profile, and its
    required providers ("Providers", M6). A dotted name whose root is settable goes under
    "Settings" (``prune_policy.older_than_days``, #318). A name the groups do not expect goes to
    "Other"."""
    groups: dict[str, list[tuple[str, str]]] = {
        "Settings": [],
        "Participation": list(participation),
        "Historical": [],
        "Profile overrides": [],
        "Rating and profile": [],
        "Providers": [],
        "Other": [],
    }
    for name in names:
        root = name.split(".", 1)[0]
        if name in SETTABLE or root in SETTABLE:
            group = "Settings"
        elif root in SUPERSEDED:
            group = "Historical"
        elif root == PROFILE_OVERRIDES:
            group = "Profile overrides"
        elif name in RATING_AND_PROFILE:
            group = "Rating and profile"
        elif name in PROVIDERS:
            group = "Providers"
        else:
            group = "Other"
        groups[group].append((name, name))
    return [(title, options) for title, options in groups.items() if options]
