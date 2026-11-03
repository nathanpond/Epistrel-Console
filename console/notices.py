"""Co-author implausible-knowledge notices beside the turn, with Override (#185, FR-RESP-14).

Under ``player_authority = co_author`` with per-character partitions on, the Engine commits a
turn whose narration has a character act on something it had no way to know, and (under
``co_author_knowledge = confirm``) answers one ``implausible_knowledge`` notice per such
presupposition. A notice is built from the player's words alone (NFR-PRIV-4): the Console
shows ``claim_text`` verbatim and never rewords it.

One notice view serves the play inspector and History: :func:`notice_cards` turns a raw
``notices`` list (or #231's typed ``Notice``s) into :class:`NoticeCards`, parsing each entry on
its own (D15: an entry that doesn't parse is kept raw under More, C2), and
``templates/console/_notices.html`` renders them.

- **Fix / Clarify** links #263's correction page (``stories:correct``) and is left out entirely
  while that name doesn't resolve (#64's nav rule). Where it can't work it is greyed with one
  reason, checked in the order not correctable → chain view → not the latest turn.
- **Override** dismisses a notice for this browser session only (founder decision: no Console
  table). It makes no Engine call: the ids live in the Django session under
  :data:`NOTICES_OVERRIDDEN_KEY`, newest :data:`NOTICES_OVERRIDDEN_MAX` kept, matched by notice
  id only, and :func:`overridden_notices` is the one source of a card's ``overridden``.
- A notice on a turn with ``external_effects`` warns that a correction can't undo it; the
  words "listed for reconciliation" link ``inspect:reconciliation`` when it resolves (D19).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse

from console.engine.models import Notice, rejected_entries
from console.wording import character_label, humanised

NOTICES_OVERRIDDEN_KEY = "console.notices.overridden"
NOTICES_OVERRIDDEN_MAX = 500

IMPLAUSIBLE_KNOWLEDGE = "implausible_knowledge"
IMPLAUSIBLE_KNOWLEDGE_TEXT = "Implausible knowledge"
NO_WAY_TO_KNOW = "{label} had no way to know"
NOW_KNOWS = "The turn committed anyway, and {label} now knows it."
# The card's other fixed words ("Fix / Clarify", "Override", "Overridden this session" and the
# external-effects warning) are written once, in ``templates/console/_notices.html``; #263's
# correction page says the warning as one plain line (no link), :data:`EXTERNAL_EFFECTS_TEXT`.
EXTERNAL_EFFECTS_TEXT = (
    "This turn has an external effect a correction can't undo — it stays listed for reconciliation"
)

NOT_CORRECTABLE_REASON = "Written by a correction — its notices are informational"
NOT_LATEST_REASON = (
    "Only the story's latest turn can be corrected — rewind or fork to change an earlier one"
)
CHAIN_REASON = "Viewing another chain — correct from the active timeline"


def unread_text(count: int) -> str:
    """ "1 notice couldn't be read" / "N notices couldn't be read" ("" for none)."""
    if not count:
        return ""
    return f"{count} notice{'' if count == 1 else 's'} couldn't be read"


@dataclass(frozen=True)
class NoticeCard:
    """One notice as a card. ``character`` is the C4 label; ``fix_url`` set offers Fix / Clarify,
    ``fix_disabled_reason`` set greys it with that reason, neither leaves it out."""

    id: str
    kind_text: str
    character: str
    claim_text: str
    turn: int
    overridden: bool = False
    external_effects: bool = False
    fix_url: str = ""
    fix_disabled_reason: str = ""
    # The rest renders the card: the Engine kind, the character id (the Override form's
    # display-only fields), where Override returns, the story it posts to, the reconciliation
    # link and whether the card has controls at all (#263's ``controls=False``).
    kind: str = IMPLAUSIBLE_KNOWLEDGE
    character_id: str = ""
    story_id: str = ""
    next_url: str = ""
    reconciliation_url: str = ""
    controls: bool = True

    @property
    def implausible(self) -> bool:
        return self.kind == IMPLAUSIBLE_KNOWLEDGE

    @property
    def no_way_to_know(self) -> str:
        return NO_WAY_TO_KNOW.format(label=self.character)

    @property
    def now_knows(self) -> str:
        return NOW_KNOWS.format(label=self.character)


@dataclass(frozen=True)
class NoticeCards:
    """A turn's notice cards in the Engine's order, and the raw entries that didn't parse.
    Falsy with neither, so callers store ``None`` then."""

    cards: tuple[NoticeCard, ...] = ()
    unparsed: tuple[Any, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.cards or self.unparsed)

    @property
    def unread(self) -> str:
        return unread_text(len(self.unparsed))


def _parse(entry: Any) -> Notice | None:
    if isinstance(entry, Notice):
        return entry
    return Notice.from_json(entry)


def _reverse(name: str, *args: Any) -> str | None:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def _fix(
    notice: Notice,
    story_id: str,
    *,
    latest: int | None,
    chain: bool,
    next_url: str,
) -> tuple[str, str]:
    """(fix_url, fix_disabled_reason): both "" when there is no Fix control at all (an unknown
    kind, or ``stories:correct`` unregistered)."""
    if notice.kind != IMPLAUSIBLE_KNOWLEDGE:
        return "", ""
    url = _reverse("stories:correct", story_id, notice.turn)
    if url is None:
        return "", ""
    if not notice.correctable:
        return "", NOT_CORRECTABLE_REASON
    if chain:
        return "", CHAIN_REASON
    if latest is not None and notice.turn != latest:
        return "", NOT_LATEST_REASON
    return (f"{url}?{urlencode({'next': next_url})}" if next_url else url), ""


def notice_cards(
    raw: Any,
    story_id: str,
    names: Mapping[str, str],
    *,
    overridden: frozenset[str],
    latest: int | None,
    chain: bool = False,
    controls: bool = True,
    next_url: str = "",
) -> NoticeCards:
    """The cards for a ``notices`` value: a raw list (mappings) or typed ``Notice``s, ``None``
    for none, any other value one unreadable entry. ``latest`` is the story's latest turn on
    the active chain (``None``: unknown, so a correctable card offers Fix and #263 re-checks);
    ``chain`` greys Fix on a History ``?variant=`` view; ``next_url`` is where Fix and Override
    return. ``controls=False`` drops Fix, Override, the overridden collapse and the per-card
    external-effects warning."""
    if raw is None:
        return NoticeCards()
    if not isinstance(raw, list | tuple):
        return NoticeCards((), tuple(rejected_entries(raw, _parse)))
    listed = list(raw)
    reconciliation = _reverse("inspect:reconciliation", story_id) or ""
    cards = []
    for entry in listed:
        notice = _parse(entry)
        if notice is None:
            continue
        fix_url, reason = (
            _fix(notice, story_id, latest=latest, chain=chain, next_url=next_url)
            if controls
            else ("", "")
        )
        implausible = notice.kind == IMPLAUSIBLE_KNOWLEDGE
        cards.append(
            NoticeCard(
                id=notice.id,
                kind_text=IMPLAUSIBLE_KNOWLEDGE_TEXT if implausible else humanised(notice.kind),
                character=character_label(notice.character_id, names),
                claim_text=notice.claim_text,
                turn=notice.turn,
                overridden=controls and notice.id in overridden,
                external_effects=controls and notice.external_effects,
                fix_url=fix_url,
                fix_disabled_reason=reason,
                kind=notice.kind,
                character_id=notice.character_id,
                story_id=story_id,
                next_url=next_url,
                reconciliation_url=reconciliation,
                controls=controls,
            )
        )
    return NoticeCards(tuple(cards), tuple(rejected_entries(listed, _parse)))


def overridden_card(
    notice_id: str, *, character: str = "", claim_text: str = "", character_id: str = ""
) -> NoticeCard:
    """The overridden line the override view answers an HTMX request with (labels from the
    caller: the snapshot, the posted label or the short id; blank when nothing was posted)."""
    return NoticeCard(
        id=notice_id,
        kind_text="",
        character=character,
        claim_text=claim_text,
        turn=0,
        overridden=True,
        character_id=character_id,
    )


# --- the session's overridden ids ---------------------------------------------------------------


def _stored(request: HttpRequest) -> list[str]:
    """The session's ids, newest last; a corrupt value (not a list) reads empty and non-string
    members are dropped."""
    value = request.session.get(NOTICES_OVERRIDDEN_KEY)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def overridden_notices(request: HttpRequest) -> frozenset[str]:
    """The notice ids this browser session has overridden (the one source of ``overridden``)."""
    return frozenset(_stored(request))


def remember_override(request: HttpRequest, notice_id: str) -> None:
    """Add ``notice_id`` as the newest override (moving it when already there) and keep the
    newest :data:`NOTICES_OVERRIDDEN_MAX`. The list is reassigned, so Django saves it."""
    kept: Iterable[str] = (item for item in _stored(request) if item != notice_id)
    found = [*kept, notice_id]
    request.session[NOTICES_OVERRIDDEN_KEY] = found[-NOTICES_OVERRIDDEN_MAX:]
