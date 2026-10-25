"""How a page names the catalog characters it mentions by id (#68's companion marker; #73's
role characters): one ``GET /catalog/characters/{id}?projection=card`` per distinct id, on the
shared fan-out pool (``console.engine.pool``; #64 convention 12). A failed read is labelled by
the id, with the error."""

from dataclasses import dataclass

from django.urls import reverse

from console.engine.choices import error_text
from console.engine.client import EngineClient, EngineClientError
from console.engine.pool import fan_out
from console.entities import label

ARCHIVED = "archived"


@dataclass(frozen=True)
class CharacterLabel:
    """One character as a page names it: its card's name (or the id, with ``error``, when the
    read failed), its detail URL and whether it is archived."""

    character_id: str
    text: str
    url: str
    error: str = ""
    archived: bool = False


def character_labels(client: EngineClient, ids: list[str]) -> dict[str, CharacterLabel]:
    distinct = list(dict.fromkeys(i for i in ids if i))
    results = fan_out(
        lambda character_id: client.get_catalog_character_card(character_id), distinct
    )
    labels: dict[str, CharacterLabel] = {}
    for character_id in distinct:
        url = reverse("catalog_characters:detail", args=[character_id])
        result = results[character_id]
        if isinstance(result, EngineClientError):
            labels[character_id] = CharacterLabel(
                character_id, character_id, url, error_text(result)
            )
        else:
            labels[character_id] = CharacterLabel(
                character_id,
                label(result.name, character_id),
                url,
                archived=result.status == ARCHIVED,
            )
    return labels
