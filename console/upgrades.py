"""Profile upgrades in M7 (#295): presets, the upgrade mode and a backfill's pause.

**E1 — the import rule.** Every M7 page that orders presets, words an upgrade mode or a backfill
pause, or links the Backfill page imports these; none defines its own.

**E10 — a backfill's pause** (refines C5 and #80; ``console.story_pause`` routes the causes here).
A backfill pauses its story with ``cause: backfill`` while it runs and ``backfill_conflicts``
while it waits on the author. :func:`backfill_pause_text` words both: :data:`BACKFILL_PAUSED_TEXT`
plus " (<chunks_done>/<chunks_total> chunks)" under E7's count rule (both ints, total > 0, else
left out), and :data:`BACKFILL_CONFLICTS_TEXT` with ``details.conflicts_open`` pluralised (E23),
else "its conflicts". Both link :func:`backfill_url` (no link while unregistered, D19). The banner
polls ``<backfill_url>?banner=1&cause=<cause>`` at ``SLOW_POLL_TRIGGER`` while the name resolves,
and never the rating-check page (which would 404 on a backfill's job id).

**E13 — presets.** :data:`PRESET_IDS` is the ladder: Lite, Standard, Full, Max.
:func:`preset_order` lists the presets in that order (unknown presets after, by name), then the
other profiles in their given order; :func:`preset_label` reads "<name> (preset)".

**E14 — upgrade mode.** :func:`upgrade_mode_text` reads a stored or reported ``upgrade_mode``
back ("Backfill", "Forward only", else humanised; ``None`` "—"). The form field is
``console.forms.upgrades.UpgradeModeField``.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from django.urls import NoReverseMatch, reverse

from console.engine.models import PauseDetails
from console.raw import EMPTY, plural

BACKFILL = "backfill"  # Engine ``pause.BACKFILL``
BACKFILL_CONFLICTS = "backfill_conflicts"  # Engine ``pause.BACKFILL_CONFLICTS``
BACKFILL_CAUSES = frozenset({BACKFILL, BACKFILL_CONFLICTS})
BACKFILL_PAUSED_TEXT = "Paused: backfill in progress"
BACKFILL_CONFLICTS_TEXT = "Paused: the backfill is waiting for you to resolve {conflicts}"
ITS_CONFLICTS = "its conflicts"

PRESET_IDS: tuple[str, ...] = (
    "00000000000000000000PRF001",  # Lite
    "00000000000000000000PRF003",  # Standard
    "00000000000000000000PRF002",  # Full
    "00000000000000000000PRF004",  # Max
)
PRESET_SUFFIX = " (preset)"

UPGRADE_MODE_TEXT: Mapping[str, str] = {"backfill": "Backfill", "forward_only": "Forward only"}


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def chunk_counts(progress: Mapping[str, Any] | None) -> tuple[int | None, int | None]:
    """A backfill pause's ``(chunks_done, chunks_total)``, falling back to ``done``/``total``;
    ``None`` for a count that isn't an int."""
    if not isinstance(progress, Mapping):
        return None, None
    done = _int(progress.get("chunks_done"))
    total = _int(progress.get("chunks_total"))
    if done is None and total is None:
        done, total = _int(progress.get("done")), _int(progress.get("total"))
    return done, total


def backfill_pause_text(cause: str, details: PauseDetails | None) -> str:
    """E10's line for a backfill's pause (``cause`` ``backfill`` or ``backfill_conflicts``)."""
    if cause == BACKFILL_CONFLICTS:
        count = details.conflicts_open if details is not None else None
        conflicts = ITS_CONFLICTS if count is None else plural(count, "conflict")
        return BACKFILL_CONFLICTS_TEXT.format(conflicts=conflicts)
    done, total = chunk_counts(details.progress if details is not None else None)
    if done is not None and total is not None and total > 0:
        return f"{BACKFILL_PAUSED_TEXT} ({done:,}/{total:,} chunks)"
    return BACKFILL_PAUSED_TEXT


def backfill_url(story_id: str) -> str | None:
    """The story's Backfill page (``stories:backfill``, #308); ``None`` while unregistered or for
    a blank or non-string id."""
    if not isinstance(story_id, str) or not story_id:
        return None
    try:
        return reverse("stories:backfill", args=[story_id])
    except NoReverseMatch:
        return None


# --- presets (E13) --------------------------------------------------------------------------------


class Profiled(Protocol):
    """A profile as the pickers list it: #78's ``EngineProfile`` fits."""

    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def preset(self) -> bool: ...


def preset_order[P: Profiled](profiles: Iterable[P]) -> list[P]:
    """The presets in :data:`PRESET_IDS` order, unknown presets after them by name, then the
    other profiles in their given order."""
    given = list(profiles)
    presets = [p for p in given if p.preset is True]
    ladder = {profile_id: n for n, profile_id in enumerate(PRESET_IDS)}
    known = sorted((p for p in presets if p.id in ladder), key=lambda p: ladder[p.id])
    unknown = sorted((p for p in presets if p.id not in ladder), key=lambda p: (p.name, p.id))
    return [*known, *unknown, *(p for p in given if p.preset is not True)]


def preset_label(profile: Profiled) -> str:
    """ "<name> (preset)" for a preset, the bare name otherwise."""
    return f"{profile.name}{PRESET_SUFFIX}" if profile.preset is True else profile.name


# --- upgrade mode (E14) ---------------------------------------------------------------------------


def upgrade_mode_text(value: object) -> str:
    """ "Backfill", "Forward only", another value humanised (C3), ``None`` or blank "—"."""
    if value is None or value == "":
        return EMPTY
    if not isinstance(value, str):
        return str(value)
    from console.wording import humanised  # wording → story_pause → upgrades

    return UPGRADE_MODE_TEXT.get(value, humanised(value))
