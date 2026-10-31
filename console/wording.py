"""The shared wording of every M5 page (#187): enum values, character labels and write refusals.

**Convention 16 (C3): enum wording.** Every enum value shown, known or unknown, goes through
:func:`humanised` (template filter ``humanised``): underscores → spaces, first letter capitalised,
no "(unknown)" suffix; the raw value sits in ``title`` where the element allows (the template
adds it). The lowercase ``humanise`` filter stays for M3 pages. A display table replaces
``humanised`` only where a story's AC gives a specific label. Flashes say "<Humanised field>:
<humanised value>", never raw ``name=value``.

**Convention 17 (C4): character labels.** :func:`character_label` ``(character_id, names,
minor=frozenset()) -> str``: the name from ``console.canon.cast_names`` over the page's one
``get_story`` read (convention 2, which already appends " (player)"), else
``short_id(character_id)``; " (minor)" appended last when the id is in ``minor``; "—" for a blank
id. :func:`minor_ids` ``(cast)`` (ids of cast members whose ``weight`` is ``minor``; a missing or
unknown weight is not minor) is called only by pages that already read the omniscient cast (Inspect,
the cast panel); no page adds a read for it. HTMX inspector endpoints (S14/#203) build ``names``
from the play page's signed snapshot carried on their ``hx-get`` links; with no valid snapshot,
short ids. Propositions reuse M4's ``proposition_view`` / ``_proposition.html`` with one
``list_predicates`` read per page.

**Convention 18 (C5): write refusals.** :func:`write_refusal` ``(exc, story_id, verb_phrase) ->
WriteRefusal(text, url) | None`` (a frozen dataclass; ``url`` "" when none, as ``PausedRefusal``),
matched on ``code`` alone:

- ``story_archived`` → :func:`archived_refusal` ``(verb_phrase)`` = "This story is archived —
  unarchive it to <verb phrase>." (each story names its verb phrase, e.g. "change a character's
  role");
- ``story_paused`` → ``story_pause.paused_refusal(exc, story_id)`` (text and check URL, with the
  caller's story id);
- ``story_busy`` → :func:`busy_notice` ``(exc.details)``: "Story busy — another turn is running" /
  "Story busy — queue full (N waiting)" ("—" for a missing depth) / "Story busy" (#48's wording,
  moved out of ``error_inspector``);
- ``revision_conflict`` → ``console.timeline.reload_message(details.current_revision)``;
- ``erased_subject_referenced`` → :data:`ERASED_TEXT` "This text refers to an erased subject —
  nothing was saved" (#48's);
- ``idempotency_conflict``, any other code, and a non-``EngineError`` → ``None``: the caller
  renders #43's ``panel_for(exc)``.

After a redirect the text goes in the flash and the URL is dropped (a PAUSED link can't survive a
flash); a re-rendered form shows both. M3/M4 pages keep their own archived strings
(``ARCHIVED_REFUSED``, ``ARCHIVED_ADD``, the Canon and Memory tables); only M5 writes use
:func:`archived_refusal`.

**Convention 19 (no C; C3 applies): confidence.** A level renders humanised (C3, not verbatim);
with a scalar, "Believed · 0.720" (3 decimals; the scalar is authoritative). Polarity humanised.
Provenance humanised, and ``testimony`` is always shown and never abbreviated.

**Convention 20 (no C): fidelity.** #62 owns the wording and styles (``console/history.py``
``FIDELITY_BADGES``; classes ``fidelity-envelope``, ``fidelity-none``). #195, #196 and S14 reuse
them. Meaning is never carried by colour alone: every non-content beat has a text badge. The
Console styles what the Engine marks and never filters beats itself.

**Convention 21 (no C): audience filter and player view.** #62's ``audience_filter_label(value)
-> (text, raw)`` is the one wording. Wherever beats are shown for a character viewpoint, one line
reads "Audience filter: <text> · Player view: <character|story>", with raw values in ``title``. An
omniscient page shows neither.

**Convention 23 (no C; C5 for refusals, C9 for no-op forms): writes.** #135 convention 8 holds.
Weight, participation, story-setting and entity-modelling (#199) writes POST-redirect-GET with a
flash (#199's 201 answers with a character, not a report). The belief write (#194) answers a
report, so it gets a result page reachable via ``?report=``. None sends
``X-Epistrel-Expected-Revision``.

**Convention 24 (no C; C5 first): errors and Engine gaps.** Each story words its own route's
codes, beside the field they name, using convention 18 first. Two more wordings are shared:

- ``unknown_character`` always reads :data:`UNKNOWN_CHARACTER`, "That character isn't in this
  story's cast";
- a ``module_off`` on a write renders as a form error with #188's
  ``unavailable_for(details.module, effective).reason`` and a Config link, never #43's generic
  panel.

Where the Console reconstructs or omits what the Engine doesn't serve, the page says so once,
naming the request: nested beliefs (#606) on Knowledge (#189's shell notice), the perception
trace (#607) on the grid. Respond's undeclared 409 (#608) is noted in code comments only.
"""

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any, Protocol

from console.engine.client import EngineClientError, EngineError
from console.engine_errors import EMPTY
from console.inspect import short_id
from console.story_pause import paused_refusal
from console.timeline import reload_message

MINOR = "minor"
MINOR_SUFFIX = " (minor)"
UNKNOWN_CHARACTER = "That character isn't in this story's cast"
ERASED_TEXT = "This text refers to an erased subject — nothing was saved"


def humanised(value: str) -> str:
    """Convention 5 / 16: ``unresolved_entity`` → "Unresolved entity" (unknown values the same)."""
    return value.replace("_", " ").capitalize()


# --- character labels (convention 17) ------------------------------------------------------------


class Weighted(Protocol):
    """A cast member with a weight: #183's ``OmniscientStoryCharacter`` fits."""

    @property
    def id(self) -> str: ...
    @property
    def weight(self) -> str: ...


def character_label(
    character_id: str | None, names: Mapping[str, str], minor: AbstractSet[str] = frozenset()
) -> str:
    """The cast name (``cast_names``, " (player)" already on), else the short id; " (minor)"
    last for a minor character; "—" for a blank id."""
    if not character_id:
        return EMPTY
    label = names.get(character_id) or short_id(character_id)
    return f"{label}{MINOR_SUFFIX}" if character_id in minor else label


def minor_ids(cast: Iterable[Weighted] | None) -> frozenset[str]:
    """The ids of the omniscient cast's minor members (exactly ``weight == "minor"``)."""
    if cast is None:
        return frozenset()
    return frozenset(member.id for member in cast if member.weight == MINOR)


# --- write refusals (convention 18) ---------------------------------------------------------------


@dataclass(frozen=True)
class WriteRefusal:
    """A refused M5 write's line, and its link ("" when none)."""

    text: str
    url: str = ""


def archived_refusal(verb_phrase: str) -> str:
    """ "This story is archived — unarchive it to <verb phrase>." (lower-case, no punctuation)."""
    return f"This story is archived — unarchive it to {verb_phrase}."


def busy_notice(details: Mapping[str, Any]) -> str:
    """#48's 409 ``story_busy`` line, by ``reason``."""
    reason = details.get("reason")
    if reason == "queue_full":
        depth = details.get("queue_depth")
        return f"Story busy — queue full ({EMPTY if depth is None else depth} waiting)"
    if reason == "in_flight":
        return "Story busy — another turn is running"
    return "Story busy"


def write_refusal(exc: EngineClientError, story_id: str, verb_phrase: str) -> WriteRefusal | None:
    """Convention 18's wording for a refused write; ``None`` → the caller renders ``panel_for``."""
    if not isinstance(exc, EngineError):
        return None
    if exc.code == "story_archived":
        return WriteRefusal(archived_refusal(verb_phrase))
    if exc.code == "story_paused":
        paused = paused_refusal(exc, story_id)
        return WriteRefusal(paused.text, paused.url)
    if exc.code == "story_busy":
        return WriteRefusal(busy_notice(exc.details))
    if exc.code == "revision_conflict":
        return WriteRefusal(reload_message(exc.details.get("current_revision")))
    if exc.code == "erased_subject_referenced":
        return WriteRefusal(ERASED_TEXT)
    return None
