"""The one client the Console uses to reach the Engine (and the only module that imports httpx).

Every request carries ``Authorization: Bearer <service key>`` when a key is set, except to the
Engine's unauthenticated routes (:data:`EXEMPT_PATHS`). Calls made for a person carry the
requesting principal in ``X-Epistrel-Principal``; a mutating call without one is refused before
anything is sent. Each public method is registered with its ``(method, path)`` so the contract
test can check it against the recorded schema. The client never logs; callers do.
"""

import functools
import json
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, ParamSpec, Self, TypeVar
from urllib.parse import quote

import httpx
from django.conf import settings

from console.engine.models import (
    ConfigChanges,
    ContentRating,
    ContentRatingList,
    ContentRatingMutation,
    ContentRatingSummary,
    DeploymentSettings,
    HealthResponse,
    HealthResult,
    History,
    IngestResponse,
    Model,
    ModelConfig,
    ModelConfigItem,
    ModelConfigMutation,
    OperationRecord,
    Policy,
    PolicyList,
    PolicyMutation,
    PolicyScope,
    QueuedTurn,
    RatingVersion,
    RatingVersionList,
    RebuildQueued,
    RebuildResult,
    RespondResponse,
    Story,
    StoryCreated,
    StoryList,
    StorySettings,
    StoryStatusChange,
    StorySummary,
)
from console.version import VERSION

USER_AGENT = f"epistrel-console/{VERSION}"
DEFAULT_TIMEOUT_S = 3.0  # reads (#43's timeouts)
MUTATION_TIMEOUT_S = 10.0  # ordinary mutations (#43's timeouts)
TURN_GRACE_S = 15.0  # a turn call waits its effective deadline plus this (#43's timeouts)
DEFAULT_TURN_DEADLINE_S = 45.0  # when neither the request nor the story names one (#43)
INGEST_PER_EVENT_S = 1.0  # an ingest waits its turn timeout plus this per event (#51)
LONG_MUTATION_TIMEOUT_S = 30.0  # fork and rebuild (#43's timeouts)
CONFIG_CHANGES_PAGE = 50  # #43's paging: Engine cursor lists ask for 50
# Routes the Engine serves without auth (Engine #44): never sent the service key.
EXEMPT_PATHS = frozenset({"/health", "/metrics", "/openapi.json", "/docs", "/redoc"})
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PRINCIPAL_HEADER = "X-Epistrel-Principal"
PRINCIPAL_MAX = 256
RESERVED_PRINCIPAL = "system"  # the Engine's background-work principal
# The headers ``_request`` sets itself; a caller's ``headers=`` never replaces them.
OWN_HEADERS = frozenset({"user-agent", "accept", "authorization", PRINCIPAL_HEADER.lower()})

# What ``_request`` returns. Code outside this module names the type through this alias, so
# only this module imports httpx.
EngineResponse = httpx.Response


class EngineClientError(Exception):
    """Base of every error the client raises."""


class EngineError(EngineClientError):
    """The Engine answered with a non-success status (or, for ``openapi()``, a bad body)."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None,
        http_status: int,
        *,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details if details is not None else {}
        self.http_status = http_status
        # The ``Retry-After`` header (seconds) a 423/503 carries, as sent (#48).
        self.retry_after = retry_after


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
    # Success statuses answered with another model than ``response_model`` (respond's 202
    # ``QueuedTurn``), as ``(status, model)`` pairs; the contract test checks each.
    status_models: tuple[tuple[int, type[Model]], ...] = ()


@dataclass(frozen=True)
class Call:
    """The public client method running now: its name (the operation log's ``kind``, #44) and
    its registration."""

    kind: str
    operation: Operation


# "GET /health" -> Operation; filled by @operation, read by the contract test.
OPERATIONS: dict[str, Operation] = {}

_CURRENT_CALL: ContextVar[Call | None] = ContextVar("engine_call", default=None)

P = ParamSpec("P")
R = TypeVar("R")


def current_call() -> Call | None:
    """The registered client method whose request is being sent, if any."""
    return _CURRENT_CALL.get()


@contextmanager
def calling(call: Call) -> Iterator[None]:
    """Mark the requests sent inside this block as ``call``'s (what ``@operation`` does)."""
    token = _CURRENT_CALL.set(call)
    try:
        yield
    finally:
        _CURRENT_CALL.reset(token)


def operation(
    method: str,
    path: str,
    response_model: type[Model] | None = None,
    schema_exempt: bool = False,
    status_models: Mapping[int, type[Model]] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register a client method as the Engine operation ``method path``.

    While the method runs, :func:`current_call` names it, so a subclass overriding
    ``_request`` (the operation log's recorder, #44) knows which method and registration a
    request belongs to.
    """

    def register(func: Callable[P, R]) -> Callable[P, R]:
        others = tuple(sorted((status_models or {}).items()))
        op = Operation(method, path, response_model, schema_exempt, others)
        OPERATIONS[f"{method} {path}"] = op
        call = Call(func.__name__, op)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with calling(call):
                return func(*args, **kwargs)

        return wrapper

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
        principal: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._service_key = service_key or None
        self._transport = transport
        self.timeout = timeout
        # Sent on every call that does not pass its own (``engine_client_for`` sets it, #43).
        self.principal = principal

    @classmethod
    def from_settings(cls, *, transport: httpx.BaseTransport | None = None) -> Self:
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
        params: dict[str, str] | None = None,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> EngineResponse:
        """Send one request and return the raw response, whatever its status.

        ``json`` is the request body; ``headers`` adds request headers (``Idempotency-Key``,
        ``X-Epistrel-Expected-Revision``, ``Prefer``) but never replaces the client's own.
        """
        method = method.upper()
        extra = {k: v for k, v in (headers or {}).items() if k.lower() not in OWN_HEADERS}
        sent = {**extra, "User-Agent": USER_AGENT, "Accept": "application/json"}
        if principal is None:
            principal = self.principal
        if principal is not None:
            sent[PRINCIPAL_HEADER] = _principal_header(principal)
        elif method in MUTATING:
            raise ValueError(f"{method} {path} needs a requesting principal")
        if self._service_key and path.split("?", 1)[0] not in EXEMPT_PATHS:
            sent["Authorization"] = f"Bearer {self._service_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout if timeout is not None else self.timeout),
                follow_redirects=False,
                verify=True,
                trust_env=False,
                transport=self._transport,
            ) as client:
                return client.request(
                    method, f"{self.base_url}{path}", headers=sent, params=params, json=json
                )
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

    @operation("GET", "/stories", response_model=StoryList)
    def list_stories(self, status: str | None = None) -> list[StorySummary]:
        """``GET /stories``: every story, newest first (optionally only one ``status``)."""
        params = {"status": status} if status is not None else None
        response = self._request("GET", "/stories", params=params)
        return _parse(response, StoryList).items

    @operation("POST", "/stories", response_model=StoryCreated)
    def create_story(self, spec: Mapping[str, Any], *, idempotency_key: str) -> StoryCreated:
        """``POST /stories`` with a blank story's ``StorySpec`` (#45).

        A blank story is created (201) or refused. A 200 (a scenario creation's ``applied:
        false``) or an answer without a story id is :class:`EngineError` ``bad_response``.
        """
        response = self._request(
            "POST",
            "/stories",
            json=dict(spec),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        created = _parse(response, StoryCreated)
        if response.status_code != 201 or created.story_id is None:
            raise EngineError(
                "bad_response",
                "the Engine did not create the story (no story id)",
                {},
                response.status_code,
            )
        return created

    @operation("GET", "/stories/{story_id}", response_model=Story)
    def get_story(self, story_id: str) -> Story:
        """``GET /stories/{id}``: one story with its cast and settings."""
        return _parse(self._request("GET", _story_path(story_id)), Story)

    @operation("POST", "/stories/{story_id}/archive", response_model=StoryStatusChange)
    def archive_story(self, story_id: str, *, idempotency_key: str) -> StoryStatusChange:
        """``POST /stories/{id}/archive``: turns are refused until it is unarchived."""
        return self._status_change(f"{_story_path(story_id)}/archive", idempotency_key)

    @operation("POST", "/stories/{story_id}/unarchive", response_model=StoryStatusChange)
    def unarchive_story(self, story_id: str, *, idempotency_key: str) -> StoryStatusChange:
        """``POST /stories/{id}/unarchive``: turns are accepted again."""
        return self._status_change(f"{_story_path(story_id)}/unarchive", idempotency_key)

    def _status_change(self, path: str, idempotency_key: str) -> StoryStatusChange:
        response = self._request(
            "POST",
            path,
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, StoryStatusChange)

    @operation("DELETE", "/stories/{story_id}")
    def delete_story(self, story_id: str, *, idempotency_key: str) -> None:
        """``DELETE /stories/{id}``: the story and all its history, permanently (204)."""
        response = self._request(
            "DELETE",
            _story_path(story_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    @operation("GET", "/admin/content-ratings", response_model=ContentRatingList)
    def list_content_ratings(self, include_retired: bool = False) -> list[ContentRatingSummary]:
        """``GET /admin/content-ratings``: the ratings in display order (retired ones only on
        request, sent as ``include_retired=1``)."""
        params = {"include_retired": "1"} if include_retired else None
        response = self._request("GET", "/admin/content-ratings", params=params)
        return _parse(response, ContentRatingList).items

    @operation("GET", "/admin/content-ratings/{rating_id}", response_model=ContentRating)
    def get_content_rating(self, rating_id: str) -> ContentRating:
        """``GET /admin/content-ratings/{id}``: the rating at its latest version, with its
        effective guardrails (own and inherited policies, each with its source rating)."""
        return _parse(self._request("GET", _rating_path(rating_id)), ContentRating)

    @operation(
        "GET", "/admin/content-ratings/{rating_id}/versions", response_model=RatingVersionList
    )
    def list_content_rating_versions(self, rating_id: str) -> list[RatingVersion]:
        """``GET /admin/content-ratings/{id}/versions``: every version (the live Engine lists
        them newest first; callers sort)."""
        response = self._request("GET", f"{_rating_path(rating_id)}/versions")
        return _parse(response, RatingVersionList).items

    @operation("POST", "/admin/content-ratings", response_model=ContentRatingMutation)
    def create_content_rating(
        self, fields: Mapping[str, Any], *, idempotency_key: str
    ) -> ContentRatingMutation:
        """``POST /admin/content-ratings`` with a ``RatingFields`` body (201)."""
        response = self._request(
            "POST",
            "/admin/content-ratings",
            json=dict(fields),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ContentRatingMutation)

    @operation("PUT", "/admin/content-ratings/{rating_id}", response_model=ContentRatingMutation)
    def update_content_rating(
        self, rating_id: str, replacement: Mapping[str, Any], *, idempotency_key: str
    ) -> ContentRatingMutation:
        """``PUT /admin/content-ratings/{id}`` with a ``RatingReplacement`` body: every field,
        replaced, as a new version. A retired rating is 409; an inheritance loop is 422
        ``inheritance_cycle``."""
        response = self._request(
            "PUT",
            _rating_path(rating_id),
            json=dict(replacement),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ContentRatingMutation)

    @operation("DELETE", "/admin/content-ratings/{rating_id}", response_model=ContentRatingMutation)
    def retire_content_rating(
        self, rating_id: str, *, idempotency_key: str
    ) -> ContentRatingMutation | None:
        """``DELETE /admin/content-ratings/{id}``: retire the rating (stories using it keep their
        version). ``None`` when it was already retired (204); the deployment default is 409."""
        response = self._request(
            "DELETE",
            _rating_path(rating_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        if response.status_code == 204:
            return None
        return _parse(response, ContentRatingMutation)

    @operation("GET", "/admin/settings", response_model=DeploymentSettings)
    def get_deployment_settings(self) -> DeploymentSettings:
        """``GET /admin/settings``: every deployment setting and its ``config_revision``."""
        return _parse(self._request("GET", "/admin/settings"), DeploymentSettings)

    @operation("PUT", "/admin/settings", response_model=DeploymentSettings)
    def update_admin_settings(
        self, patch: Mapping[str, Any], *, idempotency_key: str
    ) -> DeploymentSettings:
        """``PUT /admin/settings`` with a ``SettingsPatch`` body (#50): merge semantics, only the
        keys sent change (a list replaces the stored list; ``null`` unsets the three nullable
        settings). Answers every setting at the new ``config_revision``."""
        response = self._request(
            "PUT",
            "/admin/settings",
            json=dict(patch),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, DeploymentSettings)

    @operation("GET", "/admin/config-changes", response_model=ConfigChanges)
    def list_admin_config_changes(
        self,
        *,
        setting: str | None = None,
        change_class: str | None = None,
        since: str | None = None,
        after: str | None = None,
        limit: int = CONFIG_CHANGES_PAGE,
    ) -> ConfigChanges:
        """``GET /admin/config-changes``: the deployment's configuration changes, newest first,
        one page (``next`` is the ``after`` cursor of the following page)."""
        params = _config_change_params(setting, change_class, since, after, limit)
        return _parse(self._request("GET", "/admin/config-changes", params=params), ConfigChanges)

    @operation(
        "POST",
        "/admin/stories/{story_id}/rebuild",
        response_model=RebuildResult,
        status_models={202: RebuildQueued},
    )
    def rebuild_story(
        self, story_id: str, *, idempotency_key: str
    ) -> RebuildResult | RebuildQueued:
        """``POST /admin/stories/{id}/rebuild``: refold every projection from the event log.

        A synchronous rebuild is :class:`RebuildResult` (rows per table and the ``maintenance``
        operation id); a large story's is handed to a worker, :class:`RebuildQueued` (202, a
        job the Engine offers no status read for). Waits 30 s (#43's rebuild timeout).
        """
        response = self._request(
            "POST",
            f"/admin{_story_path(story_id)}/rebuild",
            headers={"Idempotency-Key": idempotency_key},
            timeout=LONG_MUTATION_TIMEOUT_S,
        )
        if response.status_code == 202:
            return _parse(response, RebuildQueued)
        return _parse(response, RebuildResult)

    @operation("GET", "/admin/model-config", response_model=ModelConfig)
    def list_model_config(self) -> ModelConfig:
        """``GET /admin/model-config``: every purpose's effective configuration (#46)."""
        return _parse(self._request("GET", "/admin/model-config"), ModelConfig)

    @operation("GET", "/admin/model-config/{purpose}", response_model=ModelConfigItem)
    def get_model_config(self, purpose: str) -> ModelConfigItem:
        """``GET /admin/model-config/{purpose}``: one purpose and the revision it was read at."""
        return _parse(self._request("GET", _purpose_path(purpose)), ModelConfigItem)

    @operation("PUT", "/admin/model-config/{purpose}", response_model=ModelConfigMutation)
    def put_model_config(
        self, purpose: str, config: Mapping[str, Any], *, idempotency_key: str
    ) -> ModelConfigMutation:
        """``PUT /admin/model-config/{purpose}``: the purpose's own ``base_url``, ``model`` and
        ``params``, replaced in full (an absent or ``null`` field inherits)."""
        response = self._request(
            "PUT",
            _purpose_path(purpose),
            json=dict(config),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ModelConfigMutation)

    @operation("DELETE", "/admin/model-config/{purpose}", response_model=ModelConfigMutation)
    def delete_model_config(
        self, purpose: str, *, idempotency_key: str
    ) -> ModelConfigMutation | None:
        """``DELETE /admin/model-config/{purpose}``: drop the purpose's own row (its own key
        too). The now-inherited entry, or ``None`` when there was nothing to drop (204)."""
        response = self._request(
            "DELETE",
            _purpose_path(purpose),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        if response.status_code == 204:
            return None
        return _parse(response, ModelConfigMutation)

    @operation("PUT", "/admin/model-config/{purpose}/api-key")
    def put_model_api_key(self, purpose: str, api_key: str, *, idempotency_key: str) -> None:
        """``PUT /admin/model-config/{purpose}/api-key`` (204). The operation log redacts the
        body's ``api_key`` (#44); nothing here keeps or logs it."""
        response = self._request(
            "PUT",
            f"{_purpose_path(purpose)}/api-key",
            json={"api_key": api_key},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    @operation("DELETE", "/admin/model-config/{purpose}/api-key")
    def delete_model_api_key(self, purpose: str, *, idempotency_key: str) -> None:
        """``DELETE /admin/model-config/{purpose}/api-key`` (204)."""
        response = self._request(
            "DELETE",
            f"{_purpose_path(purpose)}/api-key",
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    @operation("GET", "/admin/policies", response_model=PolicyList)
    def list_policies(
        self, scope: PolicyScope | None = None, *, include_retired: bool = False
    ) -> list[Policy]:
        """``GET /admin/policies``: global first, then by scope id and name (#47). ``scope``
        filters; retired policies only on request (sent as ``include_retired=1``)."""
        params: dict[str, str] = {}
        if scope is not None:
            params["scope"] = scope
        if include_retired:
            params["include_retired"] = "1"
        response = self._request("GET", "/admin/policies", params=params or None)
        return _parse(response, PolicyList).items

    @operation("GET", "/admin/policies/{policy_id}", response_model=Policy)
    def get_policy(self, policy_id: str) -> Policy:
        """``GET /admin/policies/{id}``: one policy, retired or not."""
        return _parse(self._request("GET", _policy_path(policy_id)), Policy)

    @operation("POST", "/admin/policies", response_model=PolicyMutation)
    def create_policy(self, fields: Mapping[str, Any], *, idempotency_key: str) -> PolicyMutation:
        """``POST /admin/policies`` with a ``PolicyFields`` body: a global or rating policy
        (201). A repeated key returns the first result."""
        response = self._request(
            "POST",
            "/admin/policies",
            json=dict(fields),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, PolicyMutation)

    @operation("PUT", "/admin/policies/{policy_id}", response_model=PolicyMutation)
    def update_policy(
        self, policy_id: str, replacement: Mapping[str, Any], *, idempotency_key: str
    ) -> PolicyMutation:
        """``PUT /admin/policies/{id}`` with a ``PolicyReplacement`` body: the whole content,
        replaced (scope, id and kind never change). A retired policy is 409."""
        response = self._request(
            "PUT",
            _policy_path(policy_id),
            json=dict(replacement),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, PolicyMutation)

    @operation("DELETE", "/admin/policies/{policy_id}", response_model=PolicyMutation)
    def retire_policy(self, policy_id: str, *, idempotency_key: str) -> PolicyMutation | None:
        """``DELETE /admin/policies/{id}``: retire the policy (it stays readable). ``None`` when
        it was already retired (204)."""
        response = self._request(
            "DELETE",
            _policy_path(policy_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        if response.status_code == 204:
            return None
        return _parse(response, PolicyMutation)

    @operation(
        "POST",
        "/stories/{story_id}/respond",
        response_model=RespondResponse,
        status_models={202: QueuedTurn},
    )
    def respond(
        self,
        story_id: str,
        body: Mapping[str, Any],
        *,
        idempotency_key: str,
        prefer_async: bool = True,
        timeout: float = DEFAULT_TURN_DEADLINE_S + TURN_GRACE_S,
    ) -> RespondResponse | QueuedTurn:
        """``POST /stories/{id}/respond``: play one turn (#48).

        A finished turn is :class:`RespondResponse` (200, a replay too); a queued one is
        :class:`QueuedTurn` (202, poll ``GET /operations/{id}``). ``prefer_async`` sends
        ``Prefer: respond-async`` (an idle story still answers 200 at once). ``timeout`` is the
        turn's effective deadline plus 15 s (:func:`turn_timeout`).
        """
        headers = {"Idempotency-Key": idempotency_key}
        if prefer_async:
            headers["Prefer"] = "respond-async"
        response = self._request(
            "POST",
            f"{_story_path(story_id)}/respond",
            json=dict(body),
            headers=headers,
            timeout=timeout,
        )
        if response.status_code == 202:
            return _parse(response, QueuedTurn)
        return _parse(response, RespondResponse)

    @operation(
        "POST",
        "/stories/{story_id}/ingest",
        response_model=IngestResponse,
        status_models={202: QueuedTurn},
    )
    def ingest(
        self,
        story_id: str,
        events: Sequence[Mapping[str, Any]],
        *,
        idempotency_key: str,
        timeout: float = DEFAULT_TURN_DEADLINE_S + TURN_GRACE_S,
    ) -> IngestResponse | QueuedTurn:
        """``POST /stories/{id}/ingest``: record player turns and narration without a reply
        (#51). ``events`` are ``IngestEvent`` bodies, 1-50, each with its own
        ``client_event_id`` (the Engine's per-event dedup; a resent id answers ``replayed``).

        No ``Prefer: respond-async``: the call waits. The Engine still answers
        :class:`QueuedTurn` (202) when a queued story's batch outwaits its turn deadline.
        ``timeout``: :func:`ingest_timeout`.
        """
        response = self._request(
            "POST",
            f"{_story_path(story_id)}/ingest",
            json={"events": [dict(event) for event in events]},
            headers={"Idempotency-Key": idempotency_key},
            timeout=timeout,
        )
        if response.status_code == 202:
            return _parse(response, QueuedTurn)
        return _parse(response, IngestResponse)

    @operation("GET", "/stories/{story_id}/history", response_model=History)
    def get_history(
        self,
        story_id: str,
        *,
        viewpoint: str | None = None,
        limit: int | None = None,
        before_turn: int | None = None,
    ) -> History:
        """``GET /stories/{id}/history``: one page (whole turns, newest page first, items
        ascending) for ``viewpoint`` (``character:<id>`` or ``omniscient``; else the story's
        default)."""
        params: dict[str, str] = {}
        if viewpoint:
            params["viewpoint"] = viewpoint
        if limit is not None:
            params["limit"] = str(limit)
        if before_turn is not None:
            params["before_turn"] = str(before_turn)
        response = self._request("GET", f"{_story_path(story_id)}/history", params=params or None)
        return _parse(response, History)

    @operation("GET", "/stories/{story_id}/settings", response_model=StorySettings)
    def get_story_settings(self, story_id: str) -> StorySettings:
        """``GET /stories/{id}/settings``: the story's settings, policies and effective budget
        (``effective_budget.turn_deadline`` is the turn deadline in seconds)."""
        return _parse(self._request("GET", f"{_story_path(story_id)}/settings"), StorySettings)

    @operation("GET", "/operations/{operation_id}", response_model=OperationRecord)
    def get_operation(self, operation_id: str, viewpoint: str | None = None) -> OperationRecord:
        """``GET /operations/{id}``: the Engine's record of one operation.

        ``viewpoint`` is needed to see a turn's stored ``result`` (the output viewpoint it was
        produced for); another named viewpoint is 409 ``idempotency_conflict``.
        """
        params = {"viewpoint": viewpoint} if viewpoint else None
        path = f"/operations/{quote(operation_id, safe='')}"
        return _parse(self._request("GET", path, params=params), OperationRecord)


def turn_timeout(deadline_s: float | None) -> float:
    """The HTTP timeout for a turn-producing call (respond, regenerate, ingest, swap): its
    effective deadline (the request's ``budget.deadline_s``, else the story's
    ``effective_budget.turn_deadline``, else 45 s) plus 15 s."""
    return (deadline_s if deadline_s is not None else DEFAULT_TURN_DEADLINE_S) + TURN_GRACE_S


def ingest_timeout(deadline_s: float | None, events: int) -> float:
    """The HTTP timeout for an ingest of ``events`` events: :func:`turn_timeout` of the story's
    turn deadline plus 1 s per event (#51)."""
    return turn_timeout(deadline_s) + INGEST_PER_EVENT_S * events


def _config_change_params(
    setting: str | None,
    change_class: str | None,
    since: str | None,
    after: str | None,
    limit: int,
) -> dict[str, str]:
    """The query of a configuration-change list (deployment here; a story's, #54, the same)."""
    params = {"limit": str(limit)}
    for name, value in (("setting", setting), ("class", change_class), ("since", since)):
        if value:
            params[name] = value
    if after:
        params["after"] = after
    return params


def _story_path(story_id: str) -> str:
    """``/stories/<id>``, the id percent-encoded (an unknown id still reaches the Engine)."""
    return f"/stories/{quote(story_id, safe='')}"


def _purpose_path(purpose: str) -> str:
    """``/admin/model-config/<purpose>``, the name percent-encoded."""
    return f"/admin/model-config/{quote(purpose, safe='')}"


def _rating_path(rating_id: str) -> str:
    """``/admin/content-ratings/<id>``, the id percent-encoded."""
    return f"/admin/content-ratings/{quote(rating_id, safe='')}"


def _policy_path(policy_id: str) -> str:
    """``/admin/policies/<id>``, the id percent-encoded."""
    return f"/admin/policies/{quote(policy_id, safe='')}"


def _parse[M: Model](response: httpx.Response, model: type[M]) -> M:
    """The typed body of a success, else :class:`EngineError` (envelope or ``bad_response``)."""
    _raise_for_envelope(response)
    parsed = model.from_json(_json_or_none(response))
    if parsed is None:
        raise EngineError(
            "bad_response",
            f"the Engine's answer is not a {model.__name__}",
            {},
            response.status_code,
        )
    return parsed


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
            error["code"],
            error["message"],
            details if isinstance(details, dict) else {},
            code,
            retry_after=response.headers.get("Retry-After"),
        )
    raise EngineError(
        f"http_{code}",
        _snippet(response),
        {},
        code,
        retry_after=response.headers.get("Retry-After"),
    )
