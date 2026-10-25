"""The play view's cast panel (#48, #72): each cast member with "Promote to catalog" (non-player
characters only) and its "Promoted → <catalog id>" links, and "Add character (n/20)".

The promotion links come from one extra ``GET /stories/{id}?viewpoint=omniscient`` (only an
omniscient read shows ``promoted_to``). When it fails the panel still offers Promote on every
non-player character, without links, and says "Promotion links unavailable: <code>". "Add
character" is disabled on an archived story only; a paused story and a full cast are left to the
Engine. The count includes player characters.
"""

from dataclasses import dataclass

from django.urls import reverse

from console import erasure
from console.cast import OMNISCIENT
from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError
from console.engine.models import Story

CAST_MAX = 20  # the Engine's ``CAST_MAX``
ADD_ARCHIVED = "Archived — unarchive to add characters"
LINKS_UNAVAILABLE = "Promotion links unavailable: {code}"


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

    @property
    def promote_label(self) -> str:
        return "Promote again" if self.promoted else "Promote to catalog"


@dataclass(frozen=True)
class CastPanel:
    rows: tuple[CastRow, ...]
    add_url: str
    add_disabled: str  # the disabled button's title; "" when enabled
    links_note: str = ""

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


def cast_panel(client: EngineClient, story: Story) -> CastPanel:
    """The panel for ``story`` (the play view's read), with one omniscient read for the
    promotion links."""
    links: dict[str, list[str]] = {}
    note = ""
    try:
        links = promoted_links(client.get_story(story.id, viewpoint=OMNISCIENT))
    except EngineClientError as exc:
        note = LINKS_UNAVAILABLE.format(code=error_text(exc))
    rows = tuple(
        CastRow(
            character.id,
            character.name,
            character.is_player,
            ""
            if character.is_player
            else reverse("stories:character-promote", args=[story.id, character.id]),
            tuple(
                Promoted(catalog_id, reverse("catalog_characters:detail", args=[catalog_id]))
                for catalog_id in links.get(character.id, ())
            ),
            erasure.new_url("character", character.id, scope="story", story=story.id) or "",
        )
        for character in story.cast
    )
    return CastPanel(
        rows,
        reverse("stories:character-new", args=[story.id]),
        ADD_ARCHIVED if story.status == "archived" else "",
        note,
    )
