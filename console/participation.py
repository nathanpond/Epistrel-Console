"""C8 (#61): how every M5 page words and checks a character's participation role and weight.

A role is ``primary``, ``reactive`` or ``silent`` (FR-MULTI-7, -10), optionally **fixed** (the
Engine never escalates a fixed reactive/silent character and never lowers a fixed primary). A
weight is ``major`` or ``minor`` (FR-EPI-10). The player's character has neither, and no page
offers it a role or weight control.

- :data:`ROLE_LABELS` / :func:`role_text`: "Primary", "Reactive (fixed)"; "—" for no role (whatever
  ``fixed`` says); an unknown role ``humanised`` (C2 ``open_enum``, C3), " (fixed)" appended the
  same way.
- :data:`WEIGHT_LABELS` / :func:`weight_label`, :data:`WEIGHT_TITLES` (the ``title`` a weight
  label or a "Make major/minor" button carries).
- :func:`role_choices` ``(engine_default)``: the three roles, C7's "Engine default (reactive)"
  (value ``""``, sends nothing) first when asked.
- :func:`participation_error` ``(role, fixed, *, is_player=False)``: the local errors, checked
  before any call — :data:`PLAYER_NO_ROLE` for a role posted for the player's character, else
  :data:`FIX_NEEDS_ROLE` for "Fixed" ticked with no role.
"""

from typing import Final, Literal, TypeGuard

from console.story_settings import engine_default_choice
from console.wording import humanised

Role = Literal["primary", "reactive", "silent"]
Weight = Literal["minor", "major"]

PRIMARY: Final = "primary"
REACTIVE: Final = "reactive"
SILENT: Final = "silent"
ROLES: tuple[Role, ...] = (PRIMARY, REACTIVE, SILENT)
DEFAULT_ROLE: Role = REACTIVE  # the Engine's default for a non-player character
ROLE_LABELS: dict[str, str] = {"primary": "Primary", "reactive": "Reactive", "silent": "Silent"}
FIXED_SUFFIX = " (fixed)"
NO_ROLE = "—"

MAJOR: Final = "major"
MINOR: Final = "minor"
DEFAULT_WEIGHT: Weight = MAJOR  # what an omniscient read means by a missing weight
WEIGHT_LABELS: dict[str, str] = {"major": "Major", "minor": "Minor"}
WEIGHT_TITLES: dict[str, str] = {
    "major": "Major: full partition and hot-memory budget",
    "minor": "Minor: sparse partition, scaled hot-memory budget, rendered only when named",
}
NO_WEIGHT = "—"

FIXED_LABEL = "Fixed"
FIXED_TITLE = "The engine never escalates or lowers a fixed role"
FIX_NEEDS_ROLE = "Choose a role to fix it."
PLAYER_NO_ROLE = "The player's character has no role."
CHOOSE_ROLE = "Choose a role."


def is_role(value: object) -> TypeGuard[Role]:
    return value in ROLES


def is_weight(value: object) -> TypeGuard[Weight]:
    return value in WEIGHT_LABELS


def role_label(role: str) -> str:
    """ "Primary" / "Reactive" / "Silent"; an unknown role humanised."""
    return ROLE_LABELS.get(role) or humanised(role)


def role_text(role: str | None, fixed: bool) -> str:
    """ "Reactive", "Primary (fixed)"; "—" for no role."""
    if not role:
        return NO_ROLE
    return f"{role_label(role)}{FIXED_SUFFIX if fixed else ''}"


def weight_label(weight: str | None) -> str:
    """ "Major" / "Minor"; "—" for an unknown weight (a failed omniscient read)."""
    if not weight:
        return NO_WEIGHT
    return WEIGHT_LABELS.get(weight) or humanised(weight)


def role_choices(engine_default: bool) -> list[tuple[str, str]]:
    """The role select's options; C7's "Engine default (reactive)" first when asked."""
    roles: list[tuple[str, str]] = [(role, ROLE_LABELS[role]) for role in ROLES]
    return [engine_default_choice(DEFAULT_ROLE), *roles] if engine_default else roles


def participation_error(role: str | None, fixed: bool, *, is_player: bool = False) -> str | None:
    """C8's local error for a role and "Fixed" about to be sent, or ``None``."""
    if is_player and role:
        return PLAYER_NO_ROLE
    if fixed and not role:
        return FIX_NEEDS_ROLE
    return None
