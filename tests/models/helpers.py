"""Helpers for the Models page tests (#46)."""

import copy
import json
from typing import Any

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

LIST = "/admin/model-config"
ITEM = "/admin/model-config/{purpose}"
KEY = "/admin/model-config/{purpose}/api-key"
IDEMPOTENCY_KEY = "01JB2Z8Y5Q3W6K9M1N4P7R0KEY"


def openapi(version: str | None = "m3") -> dict[str, Any]:
    info: dict[str, Any] = {"title": "Epistrel Engine"}
    if version is not None:
        info["version"] = version
    return {"openapi": "3.1.0", "info": info, "paths": {}}


def answer_list(engine: FakeEngine, version: str | None = "m3", **changes: Any) -> dict[str, Any]:
    """``GET /admin/model-config`` (the recorded fixture, purposes patched by ``changes``) and
    ``/openapi.json`` with ``version``."""
    body: dict[str, Any] = copy.deepcopy(engine_fixture("list_model_config"))
    for purpose, entry in changes.items():
        body["purposes"][purpose] = entry
    engine.respond("GET", LIST, 200, json=body)
    engine.serve("GET", "/openapi.json", 200, json=openapi(version))
    return body


def item(purpose: str, **fields: Any) -> dict[str, Any]:
    """A ``GET /admin/model-config/{purpose}`` body from the list fixture's entry."""
    fixture = engine_fixture("list_model_config")
    entry = copy.deepcopy(fixture["purposes"][purpose])
    return {**entry, "purpose": purpose, "config_revision": fixture["config_revision"], **fields}


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
