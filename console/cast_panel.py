"""The play view's cast panel (#48, #72): each cast member with "Promote to catalog" (non-player
characters only) and its "Promoted → <catalog id>" links, and "Add character (n/20)".

The promotion links come from one extra ``GET /stories/{id}?viewpoint=omniscient`` (only an
omniscient read shows ``promoted_to``). When it fails the panel still offers Promote on every
non-player character, without links, and says "Weights and promotion links unavailable: <code>".
"Add character" is disabled on an archived story only; a paused story and a full cast are left
to the Engine. The count includes player characters.

#61 (C8): each non-player row shows its participation role and weight (the same omniscient read;
a missing weight is "Major", the Engine's default) and "minor since <position>" for a minor with
``modeled_at``, with an inline role form and a "Make minor/major" button. When the omniscient read
fails, the role comes from the play page's own read and the weight is unknown ("—", no button).
Under any ``multi_character.renders`` but ``per_character`` a note says the roles are inert.
"""

from dataclasses import asdict, dataclass
from typing import Any

from django.core import signing
from django.urls import reverse

from console import erasure
from console.cast import OMNISCIENT
from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError
from console.engine.models import ModeledAt, Participation, Story
from console.forms.story_character import ParticipationForm
from console.ids import new_ulid
from console.inspect import PER_CHARACTER
from console.participation import (
    DEFAULT_ROLE,
    DEFAULT_WEIGHT,
    MAJOR,
    MINOR,
    ROLES,
    WEIGHT_LABELS,
    WEIGHT_TITLES,
    role_text,
    weight_label,
)
from console.positions import knowledge_position_text, knowledge_position_url

CAST_MAX = 20  # the Engine's ``CAST_MAX``
ADD_ARCHIVED = "Archived — unarchive to add characters"
LINKS_UNAVAILABLE = "Weights and promotion links unavailable: {code}"
CONTROLS_ARCHIVED = "Archived — unarchive to change roles and weights"
SHOWN_SALT = "console.cast_panel.participation"
ROW_AUTO_ID = "cast-{cid}-%s"


@dataclass(frozen=True)
class Shown:
    """A cast row as the page showed it, signed on its role and weight forms (no ``max_age``):
    the role form's no-op check and every flash's name come from it. ``role`` is ``None`` for
    "—"."""

    story_id: str
    cid: str
    name: str
    role: str | None
    fixed: bool

    def signed(self) -> str:
        return signing.dumps(asdict(self), salt=SHOWN_SALT)

    @classmethod
    def unsigned(cls, value: str | None, story_id: str, cid: str) -> Shown | None:
        """The signed row, or ``None`` when the signature is missing or bad or it names another
        story or character."""
        try:
            raw = signing.loads(value or "", salt=SHOWN_SALT)
            found = cls(**raw)
        except signing.BadSignature, TypeError:
            return None
        if (found.story_id, found.cid) != (story_id, cid):
            return None
        if not isinstance(found.name, str) or not isinstance(found.fixed, bool):
            return None
        return found

    def same_role(self, role: str, fixed: bool) -> bool:
        """The posted role is what the page showed (a shown "—" or unknown role never is)."""
        return self.role in ROLES and (self.role, self.fixed) == (role, fixed)


@dataclass(frozen=True)
class MinorSince:
    text: str
    url: str


@dataclass(frozen=True)
class Promoted:
    catalog_id: str
    url: str


@dataclass(frozen=True)
class CastRow:
    id: str
    name: str
    is_player: bool
    promote_url: str  # "" for a player character
    promoted: tuple[Promoted, ...] = ()
    erase_url: str = ""  # #86's form pre-filled for this character ("" while it does not exist)
    # #61: the stored role ("—" when None), the weight (None when unread), "minor since …"
    role: str | None = None
    fixed: bool = False
    weight: str | None = None
    minor_since: MinorSince | None = None
    shown: str = ""  # the signed Shown both forms carry ("" for a player)
    role_form: Any = None  # a ParticipationForm with this row's ids, None for a player
    role_url: str = ""
    weight_url: str = ""
    role_key: str = ""
    weight_key: str = ""
    archived: bool = False

    @property
    def promote_label(self) -> str:
        return "Promote again" if self.promoted else "Promote to catalog"

    @property
    def role_text(self) -> str:
        return role_text(self.role, self.fixed)

    @property
    def weight_label(self) -> str:
        return weight_label(self.weight)

    @property
    def weight_title(self) -> str:
        return WEIGHT_TITLES.get(self.weight or "", "")

    @property
    def target_weight(self) -> str:
        """The weight the button offers ("" while the weight is unknown: no button)."""
        if self.weight not in WEIGHT_LABELS:
            return ""
        return MAJOR if self.weight == MINOR else MINOR

    @property
    def weight_button(self) -> str:
        return f"Make {self.target_weight}" if self.target_weight else ""

    @property
    def weight_button_title(self) -> str:
        return CONTROLS_ARCHIVED if self.archived else WEIGHT_TITLES.get(self.target_weight, "")

    @property
    def controls_title(self) -> str:
        return CONTROLS_ARCHIVED if self.archived else ""


@dataclass(frozen=True)
class CastPanel:
    rows: tuple[CastRow, ...]
    add_url: str
    add_disabled: str  # the disabled button's title; "" when enabled
    links_note: str = ""
    roles_note: bool = False  # the cast has a non-player and renders isn't per_character
    renders: str | None = None  # the effective ``multi_character.renders`` (None: unread)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def cap(self) -> int:
        return CAST_MAX


def promoted_links(story: Story) -> dict[str, list[str]]:
    """Each character's ``promoted_to`` (oldest first, as the Engine lists them) from an
    omniscient read; a character without the field has none."""
    found: dict[str, list[str]] = {}
    for item in story.characters:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        links = item.get("promoted_to")
        if isinstance(links, list):
            found[item["id"]] = [link for link in links if isinstance(link, str)]
    return found


@dataclass(frozen=True)
class Omniscient:
    """What the omniscient read says of one character (#61)."""

    participation: Participation | None
    weight: str
    modeled_at: ModeledAt | None


def omniscient_rows(story: Story) -> dict[str, Omniscient]:
    """Each character's participation, weight (missing: ``major``) and ``modeled_at`` from an
    omniscient read, each part parsed on its own."""
    found: dict[str, Omniscient] = {}
    for item in story.characters:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        weight = item.get("weight")
        found[item["id"]] = Omniscient(
            Participation.from_json(item.get("participation")),
            weight if isinstance(weight, str) and weight else DEFAULT_WEIGHT,
            ModeledAt.from_json(item.get("modeled_at")),
        )
    return found


def _role_form(cid: str, role: str | None, fixed: bool, shown: str, archived: bool) -> Any:
    form = ParticipationForm(
        initial={"role": role if role in ROLES else DEFAULT_ROLE, "fixed": fixed, "shown": shown},
        auto_id=ROW_AUTO_ID.format(cid=cid),
    )
    if archived:
        for name in ("role", "fixed"):
            form.fields[name].disabled = True
    return form


def cast_panel(client: EngineClient, story: Story, *, renders: object | None) -> CastPanel:
    """The panel for ``story`` (the play view's read), with one omniscient read for the
    promotion links, roles and weights; ``renders`` is the effective
    ``multi_character.renders`` (``None`` when unread)."""
    links: dict[str, list[str]] = {}
    known: dict[str, Omniscient] | None = None
    note = ""
    try:
        omniscient = client.get_story(story.id, viewpoint=OMNISCIENT)
        links = promoted_links(omniscient)
        known = omniscient_rows(omniscient)
    except EngineClientError as exc:
        note = LINKS_UNAVAILABLE.format(code=error_text(exc))
    archived = story.status == "archived"
    rows = []
    for character in story.cast:
        promoted = tuple(
            Promoted(catalog_id, reverse("catalog_characters:detail", args=[catalog_id]))
            for catalog_id in links.get(character.id, ())
        )
        erase_url = erasure.new_url("character", character.id, scope="story", story=story.id)
        if character.is_player:
            rows.append(CastRow(character.id, character.name, True, "", promoted, erase_url or ""))
            continue
        read = known.get(character.id) if known is not None else None
        participation = read.participation if read is not None else character.participation
        weight = None if known is None else (read.weight if read is not None else DEFAULT_WEIGHT)
        role = participation.role if participation is not None else None
        fixed = participation.fixed if participation is not None else False
        modeled_at = read.modeled_at if read is not None else None
        since = (
            MinorSince(
                knowledge_position_text(modeled_at),
                knowledge_position_url(story.id, modeled_at),
            )
            if weight == MINOR and modeled_at is not None
            else None
        )
        shown = Shown(story.id, character.id, character.name, role, fixed).signed()
        rows.append(
            CastRow(
                character.id,
                character.name,
                False,
                reverse("stories:character-promote", args=[story.id, character.id]),
                promoted,
                erase_url or "",
                role=role,
                fixed=fixed,
                weight=weight,
                minor_since=since,
                shown=shown,
                role_form=_role_form(character.id, role, fixed, shown, archived),
                role_url=reverse("stories:character-participation", args=[story.id, character.id]),
                weight_url=reverse("stories:character-weight", args=[story.id, character.id]),
                role_key=new_ulid(),
                weight_key=new_ulid(),
                archived=archived,
            )
        )
    has_cast = any(not row.is_player for row in rows)
    return CastPanel(
        tuple(rows),
        reverse("stories:character-new", args=[story.id]),
        ADD_ARCHIVED if archived else "",
        note,
        roles_note=has_cast and renders != PER_CHARACTER,
        renders=None if renders is None else str(renders),
    )
