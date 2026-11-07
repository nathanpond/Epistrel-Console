"""The Play page's Direct tab: its view and the wording of a direction's answer (#300;
FR-DIRECT-1,2,6, FR-LITE-8; E2, E3, E5, E21, E23).

An author directs the story as its author, not as a character. ``apply_directive`` is
omniscient-only (E2), so the tab has no viewpoint of its own and says so:
:data:`DIRECT_VIEWPOINT_LINE`.

:func:`direct_outcome` reads the Engine's ``DirectiveBody`` into :class:`DirectOutcome`, shown above
the tab's fresh form:

- **Read as** the kind label of ``classification`` (Lite's labels on a Lite story), else the
  humanised value with the raw one in ``title``;
- ``steer_queued`` → :data:`STEER_QUEUED_TEXT` (Lite: :data:`NUDGE_QUEUED_TEXT`), with
  ``: “<steer_text>”`` when there is one;
- ``applied`` → :data:`APPLIED_TEXT` (``position`` as given, D7; "—" when null), one C11 line per
  non-empty ``applied`` list (the outbox ids linked through D11's ``outbox_url``), a Lite
  ``author_fact`` with text as :data:`AUTHOR_FACT_TEXT`; every id under More;
- ``routed_to_rewrite`` → :data:`ROUTED_TEXT`, "Open the rewrite session" (E6) and the session
  read-only through E3's partial (``actions=()``, no answers form); with no session, the text alone
  and the raw answer under More;
- any other ``disposition`` is humanised (raw in ``title``), the raw answer under More, and never
  read as applied.

Hints: ``use_custom_instructions`` → :data:`STANDING_HINT`, linked to ``stories:settings``
(plain while unregistered, D19); any other hint humanised with the raw value in ``title``. More
always holds ``directive_id`` and the idempotency key. A replayed key (the #44 row) ends the
headline " (replayed)". The tab gives no flash for any outcome.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from django.urls import NoReverseMatch, reverse

from console.effects import outbox_url
from console.engine.models import Directive, DirectiveApplied
from console.engine_errors import ErrorPanel
from console.forms.directives import DirectForm, kind_choices
from console.raw import EMPTY, more_json, number_text, plural
from console.rewrites import SessionView, rewrite_session_url, session_view
from console.wording import humanised
from console.world import WorldRefusal

DIRECT_VIEWPOINT_LINE = "Directs the whole story, as its author — not as any character"
READ_AS_TEXT = "Read as: {kind}"
STEER_QUEUED_TEXT = "Queued for the next reply only"
NUDGE_QUEUED_TEXT = "Nudge queued for the next reply only"
QUOTED_SUFFIX = ": “{text}”"
APPLIED_TEXT = "Applied at {position} · story revision now {revision}"
AUTHOR_FACT_TEXT = "Lasting author fact recorded: “{text}”"
ROUTED_TEXT = (
    "That changes the story's past, so it opened a rewrite session — nothing is written until you "
    "apply it"
)
OPEN_SESSION_TEXT = "Open the rewrite session"
STANDING_HINT = (
    "This reads as standing guidance — a direction steers only the next reply; set it as the "
    "story's custom instructions to keep it"
)
CLOCK_TEXT = "Clock advanced"
REPLAYED_SUFFIX = " (replayed)"
AMBIGUOUS_SUFFIX = " (could be: {candidates})"

STEER_QUEUED, APPLIED, ROUTED = "steer_queued", "applied", "routed_to_rewrite"
USE_CUSTOM_INSTRUCTIONS = "use_custom_instructions"
LITE = "lite"
# The ``applied`` lists in the order their lines show: (field, singular, plural). ``clock`` is the
# line alone (its entries under More).
APPLIED_LISTS = (
    ("facts", "fact", "facts"),
    ("state_changes", "State change", "State changes"),
    ("clock", "", ""),
    ("beliefs", "belief", "beliefs"),
    ("scheduled_events", "scheduled event", "scheduled events"),
    ("outbox_ids", "external effect", "external effects"),
)


@dataclass(frozen=True)
class OutcomeLine:
    """One line of what an applied direction changed; ``links`` are (id, url-or-None) pairs shown
    after the text (the outbox ids)."""

    text: str
    links: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class HintLine:
    text: str
    url: str | None = None
    title: str = ""


@dataclass(frozen=True)
class DirectOutcome:
    """What the tab shows about the Engine's answer to a direction."""

    read_as_text: str
    read_as_title: str
    headline: str
    headline_title: str = ""
    lines: tuple[OutcomeLine, ...] = ()
    hint_lines: tuple[HintLine, ...] = ()
    session_view: SessionView | None = None
    session_url: str | None = None
    more: str = ""
    applied: bool = False

    open_session_text: ClassVar[str] = OPEN_SESSION_TEXT


@dataclass(frozen=True)
class DirectView:
    """The Direct tab as rendered: a fresh key per render unless D8 keeps the posted one."""

    story_id: str
    form: DirectForm
    snapshot: str
    viewpoint: str  # the Turn tab's viewpoint value, display-only (never sent)
    transcript_player_view: str
    idempotency_key: str
    timeout_s: int  # the browser's wait for the POST: turn_timeout(None), rounded up
    outcome: DirectOutcome | None = None
    refusal: WorldRefusal | None = None
    refusal_link: str = ""
    panel: ErrorPanel | None = None
    disabled: bool = False
    note: str = ""

    viewpoint_line: ClassVar[str] = DIRECT_VIEWPOINT_LINE


def settings_url(story_id: str) -> str | None:
    """``stories:settings``; ``None`` while unregistered (D19)."""
    try:
        return reverse("stories:settings", args=[story_id])
    except NoReverseMatch:
        return None


def _labels(pipeline: str | None) -> dict[str, str]:
    return {value: label for value, label in kind_choices(pipeline) if value}


def _hint(story_id: str, hint: str) -> HintLine:
    if hint == USE_CUSTOM_INSTRUCTIONS:
        return HintLine(STANDING_HINT, settings_url(story_id))
    return HintLine(humanised(hint), title=hint)


def _quoted(text: str, steer_text: str | None) -> str:
    return text + QUOTED_SUFFIX.format(text=steer_text) if steer_text else text


def _applied_lines(applied: DirectiveApplied, story_id: str) -> list[OutcomeLine]:
    lines: list[OutcomeLine] = []
    for name, singular, plural_form in APPLIED_LISTS:
        ids = [i for i in getattr(applied, name) if isinstance(i, str)]
        if not ids:
            continue
        if name == "clock":
            lines.append(OutcomeLine(CLOCK_TEXT))
        elif name == "outbox_ids":
            links = tuple((i, outbox_url(story_id, i)) for i in ids)
            lines.append(OutcomeLine(plural(len(ids), singular, plural_form), links))
        else:
            lines.append(OutcomeLine(plural(len(ids), singular, plural_form)))
    fact = applied.author_fact
    text = fact.get("text") if isinstance(fact, Mapping) else None
    if isinstance(text, str) and text:
        lines.append(OutcomeLine(AUTHOR_FACT_TEXT.format(text=text)))
    return lines


def _applied_json(applied: DirectiveApplied) -> dict[str, Any]:
    found: dict[str, Any] = {
        name: list(getattr(applied, name)) for name, _, _ in APPLIED_LISTS if getattr(applied, name)
    }
    if applied.author_fact is not None:
        found["author_fact"] = dict(applied.author_fact)
    return found


def direct_outcome(
    answer: Directive,
    story_id: str,
    pipeline: str | None,
    *,
    key: str,
    names: Mapping[str, str] | None = None,
    raw: Any = None,
    replayed: bool = False,
) -> DirectOutcome:
    """The tab's outcome for ``answer``. ``names`` are C4's cast names (the routed session's
    labels), ``raw`` the answer as JSON (More, for an unknown disposition or a routed answer with
    no session), ``replayed`` the #44 row's flag."""
    lite = pipeline == LITE
    classification = answer.classification
    label = _labels(pipeline).get(classification)
    read_as = READ_AS_TEXT.format(kind=label or humanised(classification or EMPTY))
    more: dict[str, Any] = {
        "directive_id": answer.directive_id,
        "idempotency_key": answer.idempotency_key or key,
    }
    hints = tuple(_hint(story_id, h) for h in answer.hints if isinstance(h, str) and h)
    disposition = answer.disposition
    headline, headline_title = "", ""
    lines: list[OutcomeLine] = []
    view: SessionView | None = None
    url: str | None = None
    applied = False
    if disposition == STEER_QUEUED:
        headline = _quoted(NUDGE_QUEUED_TEXT if lite else STEER_QUEUED_TEXT, answer.steer_text)
    elif disposition == APPLIED:
        applied = True
        position = answer.position or EMPTY
        revision = number_text(answer.timeline_revision)
        headline = APPLIED_TEXT.format(position=position, revision=revision)
        if answer.applied is not None:
            lines = _applied_lines(answer.applied, story_id)
            more["applied"] = _applied_json(answer.applied)
    elif disposition == ROUTED:
        headline = ROUTED_TEXT
        session = answer.rewrite_session
        if session is not None:
            view = replace(session_view(session, story_id, names or {}), actions=())
            url = rewrite_session_url(story_id, session.id)
            more["rewrite_session_id"] = session.id
        else:
            more["answer"] = raw
    else:
        headline, headline_title = humanised(disposition or EMPTY), disposition
        more["answer"] = raw
    if replayed:
        headline += REPLAYED_SUFFIX
    return DirectOutcome(
        read_as,
        "" if label else classification,
        headline,
        headline_title,
        tuple(lines),
        hints,
        view,
        url,
        more_json(more),
        applied,
    )


def ambiguous_suffix(details: object) -> str:
    """ " (could be: a, b)" from a ``directive_ambiguous`` refusal's string ``candidates`` (others
    skipped); "" with none."""
    found = details.get("candidates") if isinstance(details, Mapping) else None
    if not isinstance(found, list):
        return ""
    names = [c for c in found if isinstance(c, str) and c]
    return AMBIGUOUS_SUFFIX.format(candidates=", ".join(names)) if names else ""
