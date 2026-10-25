"""The one client the Console uses to reach the Engine (and the only module that imports httpx).

Every request carries ``Authorization: Bearer <service key>`` when a key is set, except to the
Engine's unauthenticated routes (:data:`EXEMPT_PATHS`). Calls made for a person carry the
requesting principal in ``X-Epistrel-Principal``; a mutating call without one is refused before
anything is sent. Each public method is registered with its ``(method, path)`` so the contract
test can check it against the recorded schema. The client never logs; callers do.
"""

import functools
import inspect
import json
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import IO, Any, Literal, ParamSpec, Self, TypeVar
from urllib.parse import quote

import httpx
from django.conf import settings

from console.engine.models import (
    CatalogCharacter,
    CatalogCharacterCard,
    CatalogCharacterDraft,
    CatalogCharacterPage,
    CatalogCharacterStatus,
    CatalogScope,
    CatalogVersion,
    CatalogVersionList,
    CharacterPublished,
    CompanionDraft,
    ConfigChanges,
    ContentRating,
    ContentRatingList,
    ContentRatingMutation,
    ContentRatingSummary,
    CustomInstructions,
    CustomInstructionsWritten,
    DeploymentSettings,
    EffectiveConfig,
    EngineProfile,
    EngineProfilePage,
    ErasureBoundary,
    ErasureResult,
    ErasureStatus,
    ForkResult,
    HealthResponse,
    HealthResult,
    History,
    ImpactReport,
    IngestResponse,
    ListingStatus,
    MediaAsset,
    MediaKind,
    MediaPage,
    MediaUploaded,
    MessageFeedback,
    Model,
    ModelConfig,
    ModelConfigItem,
    ModelConfigMutation,
    OffboardingActions,
    OffboardingJob,
    OffboardingPlan,
    OffboardingQueued,
    OperationRecord,
    OwnerTransfer,
    Persona,
    PersonaPage,
    PersonaWritten,
    PinnedMemories,
    PinnedMemoriesWritten,
    Policy,
    PolicyList,
    PolicyMutation,
    PolicyScope,
    PrincipalLinks,
    ProfileDeleted,
    ProfileEdit,
    ProfileSchema,
    ProfileValidation,
    ProfileVersion,
    ProfileVersionList,
    ProfileWritten,
    Promotion,
    QueuedTurn,
    RatingApplied,
    RatingCheck,
    RatingCheckStarted,
    RatingSuggestion,
    RatingVersion,
    RatingVersionList,
    ReactivationCheck,
    RebuildQueued,
    RebuildResult,
    ReconcileResult,
    RegenerateResult,
    RelationRecord,
    Relations,
    RespondResponse,
    Scenario,
    ScenarioCard,
    ScenarioDraft,
    ScenarioListItem,
    ScenarioPage,
    ScenarioPublished,
    ScenarioStatus,
    Story,
    StoryCharacterAdded,
    StoryCreated,
    StoryList,
    StoryProfileWrite,
    StoryScope,
    StorySettings,
    StorySettingsWrite,
    StoryStatusChange,
    StorySummary,
    SubjectRef,
    SuggestionVersion,
    SwapResult,
    TruncateResult,
    UntruncateResult,
    ValidationResult,
    VariantChain,
    Variants,
)
from console.engine.timeouts import (
    CATALOG_PUBLISH,
    ERASURE,
    OFFBOARDING,
    PROFILE_EDIT,
    RECONCILE,
    SCENARIO_CREATE,
    STORY_CHARACTER_ADD,
    UPLOAD,
)
from console.entities import REGISTRY, EntityType, RecordedEntity
from console.version import VERSION

USER_AGENT = f"epistrel-console/{VERSION}"
DEFAULT_TIMEOUT_S = 3.0  # reads (#43's timeouts)
MUTATION_TIMEOUT_S = 10.0  # ordinary mutations (#43's timeouts)
TURN_GRACE_S = 15.0  # a turn call waits its effective deadline plus this (#43's timeouts)
DEFAULT_TURN_DEADLINE_S = 45.0  # when neither the request nor the story names one (#43)
INGEST_PER_EVENT_S = 1.0  # an ingest waits its turn timeout plus this per event (#51)
LONG_MUTATION_TIMEOUT_S = 30.0  # rebuild (#43's timeouts; fork uses the turn timeout since #81)
CONFIG_CHANGES_PAGE = 50  # #43's paging: Engine cursor lists ask for 50
CATALOG_PAGE = 50  # the same for catalog lists (#65)
PRINCIPAL_PAGE = 50  # the principal lookup's page, every type (#76)
INVALID_PROFILE = "invalid_profile"  # a profile that cannot run (#84's validate answers it)
ENGINE_PROFILES_PAGE = 200  # the Engine's largest page: a select reads every profile (#68)
# The media proxy's stream (#65): connect 3 s, then 30 s for each chunk, no total cap.
MEDIA_CONNECT_TIMEOUT_S = 3.0
MEDIA_READ_TIMEOUT_S = 30.0
MEDIA_CHUNK = 65536
# What the media proxy forwards to the Engine, and the answer headers it hands back (#65).
MEDIA_REQUEST_HEADERS = ("Range", "If-None-Match", "If-Range")
MEDIA_RESPONSE_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "ETag",
    "Accept-Ranges",
)
# Routes the Engine serves without auth (Engine #44): never sent the service key.
EXEMPT_PATHS = frozenset({"/health", "/metrics", "/openapi.json", "/docs", "/redoc"})
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
PRINCIPAL_HEADER = "X-Epistrel-Principal"
EXPECTED_REVISION_HEADER = "X-Epistrel-Expected-Revision"
PRINCIPAL_MAX = 256
RESERVED_PRINCIPAL = "system"  # the Engine's background-work principal
RESPONSE_PREFIX = "response:"  # ``@operation(entity_id="response:id")``: the id is in the answer
# ``@operation(entity_id="response:id|profile_id")``: the first source holding an id (#84: a
# copy's row names the new copy, or the source profile when the copy failed).
ENTITY_FALLBACK = "|"
# The headers ``_request`` sets itself; a caller's ``headers=`` never replaces them.
OWN_HEADERS = frozenset({"user-agent", "accept", "authorization", PRINCIPAL_HEADER.lower()})

# A multipart request's parts, in order: ``(name, (filename, content, content_type))``; a
# ``None`` filename sends a plain field (an upload's ``metadata``, #70).
type MultipartParts = list[tuple[str, tuple[str | None, IO[bytes] | bytes, str]]]

# A request's query: a mapping, or ``(name, value)`` pairs where a name repeats (a catalog list's
# ``label`` and ``relation``, #65).
type QueryParams = Mapping[str, str] | list[tuple[str, str]]

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
class StreamedContent:
    """A media answer being streamed (:meth:`EngineClient.open_media_content`): its status, the
    answer headers in :data:`MEDIA_RESPONSE_HEADERS` (as sent), and the body, read in chunks
    with :meth:`iter_bytes` or whole (an error envelope) with :meth:`error`."""

    status: int
    headers: Mapping[str, str]
    _response: httpx.Response = field(repr=False)

    def iter_bytes(self, chunk_size: int = MEDIA_CHUNK) -> Iterator[bytes]:
        """The body in chunks; a read that stalls or fails is :class:`EngineUnreachable`."""
        try:
            yield from self._response.iter_bytes(chunk_size)
        except httpx.TimeoutException as exc:
            raise EngineUnreachable("timeout") from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable("transport error") from exc

    def error(self) -> EngineError:
        """The answer (a non-success) as :class:`EngineError`, from its envelope if it has one;
        ``body`` keeps the bytes as sent (the proxy passes them through unchanged)."""
        try:
            self._response.read()
        except httpx.TimeoutException as exc:
            raise EngineUnreachable("timeout") from exc
        except httpx.TransportError as exc:
            raise EngineUnreachable("transport error") from exc
        try:
            _raise_for_envelope(self._response)
        except EngineError as exc:
            return exc
        return EngineError(f"http_{self.status}", "", {}, self.status)

    @property
    def body(self) -> bytes:
        """The body once :meth:`error` has read it."""
        return self._response.content


@dataclass(frozen=True)
class RecordedAbout:
    """What an operation-log row is about, from a ``record_about`` hook (#86): its
    ``entity_type``/``entity_id`` (any string; a type the registry does not know renders as
    ``<code>``) and its ``story_id`` (``None``: what the path or answer says)."""

    entity_type: str | None
    entity_id: str | None
    story_id: str | None = None


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    response_model: type[Model] | None
    schema_exempt: bool
    # Success statuses answered with another model than ``response_model`` (respond's 202
    # ``QueuedTurn``), as ``(status, model)`` pairs; the contract test checks each.
    status_models: tuple[tuple[int, type[Model]], ...] = ()
    # Whether the operation log records the call (#44, #64 convention 9); ``None``: by method,
    # POST/PUT/PATCH/DELETE (:attr:`is_recorded`).
    recorded: bool | None = None
    # The entity the call is about (#64 convention 9): an Engine ``EntityType`` and where its
    # id comes from, the name of a method argument or ``"response:<key>"`` for a create.
    entity: RecordedEntity | None = None
    entity_id: str | None = None
    # What the operation log stores as the request body, built from the call's arguments by
    # name (#70: an upload's metadata and its files' names, sizes and types, never the bytes);
    # ``None``: the JSON body as sent.
    record_body: Callable[[Mapping[str, Any]], object] | None = None
    # What the operation log stores as the (JSON, already redacted) response body (#86: an
    # erasure dry run's candidate snippets become ``"***"``); ``None``: the body as answered.
    record_response: Callable[[object], object] | None = None
    # What the row is about when a static ``entity``/``entity_id`` cannot say it (#86: the
    # erasure's subject kind and id, and its scope's story), from the call's arguments.
    record_about: Callable[[Mapping[str, Any]], RecordedAbout | None] | None = None
    # A 202 that answers once the work has ended (#87: reconcile), recorded ``ok`` at once
    # instead of ``queued`` (no job follows it).
    ended: bool = False

    @property
    def is_recorded(self) -> bool:
        return self.method.upper() in MUTATING if self.recorded is None else self.recorded


@dataclass(frozen=True)
class Call:
    """The public client method running now: its name (the operation log's ``kind``, #44), its
    registration and the arguments it was called with, by parameter name (#64: where the
    recorder reads a registration's ``entity_id`` argument from)."""

    kind: str
    operation: Operation
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def entity_ref(self, response_body: object = None) -> tuple[RecordedEntity, str] | None:
        """``(entity type, id)`` for the registration's ``entity``/``entity_id``: the named
        argument's value, or for ``"response:<key>"`` that key of the (JSON object) response.
        ``None`` when the registration names no entity or the id is not a non-blank string."""
        op = self.operation
        if op.entity is None or op.entity_id is None:
            return None
        for source in op.entity_id.split(ENTITY_FALLBACK):
            if source.startswith(RESPONSE_PREFIX):
                key = source.removeprefix(RESPONSE_PREFIX)
                value = response_body.get(key) if isinstance(response_body, dict) else None
            else:
                value = self.arguments.get(source)
            if isinstance(value, str) and value:
                return op.entity, value
        return None


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
    *,
    recorded: bool | None = None,
    entity: RecordedEntity | None = None,
    entity_id: str | None = None,
    record_body: Callable[[Mapping[str, Any]], object] | None = None,
    record_response: Callable[[object], object] | None = None,
    record_about: Callable[[Mapping[str, Any]], RecordedAbout | None] | None = None,
    listed: bool = True,
    ended: bool = False,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Register a client method as the Engine operation ``method path``.

    While the method runs, :func:`current_call` names it, so a subclass overriding
    ``_request`` (the operation log's recorder, #44) knows which method and registration a
    request belongs to.

    #64 (M3 convention 9) adds three keyword-only flags:

    - ``recorded``: whether the operation log writes a row for the call. Defaults to ``True``
      for POST/PUT/PATCH/DELETE and ``False`` otherwise. Set it explicitly to ``True`` on a
      principal-requiring POST that writes nothing (validate, rating suggestion, impact preview,
      erasure dry run, reconcile) and on feedback PUT/DELETE; ``recorded=False`` on a POST
      keeps it out of the log.
    - ``entity``: the Engine ``EntityType`` the call is about (``console.entities``), stored as
      the row's ``entity_type``.
    - ``entity_id``: where its id comes from: the name of one of the method's parameters (the
      path parameter, e.g. ``"character_id"``), or ``"response:<key>"`` to read it from the
      JSON answer (a create: ``"response:id"``). Both or neither of ``entity``/``entity_id``.
      Several sources joined with ``|`` are tried in order (#84's copy:
      ``"response:id|profile_id"``, the new copy's id, else the source's).

    #70 adds ``record_body``: the redaction hook for a request whose body is not JSON (a
    multipart upload). It is called with the method's arguments by name and returns what the
    operation log stores as the request's body.

    #86 adds ``record_response`` (called with the redacted JSON answer, returns what the row
    stores: an erasure dry run masks its candidates' snippets) and ``record_about`` (called with
    the method's arguments, returns a :class:`RecordedAbout` naming the row's entity and story
    when they depend on the arguments: an erasure's subject kind and its scope's story).

    #87 adds ``ended``: the call's 202 answers once the work has ended (reconcile), so its row
    is ``ok`` at once, never ``queued``.

    #75 adds ``listed``: ``False`` keeps the registration out of :data:`OPERATIONS` (keyed by
    method and path) for a second client method on an operation another method already lists
    (``create_story_from_scenario`` beside ``create_story``); its calls are still marked, so the
    operation log records them under their own kind and entity.

    Example::

        @operation("PATCH", "/catalog/characters/{character_id}", CharacterDetail,
                   entity="character", entity_id="character_id")
        def update_catalog_character(self, character_id: str, patch: ..., *, ...) -> ...: ...

    A story call needs no ``entity``: a row with a ``story_id`` and no entity of its own is
    recorded as ``entity_type="story"``, ``entity_id=story_id``.
    """
    if (entity is None) != (entity_id is None):
        raise ValueError("@operation: give both entity and entity_id, or neither")

    def register(func: Callable[P, R]) -> Callable[P, R]:
        others = tuple(sorted((status_models or {}).items()))
        op = Operation(
            method,
            path,
            response_model,
            schema_exempt,
            others,
            recorded,
            entity,
            entity_id,
            record_body,
            record_response,
            record_about,
            ended,
        )
        if listed:
            OPERATIONS[f"{method} {path}"] = op
        signature = inspect.signature(func)
        for source in (entity_id or "").split(ENTITY_FALLBACK) if entity_id else ():
            if not source.startswith(RESPONSE_PREFIX) and source not in signature.parameters:
                raise ValueError(f"@operation: {func.__name__} has no parameter {source!r}")

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            arguments: dict[str, Any] = {}
            if entity_id is not None or record_body is not None or record_about is not None:
                try:
                    arguments = dict(signature.bind_partial(*args, **kwargs).arguments)
                except TypeError:
                    arguments = {}  # the call itself will raise the TypeError
            with calling(Call(func.__name__, op, arguments)):
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


def is_control(ch: str) -> bool:
    """Whether ``ch`` is a control character as the Engine counts them: C0 (U+0000 to U+001F),
    DEL (U+007F) or C1 (U+0080 to U+009F)."""
    code = ord(ch)
    return code < 0x20 or 0x7F <= code <= 0x9F


def _principal_header(principal: str) -> bytes:
    """The header value for ``principal``, refused here if the Engine would refuse it.

    The Engine checks the value it receives (``requesting_principal``): spaces and tabs around it
    trimmed, then not blank, at most 256 characters, no control characters (C0, DEL, C1), not
    ``system``. Starlette decodes header bytes as Latin-1, so a principal that fits Latin-1 goes
    as its Latin-1 bytes and arrives unchanged (#64: ``café`` is ``café`` at the Engine). One
    that does not (only a default ``console:<username>`` can be such: the acting selector
    refuses them) goes as percent-encoded UTF-8, ASCII unchanged (#43's rule), and its length
    is the encoded value's: one "ŝ" is six characters ("%C5%9D") on the wire.
    """
    if any(is_control(ch) for ch in principal):
        raise ValueError("principal must not contain control characters")
    try:
        raw = principal.encode("latin-1")
        encoded = principal
    except UnicodeEncodeError:
        encoded = "".join(ch if ord(ch) < 128 else quote(ch, safe="") for ch in principal)
        raw = encoded.encode("ascii")
    value = encoded.strip(" ")
    if not value:
        raise ValueError("principal must not be blank")
    if len(value) > PRINCIPAL_MAX:
        raise ValueError(
            f"principal must be at most {PRINCIPAL_MAX} characters once encoded ({len(value)})"
        )
    if value == RESERVED_PRINCIPAL:
        raise ValueError(f"principal {RESERVED_PRINCIPAL!r} is reserved for the Engine")
    return raw


@dataclass(frozen=True)
class UploadPart:
    """One file of a media upload (#70): its name as sent (the base name only), its part's
    ``Content-Type``, its size and the open file the request streams it from."""

    filename: str
    content_type: str
    size: int
    file: IO[bytes] = field(repr=False)

    def part(self) -> tuple[str, IO[bytes], str]:
        return (self.filename, self.file, self.content_type)

    def described(self) -> dict[str, object]:
        """What the operation log keeps of it: name, size and type, never the bytes."""
        return {"name": self.filename, "size": self.size, "content_type": self.content_type}


@dataclass(frozen=True)
class MediaUploadRequest:
    """``POST /media``'s ``metadata`` part (``MediaUpload``). :meth:`to_json` leaves out every
    field that is ``None``, except ``depicted_subjects``, which is always sent (``[]`` declares
    that the asset depicts nobody)."""

    kind: MediaKind
    content_rating_id: str
    depicted_subjects: list[SubjectRef]
    owner: str | None = None
    alt_text: str | None = None
    caption: str | None = None
    poster: str | None = None

    def to_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": self.kind,
            "content_rating_id": self.content_rating_id,
            "depicted_subjects": [{"kind": s.kind, "id": s.id} for s in self.depicted_subjects],
        }
        for name in ("owner", "alt_text", "caption", "poster"):
            value = getattr(self, name)
            if value is not None:
                body[name] = value
        return body


def upload_record(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """``upload_media``'s request as the operation log stores it (#70): the metadata, and each
    file's name, size and type."""
    metadata = arguments.get("metadata")
    file = arguments.get("file")
    poster = arguments.get("poster_file")
    return {
        "metadata": metadata.to_json() if isinstance(metadata, MediaUploadRequest) else None,
        "file": file.described() if isinstance(file, UploadPart) else None,
        "poster_file": poster.described() if isinstance(poster, UploadPart) else None,
    }


# --- erasure recording (#86) ----------------------------------------------------------------------

SNIPPET_MASK = "***"
# How the operation log names an erasure's subject (#86): the registry's type for a catalog
# character or a persona; a story character has no registry type (its own string, shown raw).
SUBJECT_ENTITY = {
    "catalog_character": "character",
    "persona": "persona",
    "character": "story_character",
}


def erasure_record_response(body: object) -> object:
    """An erasure answer as the operation log stores it: every ``candidates[].snippet``
    replaced by ``"***"`` (the Engine returns snippets once and never stores them; neither does
    the Console)."""
    if not isinstance(body, dict) or not isinstance(body.get("candidates"), list):
        return body
    masked = [
        {**c, "snippet": SNIPPET_MASK} if isinstance(c, dict) and "snippet" in c else c
        for c in body["candidates"]
    ]
    return {**body, "candidates": masked}


def erasure_about(arguments: Mapping[str, Any]) -> RecordedAbout | None:
    """The erasure row's entity (the subject: its kind through :data:`SUBJECT_ENTITY`, an
    unknown kind raw) and its story (the scope's story only; ``None`` for the catalog)."""
    subject = arguments.get("subject")
    if not isinstance(subject, SubjectRef):
        return None
    scope = arguments.get("scope")
    story = scope.story if isinstance(scope, StoryScope) else None
    return RecordedAbout(SUBJECT_ENTITY.get(subject.kind, subject.kind), subject.id, story)


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
        # The ``Location`` of the latest held-back edit's 202 (#81; the view compares it with
        # the body's ``job_id``, since the client never logs).
        self.last_location: str | None = None

    @classmethod
    def from_settings(cls, *, transport: httpx.BaseTransport | None = None) -> Self:
        url: str | None = settings.ENGINE_URL
        if url is None:
            raise EngineNotConfigured("no Engine URL is configured")
        return cls(url, settings.ENGINE_SERVICE_KEY, transport=transport)

    def __repr__(self) -> str:
        key = "set" if self._service_key else "not set"
        return f"EngineClient({self.base_url!r}, service key {key})"

    def _wire_headers(
        self,
        method: str,
        path: str,
        principal: str | None,
        headers: Mapping[str, str] | None,
        *,
        accept: str,
    ) -> list[tuple[bytes, bytes]]:
        """The request's headers as wire bytes: the caller's (never replacing the client's own),
        ``User-Agent``, ``Accept``, the principal (refused here when a mutation has none) and,
        off the exempt routes, the service key."""
        extra = {k: v for k, v in (headers or {}).items() if k.lower() not in OWN_HEADERS}
        sent: dict[str, str | bytes] = {**extra, "User-Agent": USER_AGENT, "Accept": accept}
        if principal is None:
            principal = self.principal
        if principal is not None:
            sent[PRINCIPAL_HEADER] = _principal_header(principal)
        elif method in MUTATING:
            raise ValueError(f"{method} {path} needs a requesting principal")
        if self._service_key and path.split("?", 1)[0] not in EXEMPT_PATHS:
            sent["Authorization"] = f"Bearer {self._service_key}"
        # The principal is already bytes (Latin-1, ``_principal_header``); the rest are ASCII.
        return [
            (name.encode("ascii"), v if isinstance(v, bytes) else v.encode("ascii"))
            for name, v in sent.items()
        ]

    def _http(self, timeout: httpx.Timeout) -> httpx.Client:
        return httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            verify=True,
            trust_env=False,
            transport=self._transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        principal: str | None = None,
        timeout: float | None = None,
        params: QueryParams | None = None,
        json: object | None = None,
        headers: Mapping[str, str] | None = None,
        files: MultipartParts | None = None,
    ) -> EngineResponse:
        """Send one request and return the raw response, whatever its status.

        ``json`` is the request body; ``headers`` adds request headers (``Idempotency-Key``,
        ``X-Epistrel-Expected-Revision``, ``Prefer``) but never replaces the client's own.
        ``files`` sends a ``multipart/form-data`` body instead (#70), streamed from the parts'
        file objects.
        """
        method = method.upper()
        wire = self._wire_headers(method, path, principal, headers, accept="application/json")
        pairs = params.items() if isinstance(params, Mapping) else params
        query: list[tuple[str, str | int | float | bool | None]] | None = (
            None if pairs is None else list(pairs)
        )
        try:
            with self._http(httpx.Timeout(timeout if timeout is not None else self.timeout)) as c:
                return c.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=wire,
                    params=query,
                    json=json,
                    files=files,
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

    @operation(
        "POST",
        "/stories",
        response_model=StoryCreated,
        entity="scenario",
        entity_id="scenario_id",
        listed=False,
    )
    def create_story_from_scenario(
        self, body: Mapping[str, Any], *, scenario_id: str, idempotency_key: str
    ) -> StoryCreated:
        """``POST /stories`` with a ``StoryFromScenario`` body (#75), on the scenario-creation
        timeout. Answers the 201 (a story) and the 200 ``applied: false`` (nothing created,
        ``story_id`` ``None``) alike; a refusal is :class:`EngineError`.

        ``scenario_id`` (the body's ``scenario.id``) names the operation log's entity: the row
        is recorded as ``create_story_from_scenario`` on the scenario, and its ``story_id`` is
        set from a 201's answer. Not listed in :data:`OPERATIONS` (``create_story`` holds
        ``POST /stories``; both answer :class:`StoryCreated`)."""
        response = self._request(
            "POST",
            "/stories",
            json=dict(body),
            headers={"Idempotency-Key": idempotency_key},
            timeout=SCENARIO_CREATE,
        )
        created = _parse(response, StoryCreated)
        if response.status_code == 201 and created.story_id is None:
            raise EngineError("bad_response", "the Engine answered 201 without a story id", {}, 201)
        return created

    @operation("GET", "/stories/{story_id}", response_model=Story)
    def get_story(self, story_id: str, *, viewpoint: str | None = None) -> Story:
        """``GET /stories/{id}``: one story with its cast and settings. ``viewpoint=omniscient``
        (#72) adds each character's ``true_age`` and ``promoted_to``."""
        params = {"viewpoint": viewpoint} if viewpoint else None
        return _parse(self._request("GET", _story_path(story_id), params=params), Story)

    # --- story characters (#72) -----------------------------------------------------------------

    @operation("POST", "/stories/{story_id}/characters", response_model=StoryCharacterAdded)
    def add_story_character(
        self,
        story_id: str,
        *,
        name: str,
        definition: str,
        true_age: int,
        safe_fallback_text: str | None = None,
        deflection_style: str | None = None,
        images: Sequence[str] = (),
        expected_revision: int | None,
        idempotency_key: str,
    ) -> StoryCharacterAdded:
        """``POST /stories/{id}/characters``: an ad-hoc character joins the running story's
        cast (never a player character). The optional fields are sent only when given;
        ``expected_revision`` (the revision the form was opened at) is sent as
        ``X-Epistrel-Expected-Revision``. The definition and deflection style pass the input
        gate, so the timeout allows for its model calls."""
        body: dict[str, Any] = {"name": name, "definition": definition, "true_age": true_age}
        if safe_fallback_text is not None:
            body["safe_fallback_text"] = safe_fallback_text
        if deflection_style is not None:
            body["deflection_style"] = deflection_style
        if images:
            body["images"] = list(images)
        headers = {"Idempotency-Key": idempotency_key}
        if expected_revision is not None:
            headers[EXPECTED_REVISION_HEADER] = str(expected_revision)
        response = self._request(
            "POST",
            f"{_story_path(story_id)}/characters",
            json=body,
            headers=headers,
            timeout=STORY_CHARACTER_ADD,
        )
        return _parse(response, StoryCharacterAdded)

    @operation(
        "POST",
        "/stories/{story_id}/characters/{character_id}/promote",
        response_model=Promotion,
        entity="character",
        entity_id="response:catalog_id",
    )
    def promote_story_character(
        self,
        story_id: str,
        character_id: str,
        *,
        name: str,
        owner: str,
        content_rating_id: str | None = None,
        idempotency_key: str,
    ) -> Promotion:
        """``POST /stories/{id}/characters/{cid}/promote``: a new draft catalog character made
        from the story's character. ``name`` and ``owner`` are always sent; without
        ``content_rating_id`` the Engine applies the story's rating. The story's revision does
        not move, so no expected revision is sent."""
        body: dict[str, Any] = {"name": name, "owner": owner}
        if content_rating_id is not None:
            body["content_rating_id"] = content_rating_id
        response = self._request(
            "POST",
            f"{_story_path(story_id)}/characters/{quote(character_id, safe='')}/promote",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, Promotion)

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

    # --- a story's content rating (#80) -----------------------------------------------------

    @operation(
        "PUT",
        "/stories/{story_id}/content-rating",
        response_model=RatingApplied,
        status_models={202: RatingCheckStarted},
    )
    def set_story_content_rating(
        self, story_id: str, content_rating_id: str, *, idempotency_key: str
    ) -> RatingApplied | RatingCheckStarted:
        """``PUT /stories/{id}/content-rating``: change the story's rating. A relaxing change
        (or the current rating) is :class:`RatingApplied` (200); a tightening one pauses the
        story and starts a history check, :class:`RatingCheckStarted` (202, the operation log
        records it ``queued`` with its ``job_id``). 409 ``story_archived`` / ``story_paused`` /
        ``revision_conflict``, 422 ``rating_retired``. The Engine holds the write behind a
        running turn, so it waits the turn timeout (#64 convention 18)."""
        response = self._request(
            "PUT",
            f"{_story_path(story_id)}/content-rating",
            json={"content_rating_id": content_rating_id},
            headers={"Idempotency-Key": idempotency_key},
            timeout=turn_timeout(None),
        )
        if response.status_code == 202:
            return _parse(response, RatingCheckStarted)
        return _parse(response, RatingApplied)

    @operation(
        "GET", "/stories/{story_id}/content-rating/checks/{job_id}", response_model=RatingCheck
    )
    def get_rating_check(self, story_id: str, job_id: str) -> RatingCheck:
        """``GET /stories/{id}/content-rating/checks/{job_id}``: a rating (or reactivation)
        check's status, progress and, once it ends, its result. 404 ``not_found`` for a job that
        is not one of the story's, or once the check's 30 days are up. Unrecorded."""
        path = f"{_story_path(story_id)}/content-rating/checks/{quote(job_id, safe='')}"
        return _parse(self._request("GET", path), RatingCheck)

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

    # --- Timeline operations (#53) ------------------------------------------------------------
    # Each sends the revision the operator was looking at as ``X-Epistrel-Expected-Revision``
    # (when given): a stale one is 409 ``revision_conflict`` with nothing written.

    def _timeline(
        self,
        story_id: str,
        action: str,
        *,
        body: Mapping[str, Any] | None,
        expected_revision: int | None,
        idempotency_key: str,
        timeout: float,
    ) -> EngineResponse:
        headers = {"Idempotency-Key": idempotency_key}
        if expected_revision is not None:
            headers[EXPECTED_REVISION_HEADER] = str(expected_revision)
        return self._request(
            "POST",
            f"{_story_path(story_id)}/{action}",
            json=dict(body) if body is not None else None,
            headers=headers,
            timeout=timeout,
        )

    def _held(self, response: EngineResponse) -> ReactivationCheck:
        """A held-back edit's 202 (#81): the check started, with its ``Location`` kept as
        :attr:`last_location`."""
        self.last_location = response.headers.get("Location")
        return _parse(response, ReactivationCheck)

    @operation("POST", "/stories/{story_id}/regenerate", response_model=RegenerateResult)
    def regenerate(
        self,
        story_id: str,
        body: Mapping[str, Any],
        *,
        expected_revision: int | None,
        idempotency_key: str,
        timeout: float = DEFAULT_TURN_DEADLINE_S + TURN_GRACE_S,
    ) -> RegenerateResult:
        """``POST /stories/{id}/regenerate``: answer the latest turn anew as its next variant
        (the old one becomes dormant). ``body`` is a ``RegenerateRequest`` (``turn``, and
        ``prompt_params`` / ``render_character_ids`` / ``output_viewpoint`` overrides; omitted
        ones reuse the recorded values). The Engine never queues it (423 ``story_busy``), so
        there is no ``Prefer``. ``timeout``: :func:`turn_timeout`."""
        response = self._timeline(
            story_id,
            "regenerate",
            body=body,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        return _parse(response, RegenerateResult)

    @operation("POST", "/stories/{story_id}/truncate", response_model=TruncateResult)
    def truncate(
        self, story_id: str, to_turn: int, *, expected_revision: int | None, idempotency_key: str
    ) -> TruncateResult:
        """``POST /stories/{id}/truncate``: rewind to ``to_turn`` (0 to the head - 1); every
        later operation becomes ``discarded`` (``untruncate`` restores them)."""
        response = self._timeline(
            story_id,
            "truncate",
            body={"to_turn": to_turn},
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, TruncateResult)

    @operation(
        "POST",
        "/stories/{story_id}/untruncate",
        response_model=UntruncateResult,
        status_models={202: ReactivationCheck},
    )
    def untruncate(
        self,
        story_id: str,
        *,
        expected_revision: int | None,
        idempotency_key: str,
        timeout: float = DEFAULT_TURN_DEADLINE_S + TURN_GRACE_S,
    ) -> UntruncateResult | ReactivationCheck:
        """``POST /stories/{id}/untruncate`` with no body: undo the most recent rewind. 409
        ``nothing_to_undo`` when there is none; :class:`ReactivationCheck` (202) when the turns
        it restores must be re-checked first. ``timeout``: :func:`turn_timeout` (the Engine may
        hold it behind a running turn, #64 convention 18)."""
        response = self._timeline(
            story_id,
            "untruncate",
            body=None,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        if response.status_code == 202:
            return self._held(response)
        return _parse(response, UntruncateResult)

    @operation(
        "POST",
        "/stories/{story_id}/swap",
        response_model=SwapResult,
        status_models={202: ReactivationCheck},
    )
    def swap(
        self,
        story_id: str,
        turn: int,
        variant: int,
        *,
        expected_revision: int | None,
        idempotency_key: str,
        timeout: float = DEFAULT_TURN_DEADLINE_S + TURN_GRACE_S,
    ) -> SwapResult | ReactivationCheck:
        """``POST /stories/{id}/swap``: make chain ``turn:variant`` (``to_variant_chain``) the
        active timeline. 409 ``unknown_chain`` lists ``candidates``; :class:`ReactivationCheck`
        (202) when its turns must be re-checked first. ``timeout``: :func:`turn_timeout`."""
        response = self._timeline(
            story_id,
            "swap",
            body={"to_variant_chain": {"turn": turn, "variant": variant}},
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        if response.status_code == 202:
            return self._held(response)
        return _parse(response, SwapResult)

    @operation(
        "POST",
        "/stories/{story_id}/fork",
        response_model=ForkResult,
        status_models={202: ReactivationCheck},
    )
    def fork(
        self,
        story_id: str,
        *,
        at_turn: int,
        name: str,
        variant_chain: tuple[int, int] | None = None,
        expected_revision: int | None,
        idempotency_key: str,
        timeout: float = DEFAULT_TURN_DEADLINE_S + TURN_GRACE_S,
    ) -> ForkResult | ReactivationCheck:
        """``POST /stories/{id}/fork``: copy the story through ``at_turn`` into a new one named
        ``name`` (201), along ``variant_chain`` (``(turn, variant)``; default the active chain). An
        archived story can be forked. :class:`ReactivationCheck` (202) when a dormant chain's
        turns must be re-checked first. ``timeout``: :func:`turn_timeout` (#81: the Engine may
        hold it behind a running turn, #64 convention 18)."""
        body: dict[str, Any] = {"at_turn": at_turn, "name": name}
        if variant_chain is not None:
            body["variant_chain"] = {"turn": variant_chain[0], "variant": variant_chain[1]}
        response = self._timeline(
            story_id,
            "fork",
            body=body,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            timeout=timeout,
        )
        if response.status_code == 202:
            return self._held(response)
        return _parse(response, ForkResult)

    @operation("GET", "/stories/{story_id}/history", response_model=History)
    def get_history(
        self,
        story_id: str,
        *,
        viewpoint: str | None = None,
        as_of: int | None = None,
        limit: int | None = None,
        before_turn: int | None = None,
        actor_character_id: str | None = None,
        variant: str | None = None,
        include_audit: bool = False,
        include_summary: bool = False,
    ) -> History:
        """``GET /stories/{id}/history``: one page (whole turns, newest page first, items
        ascending) for ``viewpoint`` (``character:<id>`` or ``omniscient``; else the story's
        default), as of turn ``as_of`` (default: head, or the chain's tip), along chain
        ``variant`` (``T:V``). ``include_audit`` needs ``omniscient`` (403
        ``omniscient_required``). A ``None`` or ``False`` argument is left out of the query
        (#52)."""
        params: dict[str, str] = {}
        if viewpoint:
            params["viewpoint"] = viewpoint
        if as_of is not None:
            params["as_of"] = str(as_of)
        if limit is not None:
            params["limit"] = str(limit)
        if before_turn is not None:
            params["before_turn"] = str(before_turn)
        if actor_character_id:
            params["actor_character_id"] = actor_character_id
        if variant:
            params["variant"] = variant
        if include_audit:
            params["include_audit"] = "1"
        if include_summary:
            params["include_summary"] = "1"
        response = self._request("GET", f"{_story_path(story_id)}/history", params=params or None)
        return _parse(response, History)

    @operation("GET", "/stories/{story_id}/variants", response_model=Variants)
    def list_variants(self, story_id: str) -> list[VariantChain]:
        """``GET /stories/{id}/variants``: every chain of the story's variant tree (#52)."""
        return _parse(self._request("GET", f"{_story_path(story_id)}/variants"), Variants).items

    @operation("GET", "/stories/{story_id}/settings", response_model=StorySettings)
    def get_story_settings(self, story_id: str) -> StorySettings:
        """``GET /stories/{id}/settings``: the story's settings, policies and effective budget
        (``effective_budget.turn_deadline`` is the turn deadline in seconds)."""
        return _parse(self._request("GET", f"{_story_path(story_id)}/settings"), StorySettings)

    # --- A story's settings, pinned memories and custom instructions (#54) --------------------
    # None sends ``X-Epistrel-Expected-Revision`` (#43: only where an AC names it).

    @operation("PATCH", "/stories/{story_id}/settings", response_model=StorySettingsWrite)
    def patch_story_settings(
        self, story_id: str, patch: Mapping[str, Any], *, idempotency_key: str
    ) -> StorySettingsWrite:
        """``PATCH /stories/{id}/settings`` with a ``StorySettingsPatch`` (a JSON merge patch:
        a key sets that setting, ``null`` resets it; ``policies`` upserts story policies)."""
        response = self._request(
            "PATCH",
            f"{_story_path(story_id)}/settings",
            json=dict(patch),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, StorySettingsWrite)

    @operation("GET", "/stories/{story_id}/pinned-memories", response_model=PinnedMemories)
    def get_pinned_memories(self, story_id: str) -> PinnedMemories:
        """``GET /stories/{id}/pinned-memories``: the pinned memories in prompt order."""
        path = f"{_story_path(story_id)}/pinned-memories"
        return _parse(self._request("GET", path), PinnedMemories)

    @operation("PUT", "/stories/{story_id}/pinned-memories", response_model=PinnedMemoriesWritten)
    def put_pinned_memories(
        self, story_id: str, items: Sequence[Mapping[str, Any]], *, idempotency_key: str
    ) -> PinnedMemoriesWritten:
        """``PUT /stories/{id}/pinned-memories``: replace the pinned memories (``items`` in
        order, each ``{id?, text}``; an empty list clears them)."""
        response = self._request(
            "PUT",
            f"{_story_path(story_id)}/pinned-memories",
            json={"items": [dict(item) for item in items]},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, PinnedMemoriesWritten)

    @operation("GET", "/stories/{story_id}/custom-instructions", response_model=CustomInstructions)
    def get_custom_instructions(self, story_id: str) -> CustomInstructions:
        """``GET /stories/{id}/custom-instructions``: the text (``""`` when none)."""
        path = f"{_story_path(story_id)}/custom-instructions"
        return _parse(self._request("GET", path), CustomInstructions)

    @operation(
        "PUT",
        "/stories/{story_id}/custom-instructions",
        response_model=CustomInstructionsWritten,
    )
    def put_custom_instructions(
        self, story_id: str, text: str, *, idempotency_key: str
    ) -> CustomInstructionsWritten:
        """``PUT /stories/{id}/custom-instructions``: replace the text (``""`` clears it)."""
        response = self._request(
            "PUT",
            f"{_story_path(story_id)}/custom-instructions",
            json={"text": text},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, CustomInstructionsWritten)

    @operation("GET", "/stories/{story_id}/config-changes", response_model=ConfigChanges)
    def list_story_config_changes(
        self,
        story_id: str,
        *,
        setting: str | None = None,
        change_class: str | None = None,
        since: str | None = None,
        after: str | None = None,
        limit: int = CONFIG_CHANGES_PAGE,
    ) -> ConfigChanges:
        """``GET /stories/{id}/config-changes``: the story's configuration changes (settings,
        story and character policies), newest first, one page."""
        params = _config_change_params(setting, change_class, since, after, limit)
        path = f"{_story_path(story_id)}/config-changes"
        return _parse(self._request("GET", path, params=params), ConfigChanges)

    @operation("GET", "/operations/{operation_id}", response_model=OperationRecord)
    def get_operation(self, operation_id: str, viewpoint: str | None = None) -> OperationRecord:
        """``GET /operations/{id}``: the Engine's record of one operation.

        ``viewpoint`` is needed to see a turn's stored ``result`` (the output viewpoint it was
        produced for); another named viewpoint is 409 ``idempotency_conflict``.
        """
        params = {"viewpoint": viewpoint} if viewpoint else None
        path = f"/operations/{quote(operation_id, safe='')}"
        return _parse(self._request("GET", path, params=params), OperationRecord)

    # --- catalog characters (#65) ---------------------------------------------------------------

    @operation("GET", "/catalog/characters", response_model=CatalogCharacterPage)
    def list_catalog_characters(
        self,
        *,
        q: str | None = None,
        labels: Sequence[str] = (),
        content_rating: str | None = None,
        listing_status: str | None = None,
        owner: str | None = None,
        relations: Sequence[str] = (),
        status: str | None = None,
        cursor: str | None = None,
        limit: int = CATALOG_PAGE,
    ) -> CatalogCharacterPage:
        """``GET /catalog/characters``: one page of cards (``projection=card``, the only one a
        list has). ``labels`` and ``relations`` (``name:principal`` or ``name:``) are sent as
        repeated parameters (every label must match); a blank filter is not sent."""
        params: list[tuple[str, str]] = [("limit", str(limit))]
        for name, value in (
            ("q", q),
            ("content_rating", content_rating),
            ("listing_status", listing_status),
            ("owner", owner),
            ("status", status),
            ("cursor", cursor),
        ):
            if value:
                params.append((name, value))
        params.extend(("label", label) for label in labels)
        params.extend(("relation", relation) for relation in relations)
        response = self._request("GET", "/catalog/characters", params=params)
        return _parse(response, CatalogCharacterPage)

    @operation("GET", "/catalog/characters/{character_id}", response_model=CatalogCharacter)
    def get_catalog_character(
        self, character_id: str, *, version: str | int | None = None, draft: bool = False
    ) -> CatalogCharacter:
        """``GET /catalog/characters/{id}`` in ``full``: the latest published version, version
        ``version`` or (``draft``) the draft. A never-published entry's latest read is 422
        ``not_published``; a bad ``version`` reaches the Engine as given (its 422)."""
        params = _character_read_params("full", version, draft)
        response = self._request("GET", _character_path(character_id), params=params)
        return _parse(response, CatalogCharacter)

    def get_catalog_character_card(
        self, character_id: str, *, version: str | int | None = None, draft: bool = False
    ) -> CatalogCharacterCard:
        """The same read in ``card`` (the public card only). Not registered on its own: the
        registry keys by method and path, which :meth:`get_catalog_character` holds; the contract
        test checks :class:`CatalogCharacterCard` against ``CharacterCard`` directly."""
        params = _character_read_params("card", version, draft)
        response = self._request("GET", _character_path(character_id), params=params)
        return _parse(response, CatalogCharacterCard)

    @operation(
        "GET", "/catalog/characters/{character_id}/versions", response_model=CatalogVersionList
    )
    def list_catalog_character_versions(self, character_id: str) -> list[CatalogVersion]:
        """``GET /catalog/characters/{id}/versions``: every published version (the Engine lists
        them oldest first; callers sort)."""
        path = f"{_character_path(character_id)}/versions"
        return _parse(self._request("GET", path), CatalogVersionList).items

    # --- catalog character writes (#66) ---------------------------------------------------------

    @operation(
        "POST",
        "/catalog/characters",
        response_model=CatalogCharacterDraft,
        entity="character",
        entity_id="response:id",
    )
    def create_catalog_character(
        self, body: Mapping[str, Any], *, idempotency_key: str
    ) -> CatalogCharacterDraft:
        """``POST /catalog/characters`` with a ``CharacterCreate`` (``public_card``,
        ``definition``, ``metadata``, ``visual_identity``, ``owner``): a new entry whose draft is
        at revision 1."""
        response = self._request(
            "POST",
            "/catalog/characters",
            json=dict(body),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, CatalogCharacterDraft)

    @operation(
        "PATCH",
        "/catalog/characters/{character_id}",
        response_model=CatalogCharacterDraft,
        entity="character",
        entity_id="character_id",
    )
    def patch_catalog_character(
        self,
        character_id: str,
        patch: Mapping[str, Any],
        *,
        draft_revision: int,
        idempotency_key: str,
    ) -> CatalogCharacterDraft:
        """``PATCH /catalog/characters/{id}``: a JSON merge patch over the draft (a key sets that
        field, ``null`` removes it, a list replaces the stored one) based on ``draft_revision``
        (a stale one is 409 ``draft_stale``)."""
        response = self._request(
            "PATCH",
            _character_path(character_id),
            json={**patch, "draft_revision": draft_revision},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, CatalogCharacterDraft)

    @operation(
        "POST",
        "/catalog/characters/{character_id}/publish",
        response_model=CharacterPublished,
        entity="character",
        entity_id="character_id",
    )
    def publish_catalog_character(
        self,
        character_id: str,
        *,
        draft_revision: int,
        companion_draft_revision: int | None = None,
        idempotency_key: str,
    ) -> CharacterPublished:
        """``POST /catalog/characters/{id}/publish``: the draft at ``draft_revision`` as the next
        version, with a companion version made in the same step. Given a
        ``companion_draft_revision``, the companion's draft is published with it
        (``?with_companion=true``, which the Engine requires the revision for); without one the
        companion's latest content is re-pinned. 422 ``publish_invalid`` carries
        ``details.report`` (and ``details.entity``: ``character`` or ``companion``)."""
        body: dict[str, int] = {"draft_revision": draft_revision}
        params: dict[str, str] | None = None
        if companion_draft_revision is not None:
            body["companion_draft_revision"] = companion_draft_revision
            params = {"with_companion": "true"}
        response = self._request(
            "POST",
            f"{_character_path(character_id)}/publish",
            params=params,
            json=body,
            headers={"Idempotency-Key": idempotency_key},
            timeout=CATALOG_PUBLISH,
        )
        return _parse(response, CharacterPublished)

    @operation("GET", "/catalog/characters/{character_id}/companion")
    def get_catalog_character_companion(self, character_id: str) -> CompanionDraft:
        """``GET /catalog/characters/{id}/companion?draft=true&projection=full``: the companion
        scenario's draft, read for its ``draft_revision`` (what a publish ``with_companion``
        names). Registered without a response model: the answer is a whole ``ScenarioFull``
        (#68's model), of which :class:`CompanionDraft` reads a few fields."""
        params = {"draft": "true", "projection": "full"}
        path = f"{_character_path(character_id)}/companion"
        return _parse(self._request("GET", path, params=params), CompanionDraft)

    def get_catalog_character_companion_card(self, character_id: str) -> ScenarioCard:
        """``GET /catalog/characters/{id}/companion?projection=card`` (#74): the companion's
        latest published card, or its draft's while it was never published. Not registered on
        its own (the registry keys by method and path, which
        :meth:`get_catalog_character_companion` holds)."""
        path = f"{_character_path(character_id)}/companion"
        response = self._request("GET", path, params={"projection": "card"})
        return _parse(response, ScenarioCard)

    # --- personas (#67) -------------------------------------------------------------------------

    @operation("GET", "/personas", response_model=PersonaPage)
    def list_personas(
        self,
        *,
        owner: str | None = None,
        relation: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[Persona], str | None]:
        """``GET /personas``: one page of personas, newest first, and the next page's cursor
        (``None`` on the last). ``owner`` narrows to that principal's; ``relation`` is
        ``name:principal`` or ``name:``. A blank filter is not sent."""
        params: list[tuple[str, str]] = [("limit", str(limit or CATALOG_PAGE))]
        for name, value in (("owner", owner), ("relation", relation), ("cursor", cursor)):
            if value:
                params.append((name, value))
        page = _parse(self._request("GET", "/personas", params=params), PersonaPage)
        return page.items, page.next_cursor

    @operation("GET", "/personas/{persona_id}", response_model=Persona)
    def get_persona(self, persona_id: str) -> Persona:
        """``GET /personas/{id}``: the persona, its owner and when it was created and changed."""
        return _parse(self._request("GET", _persona_path(persona_id)), Persona)

    @operation(
        "POST",
        "/personas",
        response_model=PersonaWritten,
        entity="persona",
        entity_id="response:id",
    )
    def create_persona(
        self,
        *,
        name: str,
        age: int,
        description: str,
        owner: str,
        gender: str | None = None,
        idempotency_key: str,
    ) -> PersonaWritten:
        """``POST /personas``: a persona owned by ``owner`` (always sent, #64 convention 7);
        ``gender`` only when given. A name the owner already uses (ignoring case) is 409
        ``persona_name_taken``."""
        body: dict[str, Any] = {"name": name, "age": age, "description": description}
        if gender is not None:
            body["gender"] = gender
        body["owner"] = owner
        response = self._request(
            "POST",
            "/personas",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, PersonaWritten)

    @operation(
        "PATCH",
        "/personas/{persona_id}",
        response_model=PersonaWritten,
        entity="persona",
        entity_id="persona_id",
    )
    def patch_persona(
        self, persona_id: str, changes: Mapping[str, object], *, idempotency_key: str
    ) -> PersonaWritten:
        """``PATCH /personas/{id}``: a JSON merge patch of the changed fields (last write wins);
        ``None`` is sent as ``null``, which only ``gender`` accepts (it removes it)."""
        response = self._request(
            "PATCH",
            _persona_path(persona_id),
            json=dict(changes),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, PersonaWritten)

    @operation("DELETE", "/personas/{persona_id}", entity="persona", entity_id="persona_id")
    def delete_persona(self, persona_id: str, *, idempotency_key: str) -> None:
        """``DELETE /personas/{id}``: the persona and its relations (204; a retry under the same
        key is a 204 too, ``Idempotent-Replay: true``). Stories keep their snapshot."""
        response = self._request(
            "DELETE",
            _persona_path(persona_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    # --- owners and relations (#79) --------------------------------------------------------------
    #
    # One method per operation for all six owned types: the entity type picks the Engine root
    # and the registration (:data:`OWNERSHIP_OPERATIONS`), so the operation log records each
    # write with its own entity (#44) and the contract test checks every route.

    def get_relations(self, entity_type: EntityType, entity_id: str) -> Relations:
        """``GET /{type}/{id}/relations``: the entity's owner and its relations (allowed on a
        companion scenario, whose writes the Engine refuses)."""
        with _ownership_call("get_relations", entity_type, entity_id):
            response = self._request("GET", f"{_ownership_path(entity_type, entity_id)}/relations")
            return _parse(response, Relations)

    def transfer_owner(
        self, entity_type: EntityType, entity_id: str, principal: str, *, idempotency_key: str
    ) -> OwnerTransfer:
        """``PUT /{type}/{id}/owner {principal}``. A transfer to the current owner changes
        nothing (``previous_owner == owner_principal``); a character's companion scenario moves
        with it. The Engine refuses ``system`` (422)."""
        with _ownership_call("transfer_owner", entity_type, entity_id):
            response = self._request(
                "PUT",
                f"{_ownership_path(entity_type, entity_id)}/owner",
                json={"principal": principal},
                headers={"Idempotency-Key": idempotency_key},
                timeout=MUTATION_TIMEOUT_S,
            )
            return _parse(response, OwnerTransfer)

    def add_relation(
        self,
        entity_type: EntityType,
        entity_id: str,
        principal: str,
        relation: str,
        *,
        idempotency_key: str,
    ) -> tuple[RelationRecord, bool]:
        """``POST /{type}/{id}/relations {principal, relation}``: the relation and whether it
        was added (201, ``True``) or already there (200, ``False``: nothing recorded)."""
        with _ownership_call("add_relation", entity_type, entity_id):
            response = self._request(
                "POST",
                f"{_ownership_path(entity_type, entity_id)}/relations",
                json={"principal": principal, "relation": relation},
                headers={"Idempotency-Key": idempotency_key},
                timeout=MUTATION_TIMEOUT_S,
            )
            return _parse(response, RelationRecord), response.status_code == 201

    def remove_relation(
        self,
        entity_type: EntityType,
        entity_id: str,
        relation: str,
        principal: str,
        *,
        idempotency_key: str,
    ) -> None:
        """``DELETE /{type}/{id}/relations/{relation}/{principal}`` (204), both segments
        percent-encoded with nothing safe: a principal may hold ``/``. An absent relation (or
        entity) is :class:`EngineError` 404 ``not_found``."""
        path = (
            f"{_ownership_path(entity_type, entity_id)}/relations/"
            f"{quote(relation, safe='')}/{quote(principal, safe='')}"
        )
        with _ownership_call("remove_relation", entity_type, entity_id):
            response = self._request(
                "DELETE",
                path,
                headers={"Idempotency-Key": idempotency_key},
                timeout=MUTATION_TIMEOUT_S,
            )
            _raise_for_envelope(response)

    # --- rating suggestions (#82) ---------------------------------------------------------------

    def suggest_ratings(
        self,
        entry_type: SuggestionEntity,
        entry_id: str,
        *,
        version: SuggestionVersion,
        include_retired: bool = False,
        timeout: float,
    ) -> RatingSuggestion:
        """``POST /catalog/{characters|scenarios}/{id}/rating-suggestion {version}``: the
        advisory table, one row per rating (``include_retired``: retired ones too, sent as
        ``?include_retired=true``). It writes nothing and makes a model call per rating, so the
        caller passes ``timeout`` (``timeouts.rating_suggestion(n)``); no ``Idempotency-Key`` and
        no ``projection`` (the Engine refuses one, 422). Recorded in the operation log as
        ``rating_suggestion`` (it requires a principal), under the entry. An unknown entry or
        version is :class:`EngineError` 404."""
        op = SUGGESTION_OPERATIONS[entry_type]
        path = f"{REGISTRY[entry_type].engine_root}/{quote(entry_id, safe='')}/rating-suggestion"
        with calling(Call(SUGGESTION_KIND, op, {"entry_id": entry_id})):
            response = self._request(
                "POST",
                path,
                params={"include_retired": "true"} if include_retired else None,
                json={"version": version},
                timeout=timeout,
            )
            return _parse(response, RatingSuggestion)

    # --- message feedback (#83) -----------------------------------------------------------------

    @operation(
        "GET", "/stories/{story_id}/messages/{message_id}/feedback", response_model=MessageFeedback
    )
    def get_message_feedback(self, story_id: str, message_id: str) -> MessageFeedback:
        """``GET /stories/{id}/messages/{message_id}/feedback``: the requesting principal's own
        rating of the message (``rating`` ``None`` when it has none) and its history. An unknown
        message (or the consumer's own narration) is :class:`EngineError` 404. Unrecorded."""
        return _parse(self._request("GET", _feedback_path(story_id, message_id)), MessageFeedback)

    @operation(
        "PUT",
        "/stories/{story_id}/messages/{message_id}/feedback",
        response_model=MessageFeedback,
        recorded=True,
    )
    def put_message_feedback(
        self,
        story_id: str,
        message_id: str,
        *,
        rating: int,
        tags: Sequence[str],
        text: str | None,
    ) -> tuple[MessageFeedback, bool]:
        """``PUT /stories/{id}/messages/{message_id}/feedback {rating, tags, text}``: rate the
        message as the requesting principal. Returns the rating and whether it was new (201);
        200 is a replacement or the same value again. 422 ``unknown_feedback_tag`` (with
        ``details.unknown_tags``) or ``validation_error``; 404 for an unknown message. No
        ``Idempotency-Key`` (the write is idempotent by itself); recorded in the operation log
        although the Engine flags it non-mutating (#64 convention 9)."""
        response = self._request(
            "PUT",
            _feedback_path(story_id, message_id),
            json={"rating": rating, "tags": list(tags), "text": text},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, MessageFeedback), response.status_code == 201

    @operation("DELETE", "/stories/{story_id}/messages/{message_id}/feedback", recorded=True)
    def delete_message_feedback(self, story_id: str, message_id: str) -> None:
        """``DELETE /stories/{id}/messages/{message_id}/feedback``: retract the requesting
        principal's rating (it stays in the history). 204 also when there was none; 404 for an
        unknown message. No ``Idempotency-Key``; recorded."""
        response = self._request(
            "DELETE", _feedback_path(story_id, message_id), timeout=MUTATION_TIMEOUT_S
        )
        _raise_for_envelope(response)

    # --- principal lookup (#76) -----------------------------------------------------------------

    @operation("GET", "/principals/{principal_id}")
    def lookup_principal(
        self,
        principal: str,
        cursors: Mapping[str, str] | None = None,
        limit: int = PRINCIPAL_PAGE,
    ) -> PrincipalLinks:
        """``GET /principals/{principal}``: everything the principal owns and is related to, one
        page per type. ``cursors`` continues pages: keys are the Engine's cursor names without
        ``cursor[...]`` (``stories``, ``related.media``); a blank one is not sent. The principal
        travels in the path, percent-encoded whole (it may hold ``/``). Registered without a
        response model: :class:`PrincipalLinks` reads the answer by hand."""
        params: list[tuple[str, str]] = [("limit", str(limit))]
        for key, value in (cursors or {}).items():
            if value:
                params.append((f"cursor[{key}]", value))
        response = self._request("GET", f"/principals/{quote(principal, safe='')}", params=params)
        return _parse(response, PrincipalLinks)

    # --- offboarding (#77) -----------------------------------------------------------------------

    @operation(
        "POST",
        "/admin/offboarding",
        response_model=OffboardingPlan,
        status_models={202: OffboardingQueued},
        entity="principal",
        entity_id="principal",
    )
    def offboard(
        self,
        principal: str,
        actions: OffboardingActions,
        transfer_to: str | None,
        *,
        dry_run: bool,
        ignore_warnings: bool,
        idempotency_key: str,
    ) -> OffboardingPlan | OffboardingQueued:
        """``POST /admin/offboarding``: a dry run, or the start of an offboarding job.

        ``actions`` is sent whole (all seven keys); ``transfer_to`` only when given. A dry run,
        or a start its warnings held (no ``ignore_warnings``), is :class:`OffboardingPlan` (200,
        ``applied`` false); a started job is :class:`OffboardingQueued` (202, the operation log
        records it ``queued`` with its ``job_id``). Recorded on the principal (``entity_type
        "principal"``). Waits :data:`~console.engine.timeouts.OFFBOARDING`."""
        body: dict[str, Any] = {"principal": principal, "actions": actions.to_json()}
        if transfer_to is not None:
            body["transfer_to"] = transfer_to
        body["dry_run"] = dry_run
        body["ignore_warnings"] = ignore_warnings
        response = self._request(
            "POST",
            "/admin/offboarding",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
            timeout=OFFBOARDING,
        )
        if response.status_code == 202:
            return _parse(response, OffboardingQueued)
        return _parse(response, OffboardingPlan)

    @operation("GET", "/admin/offboarding/{job_id}", response_model=OffboardingJob)
    def get_offboarding(self, job_id: str) -> OffboardingJob:
        """``GET /admin/offboarding/{job_id}``: the job's status, progress and (once it ends)
        report; 404 ``not_found`` once the report's 90 days are up. Unrecorded."""
        response = self._request("GET", f"/admin/offboarding/{quote(job_id, safe='')}")
        return _parse(response, OffboardingJob)

    # --- erasure (#86) --------------------------------------------------------------------------

    @operation(
        "POST",
        "/admin/erasure",
        response_model=ErasureResult,
        recorded=True,
        record_response=erasure_record_response,
        record_about=erasure_about,
    )
    def erase(
        self,
        subject: SubjectRef,
        scope: StoryScope | CatalogScope,
        *,
        dry_run: bool,
        confirm_candidates: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> ErasureResult:
        """``POST /admin/erasure``: erase ``subject`` within ``scope``, or (``dry_run``) report
        what would go, its candidate mentions with their snippets included.

        ``confirm_candidates`` (the ticked candidate ids) is sent only when non-empty; the
        ``Idempotency-Key`` only when given (a dry run sends none, the Engine keeps no key for
        one). Recorded either way (#64 convention 9: the dry run is a non-writing POST that
        needs a principal), on the subject (``entity_type`` its kind, :func:`erasure_about`)
        and the scope's story, with every candidate's snippet stored as ``"***"``. Waits
        :data:`~console.engine.timeouts.ERASURE`."""
        body: dict[str, Any] = {
            "subject": subject.to_json(),
            "scope": scope.to_json(),
            "dry_run": dry_run,
        }
        if confirm_candidates:
            body["confirm_candidates"] = list(confirm_candidates)
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        response = self._request(
            "POST", "/admin/erasure", json=body, headers=headers, timeout=ERASURE
        )
        return _parse(response, ErasureResult)

    # --- the erasure ledger (#87) ---------------------------------------------------------------

    @operation("GET", "/admin/erasure/status", response_model=ErasureStatus)
    def get_erasure_status(self) -> ErasureStatus:
        """``GET /admin/erasure/status``: what is pending re-application after a restore, the
        applied count, whether a reapply is running and the last run's time, error and
        attempts."""
        return _parse(self._request("GET", "/admin/erasure/status"), ErasureStatus)

    @operation("GET", "/admin/erasure/boundary", response_model=ErasureBoundary)
    def get_erasure_boundary(self) -> ErasureBoundary:
        """``GET /admin/erasure/boundary``: what an erasure covers and cannot reach."""
        return _parse(self._request("GET", "/admin/erasure/boundary"), ErasureBoundary)

    @operation(
        "POST",
        "/admin/erasure/reconcile",
        response_model=ReconcileResult,
        recorded=True,
        ended=True,
    )
    def reconcile_erasures(self) -> ReconcileResult:
        """``POST /admin/erasure/reconcile``: re-apply every pending erasure now. It answers
        (202) when the run has ended; ``ran`` is ``False`` when another process was running it.
        No ``Idempotency-Key`` (re-running is harmless); recorded (#64 convention 9, a
        principal-requiring POST that writes no story content of its own). Waits
        :data:`~console.engine.timeouts.RECONCILE`."""
        response = self._request("POST", "/admin/erasure/reconcile", timeout=RECONCILE)
        return _parse(response, ReconcileResult)

    # --- catalog scenarios (#68) ----------------------------------------------------------------

    @operation("GET", "/catalog/scenarios", response_model=ScenarioPage)
    def list_scenarios(
        self,
        *,
        q: str | None = None,
        labels: Sequence[str] = (),
        content_rating: str | None = None,
        listing_status: str | None = None,
        owner: str | None = None,
        relations: Sequence[str] = (),
        status: str | None = None,
        include_companions: bool = False,
        cursor: str | None = None,
        limit: int = CATALOG_PAGE,
    ) -> tuple[list[ScenarioListItem], str | None]:
        """``GET /catalog/scenarios``: one page of cards and the next page's cursor (``None`` on
        the last). Filters as :meth:`list_catalog_characters`; companion scenarios are listed
        only with ``include_companions`` (sent as ``include_companions=true``)."""
        params: list[tuple[str, str]] = [("limit", str(limit))]
        for name, value in (
            ("q", q),
            ("content_rating", content_rating),
            ("listing_status", listing_status),
            ("owner", owner),
            ("status", status),
            ("cursor", cursor),
        ):
            if value:
                params.append((name, value))
        params.extend(("label", label) for label in labels)
        params.extend(("relation", relation) for relation in relations)
        if include_companions:
            params.append(("include_companions", "true"))
        page = _parse(self._request("GET", "/catalog/scenarios", params=params), ScenarioPage)
        return page.items, page.next_cursor

    @operation("GET", "/catalog/scenarios/{scenario_id}", response_model=Scenario)
    def get_scenario(
        self, scenario_id: str, *, version: str | int | None = None, draft: bool = False
    ) -> Scenario:
        """``GET /catalog/scenarios/{id}`` in ``full``: the latest published version, version
        ``version`` or (``draft``) the draft. A never-published entry's latest read is 422
        ``not_published``."""
        params = _character_read_params("full", version, draft)
        response = self._request("GET", _scenario_path(scenario_id), params=params)
        return _parse(response, Scenario)

    def get_scenario_card(
        self, scenario_id: str, *, version: str | int | None = None, draft: bool = False
    ) -> ScenarioCard:
        """The same read in ``card``. Not registered on its own (the registry keys by method and
        path, which :meth:`get_scenario` holds); the contract test checks :class:`ScenarioCard`
        against ``ScenarioCard`` directly."""
        params = _character_read_params("card", version, draft)
        response = self._request("GET", _scenario_path(scenario_id), params=params)
        return _parse(response, ScenarioCard)

    @operation(
        "GET", "/catalog/scenarios/{scenario_id}/versions", response_model=CatalogVersionList
    )
    def list_scenario_versions(self, scenario_id: str) -> list[CatalogVersion]:
        """``GET /catalog/scenarios/{id}/versions``: every published version (oldest first)."""
        path = f"{_scenario_path(scenario_id)}/versions"
        return _parse(self._request("GET", path), CatalogVersionList).items

    @operation(
        "POST",
        "/catalog/scenarios",
        response_model=ScenarioDraft,
        entity="scenario",
        entity_id="response:id",
    )
    def create_scenario(self, body: Mapping[str, Any], *, idempotency_key: str) -> ScenarioDraft:
        """``POST /catalog/scenarios`` with a ``ScenarioCreate``: a new entry whose draft is at
        revision 1."""
        response = self._request(
            "POST",
            "/catalog/scenarios",
            json=dict(body),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ScenarioDraft)

    @operation(
        "PATCH",
        "/catalog/scenarios/{scenario_id}",
        response_model=ScenarioDraft,
        entity="scenario",
        entity_id="scenario_id",
    )
    def patch_scenario(
        self,
        scenario_id: str,
        patch: Mapping[str, Any],
        *,
        draft_revision: int,
        idempotency_key: str,
    ) -> ScenarioDraft:
        """``PATCH /catalog/scenarios/{id}``: a JSON merge patch over the draft's groups based on
        ``draft_revision`` (a stale one is 409 ``draft_stale``; an archived entry is 422
        ``entry_archived``)."""
        response = self._request(
            "PATCH",
            _scenario_path(scenario_id),
            json={**patch, "draft_revision": draft_revision},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ScenarioDraft)

    @operation(
        "POST",
        "/catalog/scenarios/{scenario_id}/validate",
        response_model=ValidationResult,
        recorded=True,
        entity="scenario",
        entity_id="scenario_id",
    )
    def validate_scenario(
        self,
        scenario_id: str,
        *,
        draft_revision: int | None = None,
        version: int | None = None,
    ) -> ValidationResult:
        """``POST /catalog/scenarios/{id}/validate`` (#74): the checks a publish runs, as the
        catalog stands now, writing nothing (no ``Idempotency-Key``). The draft by default
        (``draft_revision``, when given, must still be the draft's: else 409 ``draft_stale``);
        published version ``version`` instead (``?version=``, no revision sent). Recorded in
        the operation log: it requires a principal."""
        body: dict[str, int] = {}
        if draft_revision is not None and version is None:
            body["draft_revision"] = draft_revision
        response = self._request(
            "POST",
            f"{_scenario_path(scenario_id)}/validate",
            params={"version": str(version)} if version is not None else None,
            json=body,
            timeout=CATALOG_PUBLISH,
        )
        return _parse(response, ValidationResult)

    @operation(
        "POST",
        "/catalog/scenarios/{scenario_id}/publish",
        response_model=ScenarioPublished,
        entity="scenario",
        entity_id="scenario_id",
    )
    def publish_scenario(
        self, scenario_id: str, *, draft_revision: int, idempotency_key: str
    ) -> ScenarioPublished:
        """``POST /catalog/scenarios/{id}/publish`` (#74): the draft at ``draft_revision`` as the
        next version. Any validation error is 422 ``publish_invalid`` with ``details.report``; a
        companion published alone is pinned to its character's latest published version."""
        response = self._request(
            "POST",
            f"{_scenario_path(scenario_id)}/publish",
            json={"draft_revision": draft_revision},
            headers={"Idempotency-Key": idempotency_key},
            timeout=CATALOG_PUBLISH,
        )
        return _parse(response, ScenarioPublished)

    # --- catalog lifecycle and listing status (#69) ---------------------------------------------
    #
    # Archive makes an entry readable but not editable or publishable; unarchive restores
    # ``published`` (``draft`` if never published); both answer the entry unchanged when there
    # is nothing to change. Delete removes every version. A character's companion follows it in
    # all three and in its listing status; the companion alone is refused (422
    # ``companion_follows_character``).

    @operation(
        "POST",
        "/catalog/characters/{character_id}/archive",
        response_model=CatalogCharacterStatus,
        entity="character",
        entity_id="character_id",
    )
    def archive_catalog_character(
        self, character_id: str, *, idempotency_key: str
    ) -> CatalogCharacterStatus:
        """``POST /catalog/characters/{id}/archive`` (its companion is archived with it)."""
        path = f"{_character_path(character_id)}/archive"
        return _parse(self._post_empty(path, idempotency_key), CatalogCharacterStatus)

    @operation(
        "POST",
        "/catalog/characters/{character_id}/unarchive",
        response_model=CatalogCharacterStatus,
        entity="character",
        entity_id="character_id",
    )
    def unarchive_catalog_character(
        self, character_id: str, *, idempotency_key: str
    ) -> CatalogCharacterStatus:
        """``POST /catalog/characters/{id}/unarchive`` (its companion is restored with it)."""
        path = f"{_character_path(character_id)}/unarchive"
        return _parse(self._post_empty(path, idempotency_key), CatalogCharacterStatus)

    @operation(
        "DELETE",
        "/catalog/characters/{character_id}",
        entity="character",
        entity_id="character_id",
    )
    def delete_catalog_character(self, character_id: str, *, idempotency_key: str) -> None:
        """``DELETE /catalog/characters/{id}``: the character, every version and its companion
        (204). Stories created from it keep their snapshot."""
        response = self._request(
            "DELETE",
            _character_path(character_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    @operation(
        "PUT",
        "/catalog/characters/{character_id}/listing-status",
        response_model=ListingStatus,
        entity="character",
        entity_id="character_id",
    )
    def set_catalog_character_listing_status(
        self, character_id: str, value: str, *, idempotency_key: str
    ) -> ListingStatus:
        """``PUT /catalog/characters/{id}/listing-status {value}``: one of the deployment's
        ``listing_status_values`` (422 ``invalid_listing_status`` otherwise); its companion
        follows."""
        path = f"{_character_path(character_id)}/listing-status"
        return _parse(self._put_value(path, value, idempotency_key), ListingStatus)

    @operation(
        "POST",
        "/catalog/scenarios/{scenario_id}/archive",
        response_model=ScenarioStatus,
        entity="scenario",
        entity_id="scenario_id",
    )
    def archive_scenario(self, scenario_id: str, *, idempotency_key: str) -> ScenarioStatus:
        """``POST /catalog/scenarios/{id}/archive``."""
        path = f"{_scenario_path(scenario_id)}/archive"
        return _parse(self._post_empty(path, idempotency_key), ScenarioStatus)

    @operation(
        "POST",
        "/catalog/scenarios/{scenario_id}/unarchive",
        response_model=ScenarioStatus,
        entity="scenario",
        entity_id="scenario_id",
    )
    def unarchive_scenario(self, scenario_id: str, *, idempotency_key: str) -> ScenarioStatus:
        """``POST /catalog/scenarios/{id}/unarchive``."""
        path = f"{_scenario_path(scenario_id)}/unarchive"
        return _parse(self._post_empty(path, idempotency_key), ScenarioStatus)

    @operation(
        "DELETE",
        "/catalog/scenarios/{scenario_id}",
        entity="scenario",
        entity_id="scenario_id",
    )
    def delete_scenario(self, scenario_id: str, *, idempotency_key: str) -> None:
        """``DELETE /catalog/scenarios/{id}``: the scenario and every version (204)."""
        response = self._request(
            "DELETE",
            _scenario_path(scenario_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    @operation(
        "PUT",
        "/catalog/scenarios/{scenario_id}/listing-status",
        response_model=ListingStatus,
        entity="scenario",
        entity_id="scenario_id",
    )
    def set_scenario_listing_status(
        self, scenario_id: str, value: str, *, idempotency_key: str
    ) -> ListingStatus:
        """``PUT /catalog/scenarios/{id}/listing-status {value}``."""
        path = f"{_scenario_path(scenario_id)}/listing-status"
        return _parse(self._put_value(path, value, idempotency_key), ListingStatus)

    def _post_empty(self, path: str, idempotency_key: str) -> EngineResponse:
        return self._request(
            "POST",
            path,
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )

    def _put_value(self, path: str, value: str, idempotency_key: str) -> EngineResponse:
        return self._request(
            "PUT",
            path,
            json={"value": value},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )

    # --- engine profiles (#68's select; #78's pages) ---------------------------------------------

    @operation("GET", "/engine-profiles", response_model=EngineProfilePage)
    def list_engine_profiles(
        self,
        *,
        owner: str | None = None,
        relation: Sequence[str] = (),
        include_deleted: bool = False,
        cursor: str | None = None,
        limit: int = CATALOG_PAGE,
    ) -> tuple[list[EngineProfile], str | None]:
        """``GET /engine-profiles``: one page of profiles (newest first) and the next cursor.
        ``owner`` narrows to that principal's; each ``relation`` is ``name:principal`` or
        ``name:``. A blank filter is not sent."""
        params: list[tuple[str, str]] = [("limit", str(limit))]
        if owner:
            params.append(("owner", owner))
        params.extend(("relation", value) for value in relation if value)
        if include_deleted:
            params.append(("include_deleted", "true"))
        if cursor:
            params.append(("cursor", cursor))
        response = self._request("GET", "/engine-profiles", params=params)
        page = _parse(response, EngineProfilePage)
        return page.items, page.next_cursor

    @operation("GET", "/engine-profiles/schema", response_model=ProfileSchema)
    def get_engine_profile_schema(self) -> ProfileSchema:
        """``GET /engine-profiles/schema``: the delivered settings schema, each setting's tags,
        the delivered modules and the routable model purposes (#78; pages read it through
        ``console.profile_schema.get_profile_schema_cached``)."""
        return _parse(self._request("GET", "/engine-profiles/schema"), ProfileSchema)

    @operation("GET", "/engine-profiles/{profile_id}", response_model=EngineProfile)
    def get_engine_profile(self, profile_id: str, version: int | None = None) -> EngineProfile:
        """``GET /engine-profiles/{id}``: the profile at its latest version, or at ``version``
        (404 when that version does not exist)."""
        params = {"version": str(version)} if version is not None else None
        return _parse(self._request("GET", _profile_path(profile_id), params=params), EngineProfile)

    @operation("GET", "/engine-profiles/{profile_id}/versions", response_model=ProfileVersionList)
    def list_engine_profile_versions(self, profile_id: str) -> list[ProfileVersion]:
        """``GET /engine-profiles/{id}/versions``: every version, in the Engine's order (oldest
        first)."""
        path = f"{_profile_path(profile_id)}/versions"
        return _parse(self._request("GET", path), ProfileVersionList).items

    @operation(
        "POST",
        "/engine-profiles/{profile_id}/validate",
        response_model=ProfileValidation,
        recorded=True,
        entity="engine_profile",
        entity_id="profile_id",
    )
    def validate_engine_profile(
        self, profile_id: str, settings: Mapping[str, Any]
    ) -> ProfileValidation:
        """``POST /engine-profiles/{id}/validate`` with ``settings`` (a dry run of an edit; the
        Engine validates the body's settings whatever profile the path names, #84 posts a
        create's to the deployment default). Recorded (#64 convention 9), no idempotency key
        (convention 19). A 422 ``invalid_profile`` is answered as ``valid=False`` with the
        ``details`` lists; any other refusal is :class:`EngineError`."""
        response = self._request(
            "POST",
            f"{_profile_path(profile_id)}/validate",
            json={"settings": dict(settings)},
            timeout=MUTATION_TIMEOUT_S,
        )
        if response.status_code == 422:
            body = _json_or_none(response)
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict) and error.get("code") == INVALID_PROFILE:
                details = error.get("details")
                details = details if isinstance(details, dict) else {}
                found = ProfileValidation.from_json(
                    {
                        "valid": False,
                        "missing_dependencies": details.get("missing_dependencies") or [],
                        "undelivered": details.get("undelivered") or [],
                        "warnings": [],
                    }
                )
                if found is not None:
                    return found
        return _parse(response, ProfileValidation)

    @operation(
        "POST",
        "/engine-profiles",
        response_model=ProfileWritten,
        entity="engine_profile",
        entity_id="response:id",
    )
    def create_engine_profile(
        self, *, name: str, settings: Mapping[str, Any], owner: str, idempotency_key: str
    ) -> ProfileWritten:
        """``POST /engine-profiles``: a profile owned by ``owner`` (always sent, #64 convention
        7). Settings that cannot run are 422 ``invalid_profile`` (``details.undelivered``,
        ``details.missing_dependencies``)."""
        response = self._request(
            "POST",
            "/engine-profiles",
            json={"name": name, "settings": dict(settings), "owner": owner},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ProfileWritten)

    @operation(
        "PUT",
        "/engine-profiles/{profile_id}",
        response_model=ProfileEdit,
        entity="engine_profile",
        entity_id="profile_id",
    )
    def update_engine_profile(
        self,
        profile_id: str,
        *,
        name: str,
        settings: Mapping[str, Any],
        ignore_warnings: bool,
        idempotency_key: str,
    ) -> ProfileEdit:
        """``PUT /engine-profiles/{id}``: name and settings replaced in full (#84). ``applied``
        false: the impact report warns and nothing was written; resend with
        ``ignore_warnings`` to apply. A deleted profile is 422 ``profile_deleted``; settings
        that cannot run, or would leave a following story's overrides unable to, are 422
        ``invalid_profile`` (``details.stories``). Waits :data:`PROFILE_EDIT` (the impact covers
        up to 1,000 stories)."""
        response = self._request(
            "PUT",
            _profile_path(profile_id),
            json={"name": name, "settings": dict(settings), "ignore_warnings": ignore_warnings},
            headers={"Idempotency-Key": idempotency_key},
            timeout=PROFILE_EDIT,
        )
        return _parse(response, ProfileEdit)

    @operation(
        "POST",
        "/engine-profiles/{profile_id}/copy",
        response_model=ProfileWritten,
        entity="engine_profile",
        entity_id="response:id|profile_id",
    )
    def copy_engine_profile(
        self,
        profile_id: str,
        *,
        name: str,
        owner: str,
        version: int | None = None,
        idempotency_key: str,
    ) -> ProfileWritten:
        """``POST /engine-profiles/{id}/copy`` (``?version=N``: copy that version, else the
        latest): a new profile, version 1, owned by ``owner``. A deleted profile can be copied.
        The operation log names the copy (the source when the copy failed)."""
        params = {"version": str(version)} if version is not None else None
        response = self._request(
            "POST",
            f"{_profile_path(profile_id)}/copy",
            params=params,
            json={"name": name, "owner": owner},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ProfileWritten)

    @operation(
        "DELETE",
        "/engine-profiles/{profile_id}",
        response_model=ProfileDeleted,
        entity="engine_profile",
        entity_id="profile_id",
    )
    def delete_engine_profile(self, profile_id: str, *, idempotency_key: str) -> ProfileDeleted:
        """``DELETE /engine-profiles/{id}``: the profile leaves listings and assignment; the
        answer warns of the stories still using it and the scenarios recommending it. The
        deployment default is 409 (``details.reason`` ``is_default``)."""
        response = self._request(
            "DELETE",
            _profile_path(profile_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ProfileDeleted)

    # --- a story's effective configuration (#85) ------------------------------------------------

    @operation("GET", "/stories/{story_id}/effective-config", response_model=EffectiveConfig)
    def get_effective_config(self, story_id: str) -> EffectiveConfig:
        """``GET /stories/{id}/effective-config``: the deployment's defaults, the profile and
        version the story runs on, its overrides, every setting's effective value, the
        fingerprint, capabilities and quality status."""
        path = f"{_story_path(story_id)}/effective-config"
        return _parse(self._request("GET", path), EffectiveConfig)

    @operation(
        "POST",
        "/stories/{story_id}/impact-preview",
        response_model=ImpactReport,
        recorded=True,
    )
    def impact_preview(
        self,
        story_id: str,
        *,
        profile_id: str | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> ImpactReport:
        """``POST /stories/{id}/impact-preview``: what assigning ``profile_id`` and/or applying
        ``overrides`` (a merge patch over the story's overrides) would do, writing nothing.
        Recorded (#64 convention 9), no idempotency key (convention 19). 422
        ``profile_deleted``, ``invalid_profile`` or ``validation_error`` as the write would."""
        body: dict[str, Any] = {}
        if profile_id is not None:
            body["profile_id"] = profile_id
        if overrides is not None:
            body["overrides"] = dict(overrides)
        response = self._request(
            "POST",
            f"{_story_path(story_id)}/impact-preview",
            json=body,
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, ImpactReport)

    @operation(
        "PATCH",
        "/stories/{story_id}/profile-overrides",
        response_model=StoryProfileWrite,
    )
    def patch_profile_overrides(
        self,
        story_id: str,
        settings: Mapping[str, Any],
        *,
        expected_revision: int | None,
        ignore_warnings: bool,
        idempotency_key: str,
    ) -> StoryProfileWrite:
        """``PATCH /stories/{id}/profile-overrides``: a JSON merge patch over the story's
        overrides (``null`` removes one). ``expected_revision`` goes in the body (no
        ``X-Epistrel-Expected-Revision``): another revision is 409 ``revision_conflict``
        (``details.reason`` ``overrides_revision``). Two stages: ``applied`` false holds the
        change for ``ignore_warnings``."""
        body: dict[str, Any] = {"settings": dict(settings), "ignore_warnings": ignore_warnings}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        response = self._request(
            "PATCH",
            f"{_story_path(story_id)}/profile-overrides",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, StoryProfileWrite)

    @operation(
        "PUT",
        "/stories/{story_id}/engine-profile",
        response_model=StoryProfileWrite,
    )
    def assign_engine_profile(
        self,
        story_id: str,
        profile_id: str,
        *,
        ignore_warnings: bool,
        idempotency_key: str,
    ) -> StoryProfileWrite:
        """``PUT /stories/{id}/engine-profile``: run the story under ``profile_id`` at its latest
        version from the next turn, keeping the overrides. 422 ``invalid_profile``
        (``details.overrides``) or ``profile_deleted``; the story's own profile is a 200 that
        changes nothing. Two stages, as :meth:`patch_profile_overrides`."""
        response = self._request(
            "PUT",
            f"{_story_path(story_id)}/engine-profile",
            json={"profile_id": profile_id, "ignore_warnings": ignore_warnings},
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        return _parse(response, StoryProfileWrite)

    # --- media (#70) ----------------------------------------------------------------------------

    @operation(
        "POST",
        "/media",
        response_model=MediaUploaded,
        entity="media",
        entity_id="response:id",
        record_body=upload_record,
    )
    def upload_media(
        self,
        metadata: MediaUploadRequest,
        file: UploadPart,
        poster_file: UploadPart | None = None,
        *,
        idempotency_key: str,
    ) -> MediaUploaded:
        """``POST /media`` as ``multipart/form-data``: the ``metadata`` JSON part, the ``file``
        and, for a video, an optional ``poster_file``, in that order, streamed from the files.
        60 s. Identical bytes and metadata answer the existing asset (201 as well)."""
        payload = json.dumps(metadata.to_json(), ensure_ascii=False).encode("utf-8")
        parts: MultipartParts = [
            ("metadata", (None, payload, "application/json")),
            ("file", file.part()),
        ]
        if poster_file is not None:
            parts.append(("poster_file", poster_file.part()))
        response = self._request(
            "POST",
            "/media",
            files=parts,
            headers={"Idempotency-Key": idempotency_key},
            timeout=UPLOAD,
        )
        return _parse(response, MediaUploaded)

    @operation("GET", "/media", response_model=MediaPage)
    def list_media(
        self,
        *,
        kind: str | None = None,
        owner: str | None = None,
        relations: Sequence[str] = (),
        state: str | None = None,
        cursor: str | None = None,
        limit: int = CATALOG_PAGE,
    ) -> tuple[list[MediaAsset], str | None]:
        """``GET /media``: one page of assets, newest first, and the next cursor. ``state`` is
        ``live`` (the Engine's default, not sent) or ``retired``; ``relations`` are sent as
        repeated parameters. A blank filter is not sent."""
        params: list[tuple[str, str]] = [("limit", str(limit))]
        for name, value in (("kind", kind), ("owner", owner), ("state", state), ("cursor", cursor)):
            if value:
                params.append((name, value))
        params.extend(("relation", relation) for relation in relations)
        page = _parse(self._request("GET", "/media", params=params), MediaPage)
        return page.items, page.next_cursor

    @operation("GET", "/media/{media_id}", response_model=MediaAsset)
    def get_media(self, media_id: str) -> MediaAsset:
        """``GET /media/{id}``: the asset's metadata (a retired one too)."""
        return _parse(self._request("GET", _media_path(media_id)), MediaAsset)

    @operation("DELETE", "/media/{media_id}", entity="media", entity_id="media_id")
    def retire_media(self, media_id: str, *, idempotency_key: str) -> None:
        """``DELETE /media/{id}``: retire the asset (and a video's poster). 204, and a 204 no-op
        on an asset already retired."""
        response = self._request(
            "DELETE",
            _media_path(media_id),
            headers={"Idempotency-Key": idempotency_key},
            timeout=MUTATION_TIMEOUT_S,
        )
        _raise_for_envelope(response)

    # --- media content (#65's proxy) -------------------------------------------------------------

    @operation("GET", "/media/{media_id}/content")
    @contextmanager
    def open_media_content(
        self, media_id: str, headers: Mapping[str, str]
    ) -> Iterator[StreamedContent]:
        """``GET /media/{id}/content``, streamed: a context manager yielding the answer, whatever
        its status, with the body unread. Only :data:`MEDIA_REQUEST_HEADERS` of ``headers`` are
        forwarded; the service key goes as on every call. Connect 3 s, then 30 s per chunk.
        The response and its connection close when the block exits. Never recorded (a read).
        No answer is :class:`EngineUnreachable`."""
        wanted = {name.lower() for name in MEDIA_REQUEST_HEADERS}
        forwarded = {k: v for k, v in headers.items() if k.lower() in wanted}
        path = f"/media/{quote(media_id, safe='')}/content"
        wire = self._wire_headers("GET", path, None, forwarded, accept="*/*")
        http = self._http(httpx.Timeout(MEDIA_READ_TIMEOUT_S, connect=MEDIA_CONNECT_TIMEOUT_S))
        try:
            try:
                request = http.build_request("GET", f"{self.base_url}{path}", headers=wire)
                response = http.send(request, stream=True)
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
            try:
                kept = {
                    name: response.headers[name]
                    for name in MEDIA_RESPONSE_HEADERS
                    if name in response.headers
                }
                yield StreamedContent(response.status_code, kept, response)
            finally:
                response.close()
        finally:
            http.close()


# --- owners and relations (#79) -------------------------------------------------------------------

# Each owned type's id parameter, as the Engine's paths name it.
OWNERSHIP_ID_PARAMS: dict[EntityType, str] = {
    "story": "story_id",
    "character": "character_id",
    "scenario": "scenario_id",
    "persona": "persona_id",
    "media": "media_id",
    "engine_profile": "profile_id",
}
# The ownership client methods: their HTTP method, path suffix and answer model.
_OWNERSHIP_ROUTES: dict[str, tuple[str, str, type[Model] | None]] = {
    "get_relations": ("GET", "/relations", Relations),
    "transfer_owner": ("PUT", "/owner", OwnerTransfer),
    "add_relation": ("POST", "/relations", RelationRecord),
    "remove_relation": ("DELETE", "/relations/{relation}/{principal}", None),
}


def _ownership_operations() -> dict[tuple[str, EntityType], Operation]:
    """One registration per method and type (24), each listed in :data:`OPERATIONS` and naming
    its entity, whose id the call's ``entity_id`` argument holds."""
    found: dict[tuple[str, EntityType], Operation] = {}
    for kind, (method, suffix, model) in _OWNERSHIP_ROUTES.items():
        for entity_type, param in OWNERSHIP_ID_PARAMS.items():
            path = f"{REGISTRY[entity_type].engine_root}/{{{param}}}{suffix}"
            op = Operation(method, path, model, False, entity=entity_type, entity_id="entity_id")
            OPERATIONS[f"{method} {path}"] = op
            found[(kind, entity_type)] = op
    return found


OWNERSHIP_OPERATIONS = _ownership_operations()


# #82: the rating suggestion's two registrations (one per catalog entry type), each recorded
# (a principal-requiring POST that writes nothing) under its entry, as kind ``rating_suggestion``.
SuggestionEntity = Literal["character", "scenario"]
SUGGESTION_KIND = "rating_suggestion"
SUGGESTION_PARAMS: dict[SuggestionEntity, str] = {
    "character": "character_id",
    "scenario": "scenario_id",
}


def _suggestion_operations() -> dict[SuggestionEntity, Operation]:
    found: dict[SuggestionEntity, Operation] = {}
    for entity_type, param in SUGGESTION_PARAMS.items():
        path = f"{REGISTRY[entity_type].engine_root}/{{{param}}}/rating-suggestion"
        op = Operation(
            "POST",
            path,
            RatingSuggestion,
            False,
            recorded=True,
            entity=entity_type,
            entity_id="entry_id",
        )
        OPERATIONS[f"POST {path}"] = op
        found[entity_type] = op
    return found


SUGGESTION_OPERATIONS = _suggestion_operations()


def _ownership_call(
    kind: str, entity_type: EntityType, entity_id: str
) -> AbstractContextManager[None]:
    """Mark the request as ``kind`` on that type's registration (what ``@operation`` does)."""
    op = OWNERSHIP_OPERATIONS[(kind, entity_type)]
    return calling(Call(kind, op, {"entity_type": entity_type, "entity_id": entity_id}))


def _ownership_path(entity_type: EntityType, entity_id: str) -> str:
    """``<type's Engine root>/<id>``, the id percent-encoded."""
    return f"{REGISTRY[entity_type].engine_root}/{quote(entity_id, safe='')}"


def _character_read_params(
    projection: str, version: str | int | None, draft: bool
) -> dict[str, str]:
    """A character read's query: ``projection``, and ``draft=true`` or ``version`` (a draft
    read wins: the Engine refuses both)."""
    params = {"projection": projection}
    if draft:
        params["draft"] = "true"
    elif version is not None and version != "":
        params["version"] = str(version)
    return params


def _character_path(character_id: str) -> str:
    """``/catalog/characters/<id>``, the id percent-encoded."""
    return f"/catalog/characters/{quote(character_id, safe='')}"


def _scenario_path(scenario_id: str) -> str:
    """``/catalog/scenarios/<id>``, the id percent-encoded."""
    return f"/catalog/scenarios/{quote(scenario_id, safe='')}"


def _persona_path(persona_id: str) -> str:
    """``/personas/<id>``, the id percent-encoded."""
    return f"/personas/{quote(persona_id, safe='')}"


def _profile_path(profile_id: str) -> str:
    """``/engine-profiles/<id>``, the id percent-encoded."""
    return f"/engine-profiles/{quote(profile_id, safe='')}"


def _media_path(media_id: str) -> str:
    """``/media/<id>``, the id percent-encoded."""
    return f"/media/{quote(media_id, safe='')}"


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


def _feedback_path(story_id: str, message_id: str) -> str:
    """``/stories/<id>/messages/<message_id>/feedback``, both ids percent-encoded."""
    return f"{_story_path(story_id)}/messages/{quote(message_id, safe='')}/feedback"


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
