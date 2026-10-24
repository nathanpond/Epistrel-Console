"""The one client the Console uses to reach the Engine (and the only module that imports httpx).

Every request carries ``Authorization: Bearer <service key>`` when a key is set, except to the
Engine's unauthenticated routes (:data:`EXEMPT_PATHS`). Calls made for a person carry the
requesting principal in ``X-Epistrel-Principal``; a mutating call without one is refused before
anything is sent. Each public method is registered with its ``(method, path)`` so the contract
test can check it against the recorded schema. The client never logs; callers do.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar
from urllib.parse import quote

import httpx
from django.conf import settings

from console.engine.models import HealthResponse, HealthResult, Model
from console.version import VERSION

USER_AGENT = f"epistrel-console/{VERSION}"
DEFAULT_TIMEOUT_S = 3.0
# Routes the Engine serves without auth (Engine #44): never sent the service key.
EXEMPT_PATHS = frozenset({"/health", "/metrics", "/openapi.json", "/docs", "/redoc"})
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PRINCIPAL_HEADER = "X-Epistrel-Principal"
PRINCIPAL_MAX = 256
RESERVED_PRINCIPAL = "system"  # the Engine's background-work principal


class EngineClientError(Exception):
    """Base of every error the client raises."""


class EngineError(EngineClientError):
    """The Engine answered with a non-success status (or, for ``openapi()``, a bad body)."""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None, http_status: int
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}
        self.http_status = http_status


class EngineUnreachable(EngineClientError):
    """No HTTP answer. ``reason`` is ``timeout``, ``connection failed`` or ``transport error``.

    ``unexpected`` marks a failure that was not an ordinary transport error (the cause is
    chained), so a caller can log its traceback.
    """

    def __init__(self, reason: str, *, unexpected: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.unexpected = unexpected


class EngineNotConfigured(EngineClientError):
    """No Engine URL is set."""


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    response_model: type[Model] | None
    schema_exempt: bool


# "GET /health" -> Operation; filled by @operation, read by the contract test.
OPERATIONS: dict[str, Operation] = {}

P = ParamSpec("P")
R = TypeVar("R")


def operation(
    method: str,
    path: str,
    response_model: type[Model] | None = None,
    schema_exempt: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register a client method as the Engine operation ``method path``."""

    def register(func: Callable[P, R]) -> Callable[P, R]:
        OPERATIONS[f"{method} {path}"] = Operation(method, path, response_model, schema_exempt)
        return func

    return register


def principal_for(user: Any) -> str:
    """The requesting principal for a Console user: ``console:<username>``."""
    if getattr(user, "is_anonymous", True):
        raise ValueError("an anonymous user has no principal")
    username = user.get_username()
    if not username:
        raise ValueError("a user with an empty username has no principal")
    return f"console:{username}"


def _principal_header(principal: str) -> str:
    """The header value for ``principal``, refused here if the Engine would refuse it.

    The Engine checks the value it receives (``requesting_principal``): spaces and tabs around it
    trimmed, then not blank, at most 256 characters, no control characters, not ``system``.
    """
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in principal):
        raise ValueError("principal must not contain control characters")
    # Headers are ASCII: non-ASCII characters go as percent-encoded UTF-8, ASCII unchanged.
    encoded = "".join(ch if ord(ch) < 128 else quote(ch, safe="") for ch in principal)
    # Length is the encoded value's: one "é" is six characters ("%C3%A9") on the wire.
    value = encoded.strip(" ")
    if not value:
        raise ValueError("principal must not be blank")
    if len(value) > PRINCIPAL_MAX:
        raise ValueError(
            f"principal must be at most {PRINCIPAL_MAX} characters once encoded ({len(value)})"
        )
    if value == RESERVED_PRINCIPAL:
        raise ValueError(f"principal {RESERVED_PRINCIPAL!r} is reserved for the Engine")
    return encoded


class EngineClient:
    def __init__(
        self,
        base_url: str,
        service_key: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._service_key = service_key or None
        self._transport = transport
        self.timeout = timeout

    @classmethod
    def from_settings(cls, *, transport: httpx.BaseTransport | None = None) -> EngineClient:
        url: str | None = settings.ENGINE_URL
        if url is None:
            raise EngineNotConfigured("no Engine URL is configured")
        return cls(url, settings.ENGINE_SERVICE_KEY, transport=transport)

    def __repr__(self) -> str:
        key = "set" if self._service_key else "not set"
        return f"EngineClient({self.base_url!r}, service key {key})"

    def _request(
        self,
        method: str,
        path: str,
        *,
        principal: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Send one request and return the raw response, whatever its status."""
        method = method.upper()
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if principal is not None:
            headers[PRINCIPAL_HEADER] = _principal_header(principal)
        elif method in MUTATING:
            raise ValueError(f"{method} {path} needs a requesting principal")
        if self._service_key and path.split("?", 1)[0] not in EXEMPT_PATHS:
            headers["Authorization"] = f"Bearer {self._service_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout if timeout is not None else self.timeout),
                follow_redirects=False,
                verify=True,
                trust_env=False,
                transport=self._transport,
            ) as client:
                return client.request(method, f"{self.base_url}{path}", headers=headers)
        except AssertionError:
            raise  # the fake Engine's signals reach the test
        except httpx.TimeoutException as exc:
            raise EngineUnreachable("timeout") from exc
        except httpx.ConnectError as exc:
            raise EngineUnreachable("connection failed") from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable("transport error") from exc
        except Exception as exc:
            raise EngineUnreachable("transport error", unexpected=True) from exc

    @operation("GET", "/health", response_model=HealthResponse)
    def health(self) -> HealthResult:
        """``GET /health`` for any HTTP status; never raises :class:`EngineError`."""
        started = time.monotonic()
        response = self._request("GET", "/health")
        latency_ms = round((time.monotonic() - started) * 1000)
        raw = _json_or_none(response)
        return HealthResult(response.status_code, HealthResponse.from_json(raw), raw, latency_ms)

    @operation("GET", "/openapi.json", schema_exempt=True)
    def openapi(self, timeout: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        """The Engine's OpenAPI document. FastAPI does not list this route in it."""
        response = self._request("GET", "/openapi.json", timeout=timeout)
        code = response.status_code
        if code != 200:
            raise EngineError(f"http_{code}", _snippet(response), {}, code)
        doc = _json_or_none(response)
        if not isinstance(doc, dict):
            raise EngineError("bad_response", "the OpenAPI document is not a JSON object", {}, code)
        return doc


def _json_or_none(response: httpx.Response) -> object | None:
    try:
        parsed: object = json.loads(response.content)
    except ValueError:
        return None
    return parsed


def _snippet(response: httpx.Response) -> str:
    return response.content.decode("utf-8", errors="replace")[:200]


def _raise_for_envelope(response: httpx.Response) -> None:
    """Raise :class:`EngineError` for a non-2xx response, from the error envelope if present."""
    code = response.status_code
    if 200 <= code < 300:
        return
    body = _json_or_none(response)
    error = body.get("error") if isinstance(body, dict) else None
    if (
        isinstance(error, dict)
        and isinstance(error.get("code"), str)
        and isinstance(error.get("message"), str)
    ):
        details = error.get("details")
        raise EngineError(
            error["code"], error["message"], details if isinstance(details, dict) else {}, code
        )
    raise EngineError(f"http_{code}", _snippet(response), {}, code)
