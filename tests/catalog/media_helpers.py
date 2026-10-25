"""Helpers for the media-attachment tests (#71): the per-asset lookup, the picker's list and the
posted state of an editor's media sections."""

import copy
import re
from collections.abc import Mapping
from typing import Any

import httpx
from django.http import QueryDict

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

MEDIA_ITEM = "/media/{media_id}"
MEDIA_LIST = "/media"
PHT1 = "01JC000000000000000000PHT1"
PHT2 = "01JC000000000000000000PHT2"
VDE1 = "01JC000000000000000000VDE1"
PST1 = "01JC000000000000000000PST1"
GNE1 = "01JC000000000000000000GNE1"


def media(name: str, **changes: Any) -> dict[str, Any]:
    """``tests/fixtures/engine/media/<name>.json``, with ``changes``."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture(f"media/{name}"))
    body.update(changes)
    return body


def asset(media_id: str, **changes: Any) -> dict[str, Any]:
    """A live asset with ``media_id`` (the live fixture's metadata otherwise)."""
    return media("get_media_live", id=media_id, **changes)


def answer_media(
    engine: FakeEngine,
    assets: Mapping[str, dict[str, Any]] | None = None,
    *,
    missing: frozenset[str] = frozenset(),
) -> None:
    """``GET /media/{id}``: ``assets[id]`` when given, a 404 for an id in ``missing``, else a
    live image with the id asked."""
    known = dict(assets or {})

    def answer(request: httpx.Request) -> httpx.Response:
        media_id = request.url.path.rsplit("/", 1)[1]
        if media_id in missing:
            return httpx.Response(404, json=media("get_media_404"))
        return httpx.Response(200, json=known.get(media_id) or asset(media_id))

    engine.side_effect("GET", MEDIA_ITEM, answer)


def media_reads(engine: FakeEngine) -> list[str]:
    """The ids ``GET /media/{id}`` was asked for, in order."""
    return [
        r.url.path.rsplit("/", 1)[1]
        for r in engine.requests
        if r.method == "GET" and re.fullmatch(r"/media/[^/]+", r.url.path)
    ]


def sections_posted(response: Any) -> QueryDict:
    """The media sections' bound state as the page posts it back."""
    data: QueryDict = response.context["media_sections"].data.copy()
    return data


def set_rows(
    data: QueryDict, prefix: str, rows: list[dict[str, str]], *, blank: bool = True
) -> QueryDict:
    """``data`` with the formset ``prefix`` replaced by ``rows`` (and one blank row)."""
    out = data.copy()
    for key in [k for k in out if k.startswith(f"{prefix}-")]:
        del out[key]
    shown = [*rows, {}] if blank else rows
    for index, row in enumerate(shown):
        for name, value in row.items():
            out[f"{prefix}-{index}-{name}"] = value
        out[f"{prefix}-{index}-ORDER"] = str(index + 1)
    out[f"{prefix}-TOTAL_FORMS"] = str(len(shown))
    out[f"{prefix}-INITIAL_FORMS"] = "0"
    out[f"{prefix}-MIN_NUM_FORMS"] = "0"
    out[f"{prefix}-MAX_NUM_FORMS"] = "21"
    return out
