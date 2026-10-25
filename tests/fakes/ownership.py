"""Fake-Engine answers for #79's owner and relations panel.

Every owned entity's page reads ``GET /{type}/{id}/relations`` for its panel, so each test
fixture that renders one of those pages calls :func:`answer_relations`: every type's relations
read is answered with no relations, for whichever id the page asks about. A test that needs
something else replaces one route with ``FakeEngine.respond`` (which replaces a side effect).
"""

from collections.abc import Sequence
from typing import Any
from urllib.parse import unquote

import httpx

from console.engine.client import OWNERSHIP_ID_PARAMS
from console.entities import ENTITY_TYPES, REGISTRY, EntityType
from tests.fakes.engine import FakeEngine

DEFAULT_OWNER = "p-owner"


def relations_path(entity_type: EntityType) -> str:
    """The relations route's schema template (``/catalog/characters/{character_id}/relations``)."""
    return f"{REGISTRY[entity_type].engine_root}/{{{OWNERSHIP_ID_PARAMS[entity_type]}}}/relations"


def relations_body(
    entity_type: EntityType,
    entity_id: str,
    *,
    owner: str = DEFAULT_OWNER,
    items: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """A ``RelationsBody``."""
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "owner_principal": owner,
        "items": list(items),
    }


def answer_relations(fake: FakeEngine, *, owner: str = DEFAULT_OWNER) -> None:
    """Answer every type's relations read with ``owner`` and no relations."""
    for entity_type in ENTITY_TYPES:

        def handler(request: httpx.Request, kind: EntityType = entity_type) -> httpx.Response:
            segments = request.url.raw_path.decode("ascii").split("?", 1)[0].split("/")
            entity_id = unquote(segments[-2])
            return httpx.Response(200, json=relations_body(kind, entity_id, owner=owner))

        fake.side_effect("GET", relations_path(entity_type), handler)


def answer_one_relation(
    fake: FakeEngine,
    entity_type: EntityType,
    entity_id: str,
    *,
    principal: str = "bob",
    relation: str = "editor",
) -> None:
    """Answer the entity's relations read with ``alice`` as owner and one relation."""
    item = {
        "principal": principal,
        "relation": relation,
        "created_at": "2026-10-20T09:00:00Z",
        "created_by": "console:nate",
    }
    body = relations_body(entity_type, entity_id, owner="alice", items=[item])
    fake.respond("GET", relations_path(entity_type), 200, json=body)


def shows_panel(html: str, entity_type: EntityType, entity_id: str) -> bool:
    """Whether a page carries the writable panel with :func:`answer_one_relation`'s row."""
    remove = f"/ownership/{entity_type}/{entity_id}/relations/remove/?relation=editor&amp;"
    return (
        'id="ownership"' in html
        and "<code>editor</code>" in html
        and remove in html
        and f'action="/ownership/{entity_type}/{entity_id}/owner/"' in html
        and f'action="/ownership/{entity_type}/{entity_id}/relations/add/"' in html
    )
