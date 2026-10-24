"""Helpers for the Engine panel tests (#50)."""

import copy
import json
import re
from typing import Any

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

SETTINGS = "/admin/settings"
RATINGS = "/admin/content-ratings"
CHANGES = "/admin/config-changes"
REBUILD = "/admin/stories/{story_id}/rebuild"
STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2V"
HELD_STORY = "01JB2Z8Y5Q3W6K9M1N4P7R0T2W"


def settings_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(engine_fixture("get_deployment_settings"))
    body.update(changes)
    return body


def answer_settings(engine: FakeEngine, **changes: Any) -> dict[str, Any]:
    """``GET /admin/settings`` (the fixture, patched) and the ratings (all four, none retired)."""
    body = settings_body(**changes)
    engine.respond("GET", SETTINGS, 200, json=body)
    engine.respond("GET", RATINGS, 200, json=engine_fixture("list_content_ratings_all"))
    return body


def calls(fake: FakeEngine, method: str | None = None, path: str | None = None) -> list[Any]:
    return [
        r
        for r in fake.requests
        if (method is None or r.method == method) and (path is None or r.url.path == path)
    ]


def body(request: Any) -> Any:
    return json.loads(request.content)


def error(code: str, message: str = "refused", **details: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def form_data(html: str) -> dict[str, str]:
    """The settings form's values as the browser would post them unchanged."""
    import html as markup

    form = re.search(r'<form method="post" action="/engine/settings/".*?</form>', html, re.S)
    assert form is not None
    data: dict[str, str] = {}
    for match in re.finditer(r"<input([^>]*)>", form.group(0)):
        attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', match.group(1)))
        if "name" not in attrs:
            continue
        if attrs.get("type") in ("checkbox", "radio"):
            if re.search(r"\schecked\b", match.group(1)):
                data[attrs["name"]] = attrs.get("value", "on")
        else:
            data[attrs["name"]] = attrs.get("value", "")
    for match in re.finditer(
        r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', form.group(0), re.S
    ):
        data[match.group(1)] = match.group(2).removeprefix("\n")
    for match in re.finditer(
        r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form.group(0), re.S
    ):
        chosen = re.search(r'<option value="([^"]*)"[^>]*selected', match.group(2))
        data[match.group(1)] = chosen.group(1) if chosen else ""
    return {k: markup.unescape(v) for k, v in data.items()}
