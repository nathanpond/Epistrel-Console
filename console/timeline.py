"""Timeline operation links (#53): what the play view's transcript, its header and the history
page's Variants panel offer — regenerate, rewind, undo rewind, swap and fork — and the wording
the operation pages share.

Every link carries what the operator was looking at, ``?rev=<timeline_revision>&head=<head
turn>&name=<story name>&next=<page>``, so the confirm GET makes no Engine call and its POST
sends ``rev`` as ``X-Epistrel-Expected-Revision``. A stale page then gets the Engine's 409
``revision_conflict`` instead of changing state the operator did not see.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from django.urls import reverse

ARCHIVED_NOTE = "Archived — unarchive to change the timeline"
PAUSED_NOTE = "Paused while the check runs"  # #80: every control, Fork included
CHECK_FLAG = "1"  # ``&check=1``: the swap target's ``requires_check`` (the confirm page warns)


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
    every non-active chain diverging at this turn, Fork always (turn 0 included)."""
    swaps = tuple(
        basis.swap(c.turn, c.variant, requires_check=c.requires_check)
        for c in chains
        if c.turn == turn and c.membership != "active"
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
