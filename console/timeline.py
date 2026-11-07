"""Timeline operation links (#53): what the play view's transcript, its header and the history
page's Variants panel offer — regenerate, rewind, undo rewind, swap and fork — and the wording
the operation pages share.

Every link carries what the operator was looking at, ``?rev=<timeline_revision>&head=<head
turn>&name=<story name>&next=<page>``, so the confirm GET makes no Engine call and its POST
sends ``rev`` as ``X-Epistrel-Expected-Revision``. A stale page then gets the Engine's 409
``revision_conflict`` instead of changing state the operator did not see.

**The external effects an edit could not undo (#258, FR-EXT-13/14).** :func:`edit_effects` and
:func:`fork_effects` read a rewind's, undo rewind's, swap's or fork's answer into
:class:`EditEffects`: its ``reconciliation_required``, ``cancelled_on_reactivation`` and
``effects_pending_in_source`` rows, each with its full outbox id (D11) linked to the
reconciliation list (``outbox_url``, D19), the D12 count link, and the headings and lines the
report page and #81's re-check page show. ``None`` when every list is empty, so the edit keeps
its redirect and flash. A fork's rows belong to the **source** story: its copies in the new
story are records the Engine never lists.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlencode

from django.urls import NoReverseMatch, reverse
from django.utils.safestring import SafeString

if TYPE_CHECKING:
    from console.engine.models import (
        ForkResult,
        PendingEffect,
        RequiredEffect,
        SwapResult,
        TruncateResult,
        UntruncateResult,
    )

ARCHIVED_NOTE = "Archived — unarchive to change the timeline"
PAUSED_NOTE = "Paused while the check runs"  # #80: every control, Fork included
CHECK_FLAG = "1"  # ``&check=1``: the swap target's ``requires_check`` (the confirm page warns)
# A chain a prune discarded (FR-STORE-19, ADR-175, #293): only its operations remain, as markers.
# The Engine refuses Swap, Fork and ``history?variant=`` on it with 409 ``pruned``.
PRUNED_MEMBERSHIP = "pruned"
PRUNED_CHAIN_NOTE = "Its records were pruned; only its operations remain as markers"


class Chain(Protocol):
    """A variant chain as ``GET /stories/{id}`` and ``GET /stories/{id}/variants`` list it."""

    @property
    def turn(self) -> int: ...
    @property
    def variant(self) -> int: ...
    @property
    def membership(self) -> str: ...
    @property
    def requires_check(self) -> bool: ...


def chain_label(turn: int, variant: int) -> str:
    return f"turn {turn} · variant {variant}"


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Basis:
    """The state a page showed: what every timeline link it renders carries."""

    story_id: str
    rev: int
    head: int  # ``next_turn_index - 1``
    name: str
    archived: bool
    next_url: str  # where Cancel goes back to
    paused: bool = False  # #80: a check runs; the Engine refuses every operation

    def query(self, **extra: str) -> str:
        params = {
            "rev": str(self.rev),
            "head": str(self.head),
            "name": self.name,
            "next": self.next_url,
            **extra,
        }
        return urlencode(params)

    def url(self, route: str, *args: object, **extra: str) -> str:
        path = reverse(f"stories:{route}", args=[self.story_id, *args])
        return f"{path}?{self.query(**extra)}"

    def swap(self, turn: int, variant: int, *, requires_check: bool = False) -> Link:
        extra = {"chain": f"{turn}:{variant}"}
        if requires_check:
            extra["check"] = CHECK_FLAG
        return Link(f"Swap to {chain_label(turn, variant)}", self.url("swap", **extra))

    def fork(self, turn: int, *, chain: tuple[int, int] | None = None) -> Link:
        extra = {"variant_chain": f"{chain[0]}:{chain[1]}"} if chain else {}
        return Link("Fork from here", self.url("fork", turn, **extra))

    def rewind(self, turn: int) -> Link:
        return Link("Rewind to start" if turn == 0 else "Rewind to here", self.url("rewind", turn))

    def undo(self) -> Link:
        return Link("Undo rewind", self.url("undo-rewind"))

    def regenerate(self, snapshot: str) -> Link:
        return Link("Regenerate", self.url("regenerate", snapshot=snapshot))


@dataclass(frozen=True)
class TurnControls:
    """One transcript turn's operations. ``rewind``/``regenerate`` are ``None`` where they do
    not apply (the head turn has no "Rewind to here"; only the head turn's reply regenerates).
    While archived, everything but Fork renders disabled with :data:`ARCHIVED_NOTE`; while
    paused (#80), everything does, with :data:`PAUSED_NOTE`."""

    turn: int
    fork: Link
    rewind: Link | None = None
    regenerate: Link | None = None
    swaps: tuple[Link, ...] = ()
    archived: bool = False
    paused: bool = False

    @property
    def disabled_note(self) -> str:
        """Why Regenerate, Rewind and Swap are disabled ("" when they are not)."""
        if self.paused:
            return PAUSED_NOTE
        return ARCHIVED_NOTE if self.archived else ""


def turn_controls(
    basis: Basis,
    turn: int,
    *,
    chains: Iterable[Chain] = (),
    has_reply: bool = False,
    snapshot: str = "",
) -> TurnControls:
    """Turn ``turn``'s controls: Rewind below the head, Regenerate on the head's reply, Swap for
    every non-active chain diverging at this turn but a pruned one (#293), Fork always (turn 0
    included)."""
    swaps = tuple(
        basis.swap(c.turn, c.variant, requires_check=c.requires_check)
        for c in chains
        if c.turn == turn and c.membership not in ("active", PRUNED_MEMBERSHIP)
    )
    return TurnControls(
        turn=turn,
        fork=basis.fork(turn),
        rewind=basis.rewind(turn) if turn < basis.head else None,
        regenerate=(
            basis.regenerate(snapshot) if turn == basis.head and turn >= 1 and has_reply else None
        ),
        swaps=swaps,
        archived=basis.archived,
        paused=basis.paused,
    )


# --- Wording the operation pages share ------------------------------------------------------------


def reload_message(current_revision: object) -> str:
    """The 409 ``revision_conflict`` line (the AC's wording)."""
    now = current_revision if isinstance(current_revision, int) else "?"
    return f"The story changed since you loaded this page (now revision {now}) — reload"


# --- External effects a timeline edit could not undo (#258) ---------------------------------------

REQUIRED_HEADING = "External effects this edit could not undo"
REQUIRED_LINE = (
    "They had already reached the provider. Each stays listed for reconciliation until it is "
    "resolved; nothing was compensated."
)
CANCELLED_HEADING = "Effects cancelled while their turns were out"
CANCELLED_LINE = (
    "Their turns are active again, but a cancelled effect is never re-sent. Each is listed for "
    "reconciliation."
)
PENDING_HEADING = "Effects still pending in the source story"
PENDING_LINE = (
    "The fork copies them as records only and never dispatches them; the source story keeps "
    "dispatching its own, and its Reconciliation lists any that need attention."
)
SOURCE_RECONCILIATION_LABEL = "Source story's Reconciliation"
RECONCILIATION_ROUTE = "inspect:reconciliation"


@dataclass(frozen=True)
class EffectRef:
    """One outbox row an edit reports. ``url`` is the reconciliation list at ``#outbox-<id>``
    (``None`` while unregistered, D19); ``status`` is humanised (D10), ``status_raw`` verbatim.
    ``value`` is the provider value verbatim, or #249's ``REDACTED`` text (``redacted``) when
    the Engine sent none; "" on a pending row, which carries no provider or value.
    ``effect_text`` is #249's wording of a pending row's effect, "" otherwise."""

    outbox_id: str
    provider: str
    value: str
    status: str
    status_raw: str
    effect_text: str
    url: str | None
    redacted: bool = False


@dataclass(frozen=True)
class EditEffects:
    """What a timeline edit could not undo or re-send, for ``_edit_effects.html``. ``story_id``
    is the story whose Reconciliation lists the rows (a fork's source). ``link`` is D12's
    ``reconciliation_link`` over the required and cancelled rows; a fork's pending rows aren't
    counted (they may not be listed yet), so a fork carries ``source_link`` instead."""

    story_id: str
    required: tuple[EffectRef, ...] = ()
    cancelled: tuple[EffectRef, ...] = ()
    pending: tuple[EffectRef, ...] = ()
    link: tuple[str, str] | None = None
    source_link: Link | None = None

    @property
    def count(self) -> int:
        return len(self.required) + len(self.cancelled)

    @property
    def count_text(self) -> str | None:
        """D12's words, shown unlinked while ``link`` is ``None`` (D19)."""
        from console.effects import reconciliation_text

        return reconciliation_text(self.count)

    @property
    def notice(self) -> SafeString:
        """D11's outbox-list notice, through its one renderer."""
        from console.effects import outbox_list_notice_html

        return outbox_list_notice_html(story_id=self.story_id)

    @property
    def sections(self) -> tuple[EffectSection, ...]:
        """The non-empty lists in the report's order: required, cancelled, pending."""
        found = (
            EffectSection(REQUIRED_HEADING, REQUIRED_LINE, self.required),
            EffectSection(CANCELLED_HEADING, CANCELLED_LINE, self.cancelled),
            EffectSection(PENDING_HEADING, PENDING_LINE, self.pending, pending=True),
        )
        return tuple(section for section in found if section.rows)


@dataclass(frozen=True)
class EffectSection:
    """One list of :class:`EditEffects` as the partial renders it: a pending list has Status
    and Effect columns, the others Provider, Provider value and Status."""

    heading: str
    line: str
    rows: tuple[EffectRef, ...]
    pending: bool = False


# console.effects and console.reconciliation import console.wording, which imports this module
# (``reload_message``): they are imported where they are used, not at the top.


def _required_ref(story_id: str, row: RequiredEffect) -> EffectRef:
    from console.effects import outbox_url
    from console.reconciliation import REDACTED
    from console.wording import humanised

    redacted = row.value is None
    return EffectRef(
        outbox_id=row.outbox_id,
        provider=row.provider,
        value=REDACTED if redacted else row.value or "",
        status=humanised(row.status),
        status_raw=row.status,
        effect_text="",
        url=outbox_url(story_id, row.outbox_id),
        redacted=redacted,
    )


def _pending_ref(story_id: str, row: PendingEffect) -> EffectRef:
    from console.effects import outbox_url
    from console.reconciliation import effect_text
    from console.wording import humanised

    return EffectRef(
        outbox_id=row.outbox_id,
        provider="",
        value="",
        status=humanised(row.status),
        status_raw=row.status,
        effect_text=effect_text(row.effect),
        url=outbox_url(story_id, row.outbox_id),
    )


def edit_effects(
    story_id: str, result: TruncateResult | UntruncateResult | SwapResult
) -> EditEffects | None:
    """A rewind's, undo rewind's or swap's effects; ``None`` when every list is empty."""
    from console.effects import reconciliation_link
    from console.engine.models import TruncateResult

    required = tuple(_required_ref(story_id, row) for row in result.reconciliation_required)
    cancelled: tuple[EffectRef, ...] = ()
    if not isinstance(result, TruncateResult):
        cancelled = tuple(_required_ref(story_id, row) for row in result.cancelled_on_reactivation)
    if not required and not cancelled:
        return None
    return EditEffects(
        story_id=story_id,
        required=required,
        cancelled=cancelled,
        link=reconciliation_link(story_id, len(required) + len(cancelled)),
    )


def _source_link(story_id: str) -> Link | None:
    try:
        url = reverse(RECONCILIATION_ROUTE, args=[story_id])
    except NoReverseMatch:
        return None
    return Link(SOURCE_RECONCILIATION_LABEL, url)


def fork_effects(source_story_id: str, result: ForkResult) -> EditEffects | None:
    """A fork's source effects still pending at its boundary, linked to the **source** story's
    Reconciliation; ``None`` when there are none."""
    pending = tuple(_pending_ref(source_story_id, row) for row in result.effects_pending_in_source)
    if not pending:
        return None
    return EditEffects(
        story_id=source_story_id,
        pending=pending,
        source_link=_source_link(source_story_id),
    )
