"""A fake Engine that answers only what its OpenAPI document allows.

Built from the recorded schema (``console/engine/openapi.json``) or one passed in. A request the
schema does not declare raises :class:`UndeclaredEngineCall`; a declared one with no canned answer
raises :class:`UnconfiguredEngineCall`. Both subclass ``AssertionError``, which the client and the
probe always re-raise, so a test fails instead of receiving a made-up answer. Every canned JSON
body is validated against the schema's response schema for that status before it is served, and
a status whose schema declares a body must be given one.

A canned answer may be keyed on the query string too (``respond(..., query={"cursor": "X"})``,
#188's C14): a request carrying every listed parameter with that value gets it, the most
specific match winning, so page-1/page-2 tests don't rely on call order.
"""

import copy
import json
import re
import time
from collections.abc import Callable, Mapping
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
        self._queried: dict[tuple[str, str], list[tuple[dict[str, str], _Canned]]] = {}
        self._sequences: dict[tuple[str, str], list[_Canned]] = {}
        self._served: dict[tuple[str, str], _Canned] = {}
        self._failures: dict[tuple[str, str], Exception] = {}
        self._effects: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] = {}
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
        *,
        query: Mapping[str, str] | None = None,
    ) -> None:
        """Answer ``method path`` (a schema path template) with this, until replaced (a
        :meth:`side_effect` on it too).

        With ``query``, answer only a request whose query string carries every listed parameter
        with that value (extra parameters allowed; a repeated parameter matches when any of its
        values does); the most specific match wins (first registered on a tie), else the
        query-less answer. A ``query`` answer adds or replaces the answer for exactly that
        ``query`` and leaves the query-less answer, sequences and side effects alone; a plain
        answer replaces only the query-less one (and clears a sequence and a side effect).

        Refused unless the schema declares the operation and the status, and the JSON body
        validates against that status's response schema.
        """
        method = method.upper()
        if query is not None:
            wanted = {str(k): str(v) for k, v in query.items()}
            canned = self._validated(method, path, status, json, headers, delay)
            answers = self._queried.setdefault((method, path), [])
            for i, (registered, _) in enumerate(answers):
                if registered == wanted:
                    answers[i] = (wanted, canned)
                    return
            answers.append((wanted, canned))
            return
        self._sequences.pop((method, path), None)
        self._effects.pop((method, path), None)  # a canned answer replaces a side effect
        self._canned[(method, path)] = self._validated(method, path, status, json, headers, delay)

    def respond_sequence(self, method: str, path: str, answers: list[dict[str, Any]]) -> None:
        """Answer ``method path`` with ``answers`` in order, one per request; the last one
        repeats. Each answer is ``{"status", "json"?, "headers"?, "delay"?}``, validated as
        :meth:`respond` validates (a scripted poll: ``queued``, ``in_flight``, ``committed``)."""
        method = method.upper()
        if not answers:
            raise ValueError("a sequence needs at least one answer")
        canned = [
            self._validated(
                method,
                path,
                a["status"],
                a.get("json"),
                a.get("headers"),
                a.get("delay", 0.0),
            )
            for a in answers
        ]
        self._canned[(method, path)] = canned[-1]
        self._sequences[(method, path)] = canned[:-1]

    def _validated(
        self,
        method: str,
        path: str,
        status: int,
        json: object | None,
        headers: dict[str, str] | None,
        delay: float,
    ) -> _Canned:
        responses = self._declared(method, path).get("responses", {})
        key = self._response_key(responses, status)
        if key is None:
            raise ValueError(f"{method} {path} declares no {status} response")
        content = responses[key].get("content", {}).get("application/json")
        if content is not None and "schema" in content:
            if json is None:
                raise ValueError(f"{method} {path} {status} declares a JSON body: pass json=")
            pointer = "/".join(
                _escape(t) for t in ("paths", path, method.lower(), "responses", key, "content")
            )
            ref = {"$ref": f"{DOC_URI}#/{pointer}/application~1json/schema"}
            Draft202012Validator(ref, registry=self._registry).validate(json)
        return _Canned(status, json, headers or {}, delay)

    def serve(
        self, method: str, path: str, status: int, json: object | None = None, delay: float = 0.0
    ) -> None:
        """Answer a route the schema does not list (e.g. ``/openapi.json``), unvalidated.

        Refused for a route the schema declares: that one is answered through :meth:`respond`,
        which validates the body.
        """
        method = method.upper()
        if self._match(method, path) is not None:
            raise ValueError(f"{method} {path} is in the Engine's schema: use respond()")
        self._served[(method, path)] = _Canned(status, json, {}, delay)

    def side_effect(
        self, method: str, path: str, handler: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        """Answer ``method path`` (a schema path template) by calling ``handler`` with each
        request: for a binary answer (media content, #65) the JSON validation cannot cover.
        Refused unless the schema declares the operation; ``handler`` must answer a status the
        operation declares (checked per request)."""
        method = method.upper()
        self._declared(method, path)
        self._effects[(method, path)] = handler

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

    def _query_answer(self, key: tuple[str, str], params: httpx.QueryParams) -> _Canned | None:
        matching = [
            (registered, canned)
            for registered, canned in self._queried.get(key, [])
            if all(value in params.get_list(name) for name, value in registered.items())
        ]
        if not matching:
            return None
        return max(matching, key=lambda answer: len(answer[0]))[1]  # first on a tie

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        method, path = request.method, request.url.path
        served = self._served.get((method, path))
        # A path parameter may hold an encoded "/" (``%2F``, a principal, #76): the decoded
        # path then has an extra segment, so the raw path is matched too.
        raw_path = request.url.raw_path.decode("ascii").split("?", 1)[0]
        template = None if served else self._match(method, path) or self._match(method, raw_path)
        key = (method, path) if served else (method, template or path)
        if key in self._failures:
            raise self._failures[key]
        effect = self._effects.get(key)
        if effect is not None:
            answer = effect(request)
            responses = self._declared(method, key[1]).get("responses", {})
            if self._response_key(responses, answer.status_code) is None:
                raise UndeclaredEngineCall(f"{method} {key[1]} declares no {answer.status_code}")
            return answer
        canned = served
        if canned is None:
            if template is None:
                raise UndeclaredEngineCall(f"{method} {path} is not in the Engine's schema")
            queued = self._sequences.get((method, template))
            if queued:
                canned = queued.pop(0)
            else:
                canned = self._query_answer((method, template), request.url.params)
                if canned is None:
                    canned = self._canned.get((method, template))
            if canned is None:
                queries = [q for q, _ in self._queried.get((method, template), [])]
                raise UnconfiguredEngineCall(
                    f"no answer registered for {method} {template}"
                    f" (query {request.url.query.decode('ascii')!r}; registered queries {queries})"
                )
        if canned.delay:
            time.sleep(canned.delay)
        if canned.json is None:
            return httpx.Response(canned.status, headers=canned.headers)
        return httpx.Response(canned.status, json=canned.json, headers=canned.headers)
