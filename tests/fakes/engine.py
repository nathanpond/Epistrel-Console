"""A fake Engine that answers only what its OpenAPI document allows.

Built from the recorded schema (``console/engine/openapi.json``) or one passed in. A request the
schema does not declare raises :class:`UndeclaredEngineCall`; a declared one with no canned answer
raises :class:`UnconfiguredEngineCall`. Both subclass ``AssertionError``, which the client and the
probe always re-raise, so a test fails instead of receiving a made-up answer. Every canned JSON
body is validated against the schema's response schema for that status before it is served.
"""

import copy
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from console.engine import schema as engine_schema

DOC_URI = "urn:epistrel:engine-openapi"
HTTP_METHODS = engine_schema.HTTP_METHODS


class UndeclaredEngineCall(AssertionError):
    """The Console called something the Engine's schema does not declare."""


class UnconfiguredEngineCall(AssertionError):
    """A declared operation was called but the test registered no answer for it."""


def recorded_schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(engine_schema.RECORDED_PATH.read_text(encoding="utf-8"))
    return loaded


def with_test_thing(doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` plus a synthetic non-exempt ``GET``/``POST /_test/thing`` with error responses."""
    extended = copy.deepcopy(doc)
    ok = {
        "description": "ok",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                }
            }
        },
    }
    error = {
        "description": "error",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }
    operation = {"responses": {"200": ok, "4XX": error, "5XX": error}}
    extended["paths"]["/_test/thing"] = {"get": operation, "post": copy.deepcopy(operation)}
    return extended


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _template_regex(path: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}/]+\})", path)
    body = "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts)
    return re.compile(f"^{body}$")


@dataclass
class _Canned:
    status: int
    json: object | None
    headers: dict[str, str]
    delay: float


class FakeEngine:
    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self.schema = schema if schema is not None else recorded_schema()
        self.requests: list[httpx.Request] = []
        self._routes: list[tuple[str, str, re.Pattern[str]]] = [
            (method.upper(), path, _template_regex(path))
            for path, item in self.schema.get("paths", {}).items()
            for method in HTTP_METHODS
            if method in item
        ]
        self._canned: dict[tuple[str, str], _Canned] = {}
        self._served: dict[tuple[str, str], _Canned] = {}
        self._failures: dict[tuple[str, str], Exception] = {}
        registry: Registry[Any] = Registry().with_resource(
            DOC_URI, Resource.from_contents(self.schema, default_specification=DRAFT202012)
        )
        self._registry = registry
        self.transport = httpx.MockTransport(self._handle)

    # --- configuring -------------------------------------------------------------------------

    def _declared(self, method: str, path: str) -> dict[str, Any]:
        item = self.schema.get("paths", {}).get(path)
        if not isinstance(item, dict) or method.lower() not in item:
            raise UndeclaredEngineCall(f"{method} {path} is not in the Engine's schema")
        operation: dict[str, Any] = item[method.lower()]
        return operation

    def _response_key(self, responses: dict[str, Any], status: int) -> str | None:
        for key in (str(status), f"{status // 100}XX", "default"):
            if key in responses:
                return key
        return None

    def respond(
        self,
        method: str,
        path: str,
        status: int,
        json: object | None = None,
        headers: dict[str, str] | None = None,
        delay: float = 0.0,
    ) -> None:
        """Answer ``method path`` (a schema path template) with this, until replaced.

        Refused unless the schema declares the operation and the status, and the JSON body
        validates against that status's response schema.
        """
        method = method.upper()
        responses = self._declared(method, path).get("responses", {})
        key = self._response_key(responses, status)
        if key is None:
            raise ValueError(f"{method} {path} declares no {status} response")
        if json is not None:
            content = responses[key].get("content", {}).get("application/json")
            if content is not None and "schema" in content:
                pointer = "/".join(
                    _escape(t) for t in ("paths", path, method.lower(), "responses", key, "content")
                )
                ref = {"$ref": f"{DOC_URI}#/{pointer}/application~1json/schema"}
                Draft202012Validator(ref, registry=self._registry).validate(json)
        self._canned[(method, path)] = _Canned(status, json, headers or {}, delay)

    def serve(
        self, method: str, path: str, status: int, json: object | None = None, delay: float = 0.0
    ) -> None:
        """Answer a route the schema does not list (e.g. ``/openapi.json``), unvalidated."""
        self._served[(method.upper(), path)] = _Canned(status, json, {}, delay)

    def fail(self, method: str, path: str, exc: Exception) -> None:
        """Raise ``exc`` (an httpx transport error) for ``method path``."""
        self._failures[(method.upper(), path)] = exc

    def reset(self) -> None:
        self.requests.clear()

    # --- answering ---------------------------------------------------------------------------

    def _match(self, method: str, path: str) -> str | None:
        for route_method, template, regex in self._routes:
            if route_method == method and regex.match(path):
                return template
        return None

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        served = self._served.get((method, path))
        template = None if served else self._match(method, path)
        key = (method, path) if served else (method, template or path)
        if key in self._failures:
            raise self._failures[key]
        canned = served
        if canned is None:
            if template is None:
                raise UndeclaredEngineCall(f"{method} {path} is not in the Engine's schema")
            canned = self._canned.get((method, template))
            if canned is None:
                raise UnconfiguredEngineCall(f"no answer registered for {method} {template}")
        if canned.delay:
            time.sleep(canned.delay)
        if canned.json is None:
            return httpx.Response(canned.status, headers=canned.headers)
        return httpx.Response(canned.status, json=canned.json, headers=canned.headers)
