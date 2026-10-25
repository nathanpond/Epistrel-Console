"""Message feedback (#83): which rendered items carry a "Rate" control, and what the widget shows.

- :func:`rateable`: the gate. An item gets a control when it is a ``message_rendered`` item the
  Engine generated (``origin == "generated"``) and not an audit record. Player input, ingested
  items (the consumer's own narration, which the Engine answers 404 for), audit records and
  summaries get none; a scenario's opening narration is a generated message and gets one.
- :func:`first_parts`: the Engine splits one ``message_rendered`` event into channel parts that
  share its ``message_id``; the control goes on the first such part among the items rendered
  on this page (a per-render seen set), so a filtered or paged-out first part moves it to the
  first visible one.
- :class:`FeedbackWidget`: one ``_feedback.html`` in its ``collapsed`` state (the bare "Rate"
  link, plus the result line after a save), ``open`` (the form, history and context) or
  ``unavailable`` ("Can't be rated …"). Its wrapper id is ``feedback-<view>-<message_id>``.
- The pre-fill is signed (:func:`sign_prefill`/:func:`unsign_prefill`) so a save compares the
  submission with what the form showed, and re-renders an invalid form, with zero Engine calls.
- :func:`same_value` compares a submission with the pre-filled rating the Engine's way: tags
  trimmed, lower-cased, compared as a set; text CRLF→LF, trimmed, empty = none.
"""

import dataclasses
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from django.core import signing
from django.urls import reverse

from console.engine.models import HistoryItem, MessageFeedback
from console.engine_errors import ErrorPanel
from console.history import AUDIT_CHANNEL, REPLY_KIND

View = Literal["play", "transcript"]
VIEWS: tuple[View, ...] = ("play", "transcript")
State = Literal["collapsed", "open", "unavailable"]

PREFILL_SALT = "console.feedback.prefill"
PREFILL_FIELD = "prefill"
MAX_AGE_S = 3600
DEFAULT_TEXT_MAX = 2000  # the Engine's default ``feedback_text_max_chars``

UNAVAILABLE_TEXT = "Can't be rated (the Engine doesn't know this message)"
EXPIRED_TEXT = "This form expired — open it again"
TAGS_NOTE = "Couldn't read feedback tags; you can still rate"
RETRACTED_TEXT = "Rating retracted"
NO_CHANGE_TEXT = "No change"


# --- the gate -----------------------------------------------------------------------------------


def rateable(item: HistoryItem) -> bool:
    """Whether ``item`` is a part of a generated message (the Engine's rateable kind)."""
    return item.kind == REPLY_KIND and item.origin == "generated" and item.channel != AUDIT_CHANNEL


def first_parts(items: Iterable[HistoryItem]) -> list[bool]:
    """For each item, whether it carries its message's control: rateable and the first part
    of its ``message_id`` among ``items``."""
    seen: set[str] = set()
    marks = []
    for item in items:
        mark = rateable(item) and item.message_id not in seen
        if mark:
            seen.add(item.message_id)
        marks.append(mark)
    return marks


# --- normalising (the Engine's way) -------------------------------------------------------------


def norm_tag(tag: str) -> str:
    return tag.strip().lower()


def norm_text(text: str | None) -> str | None:
    """CRLF (and lone CR) → LF, trimmed; an empty text is none."""
    if text is None:
        return None
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return cleaned or None


def same_value(
    rating: int,
    tags: Sequence[str],
    text: str | None,
    *,
    current_rating: int | None,
    current_tags: Sequence[str],
    current_text: str | None,
) -> bool:
    """Whether a submission equals the pre-filled rating (never true when there is none)."""
    if current_rating is None:
        return False
    return (
        rating == current_rating
        and {norm_tag(t) for t in tags} == {norm_tag(t) for t in current_tags}
        and norm_text(text) == norm_text(current_text)
    )


# --- the signed pre-fill ------------------------------------------------------------------------


@dataclass(frozen=True)
class Prefill:
    """What the open form was rendered from: the message, the principal it was read as, the
    rating then (``feedback``, whole, so a kept form re-renders its history), and the tag list
    and text cap it offered (``tags_read``: whether the settings read worked)."""

    story_id: str
    message_id: str
    principal: str
    feedback: MessageFeedback
    configured: tuple[str, ...]
    cap: int
    tags_read: bool


def sign_prefill(prefill: Prefill) -> str:
    """``signing.dumps({story_id, message_id, principal, rating, tags, text, updated_at, …})``."""
    feedback = prefill.feedback
    raw = {
        "story_id": prefill.story_id,
        "message_id": prefill.message_id,
        "principal": prefill.principal,
        "rating": feedback.rating,
        "tags": feedback.tags,
        "text": feedback.text,
        "updated_at": feedback.updated_at,
        "feedback": dataclasses.asdict(feedback),
        "configured": list(prefill.configured),
        "cap": prefill.cap,
        "tags_read": prefill.tags_read,
    }
    return signing.dumps(raw, salt=PREFILL_SALT, compress=True)


def unsign_prefill(token: str, *, story_id: str, message_id: str, principal: str) -> Prefill | None:
    """The pre-fill ``token`` carries, or ``None`` when it is expired, tampered with, or bound
    to another story, message or principal."""
    try:
        raw = signing.loads(token, salt=PREFILL_SALT, max_age=MAX_AGE_S)
    except signing.BadSignature:
        return None
    if not isinstance(raw, dict) or (
        raw.get("story_id"),
        raw.get("message_id"),
        raw.get("principal"),
    ) != (story_id, message_id, principal):
        return None
    feedback = MessageFeedback.from_json(raw.get("feedback"))
    configured = raw.get("configured")
    cap = raw.get("cap")
    if feedback is None or not isinstance(configured, list) or not isinstance(cap, int):
        return None
    return Prefill(
        story_id,
        message_id,
        principal,
        feedback,
        tuple(str(t) for t in configured),
        cap,
        bool(raw.get("tags_read")),
    )


# --- the widget ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextLine:
    label: str
    value: str  # "—" when null
    url: str = ""
    code: bool = False


@dataclass(frozen=True)
class FeedbackWidget:
    """One message's widget. ``state`` picks what ``_feedback.html`` renders; ``result`` is
    the line a collapsed widget shows after a save ("Rated 2"), ``badge`` the rating it shows
    with it; ``notice`` the line above an open form ("Rating retracted")."""

    story_id: str
    message_id: str
    view: View
    next_url: str
    state: State = "collapsed"
    result: str = ""
    badge: int | None = None
    notice: str = ""
    feedback: MessageFeedback | None = None
    form: Any = None  # console.forms.feedback.FeedbackForm
    token: str = ""
    cap: int = DEFAULT_TEXT_MAX
    tags_note: str = ""
    error: ErrorPanel | None = None
    context: tuple[ContextLine, ...] = ()
    models: tuple[str, ...] = ()

    @property
    def dom_id(self) -> str:
        return f"feedback-{self.view}-{self.message_id}"

    def _url(self, name: str, **extra: str) -> str:
        url = reverse(name, args=[self.story_id, self.message_id])
        return f"{url}?{urlencode({'view': self.view, 'next': self.next_url, **extra})}"

    @property
    def open_url(self) -> str:
        return self._url("stories:feedback")

    @property
    def close_url(self) -> str:
        return self._url("stories:feedback", close="1")

    @property
    def save_url(self) -> str:
        return self._url("stories:feedback")

    @property
    def retract_url(self) -> str:
        return self._url("stories:feedback-retract")

    @property
    def rated(self) -> bool:
        return self.feedback is not None and self.feedback.rating is not None


def collapsed(story_id: str, view: View, next_url: str) -> Callable[[str], FeedbackWidget]:
    """A factory of collapsed widgets for one rendered page: message id → widget."""

    def widget(message_id: str) -> FeedbackWidget:
        return FeedbackWidget(story_id, message_id, view, next_url)

    return widget
