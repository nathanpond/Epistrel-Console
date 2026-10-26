"""Models (#46): every model purpose the Engine lists, which model each uses and where each value
comes from; editing a purpose (``PUT``), dropping its override, and setting or clearing its API
key.

``embedding`` is a model port (#59): its own row only, so its form is base URL, model and
``timeout`` with a blank field unset, and it has no override to drop (the drop URL answers 404
without calling the Engine). A save that names a different model re-embeds every story, so it
goes through #134's confirmed mode first.

Every action redirects to ``/models/``, except a confirmed embedding model change, which lands
on the re-embed run page (#150: ``/models/embedding/reembed/``, its stories' progress and a
confirmed **Retry failed**). The edit page carries the purpose as it was read in a
signed hidden field, so a re-render after the Console refuses input makes no Engine call. The
API key is only ever sent in the ``PUT …/api-key`` body: the form never renders it back, flashes
do not name it, and the operation log redacts it (#44).
"""

from dataclasses import asdict
from typing import Any

from django.contrib import messages
from django.core import signing
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from console.confirm import (
    ConfirmPage,
    confirm_action,
    posted_idempotency_key,
    safe_next,
)
from console.engine.client import EngineClientError, EngineError, EngineUnreachable
from console.engine.models import ModelConfigItem
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, is_htmx, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms import STOP_MAX, ApiKeyForm, EmbeddingConfigForm, ModelConfigForm
from console.ids import new_ulid
from console.job_poll import RETRYING_TEXT
from console.model_config import (
    NOT_SET,
    PARAM_LABELS,
    PARAMS,
    PORT_NA,
    PORTS,
    PurposeConfig,
    engine_version,
    guide_link,
    is_port,
    parent_of,
    purpose_config,
    purposes,
)
from console.reembed import (
    NO_RUN,
    NO_STORIES,
    NOTHING_TO_RETRY,
    POLL_TRIGGER,
    RETRY_LABEL,
    RETRY_QUESTION,
    read_run,
    retried_text,
    run_url,
)

SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.models.snapshot"
# A 422 at ``params.<name>`` belongs on that field; any stop line on ``stop``.
ALIASES = {f"params.{name}": name for name in PARAMS} | {
    f"params.stop[{index}]": "stop" for index in range(STOP_MAX)
}


# #59's embedding texts.
NO_CHANGE = "No change — embedding already uses these values"
UNREACHABLE = (
    "The Engine couldn't reach that embedding model ({reason}). The model was not changed."
)
REEMBED = (
    "Changing the embedding model re-embeds every story's hot memories in the background. A "
    "model with a different vector dimension retypes the deployment's vector column. While a "
    "story re-embeds, its searches are lexical-only."
)
MAY_STILL_APPLY = (
    "The Engine may still apply this change — check Models and the re-embed run before saving "
    "again."
)
PORT_KEY_CLEARED = (
    "The embedding model will then be called without a key: the port never uses default's key."
)

type PurposeForm = ModelConfigForm | EmbeddingConfigForm


def _droppable(purpose: str) -> None:
    """A port has no override to drop: its drop URL 404s before any Engine call."""
    if is_port(purpose):
        raise Http404(PORTS[purpose])


def _list_url() -> str:
    return reverse("models:list")


def _edit_url(purpose: str) -> str:
    return reverse("models:edit", kwargs={"purpose": purpose})


# --- List ---------------------------------------------------------------------------------------


@require_safe
def model_list(request: HttpRequest) -> HttpResponse:
    """``GET /admin/model-config``, one row per purpose (``default`` first), and the model guide
    for the running Engine (one ``/openapi.json`` read; on failure the link points to main)."""
    client = engine_client_for(request)
    config = client.list_model_config()
    try:
        version = engine_version(client.openapi())
    except EngineClientError as exc:
        log_engine_error(request, exc)
        version = None
    return render(
        request,
        "console/models/list.html",
        {
            "config": config,
            "rows": purposes(config),
            "param_labels": [PARAM_LABELS[name] for name in PARAMS],
            "port_na": PORT_NA,
            "guide": guide_link(version),
            "next_query": f"?next={_list_url()}",
        },
    )


# --- Edit page (configuration and API key forms) ----------------------------------------------


def _snapshot(item: ModelConfigItem) -> str:
    return signing.dumps(asdict(item), salt=SNAPSHOT_SALT, compress=True)


def _item(request: HttpRequest, purpose: str) -> ModelConfigItem:
    """The purpose as the posted page read it, else (absent, tampered, another purpose) a
    fresh ``GET /admin/model-config/{purpose}``."""
    try:
        raw = signing.loads(request.POST.get(SNAPSHOT_FIELD, ""), salt=SNAPSHOT_SALT)
    except signing.BadSignature:
        raw = None
    item = ModelConfigItem.from_json(raw)
    if item is None or item.purpose != purpose:
        item = engine_client_for(request).get_model_config(purpose)
    return item


def _initial(config: PurposeConfig) -> dict[str, Any]:
    """The purpose's own values (all of ``default``'s); inherited fields stay blank."""
    initial: dict[str, Any] = {}
    fields = [("base_url", config.base_url), ("model", config.model), *config.params]
    for name, sourced in fields:
        if sourced.own and sourced.value is not None:
            value = sourced.value
            initial[name] = "\n".join(map(str, value)) if isinstance(value, list) else value
    return initial


def _placeholders(config: PurposeConfig) -> dict[str, str]:
    """``<parent or default>: <value>`` on each field the purpose does not set itself."""
    fields = [("base_url", config.base_url), ("model", config.model), *config.params]
    return {name: sourced.placeholder for name, sourced in fields if not sourced.own}


def _render_edit(
    request: HttpRequest,
    item: ModelConfigItem,
    *,
    form: PurposeForm | None = None,
    key_form: ApiKeyForm | None = None,
    engine_error: ErrorPanel | None = None,
    engine_error_note: str = "",
    key_missing: bool = False,
    key_engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    config = purpose_config(item.purpose, item)
    if form is None and config.is_port:
        form = EmbeddingConfigForm(initial=_initial(config))
    elif form is None:
        form = ModelConfigForm(initial=_initial(config), placeholders=_placeholders(config))
    context: dict[str, Any] = {
        "purpose": config,
        "config_revision": item.config_revision,
        "form": form,
        "key_form": key_form or ApiKeyForm(),
        "engine_error": engine_error,
        "engine_error_note": engine_error_note,
        "key_missing": key_missing,  # 503 config_key_missing, shown above the key form
        "key_engine_error": key_engine_error,
        # One fresh key per form, minted at each render (#43).
        "idempotency_key": new_ulid(),
        "key_idempotency_key": new_ulid(),
        "snapshot": _snapshot(item),
        "edit_query": f"?next={_edit_url(item.purpose)}",
    }
    return render(request, "console/models/edit.html", context)


@require_http_methods(["GET", "HEAD", "POST"])
def model_edit(request: HttpRequest, purpose: str) -> HttpResponse:
    """GET: the purpose's form (its own values filled, inherited ones as placeholders). POST:
    validate, then ``PUT`` the full replacement and return to the list (a port: see
    :func:`_port_edit`)."""
    if request.method != "POST":
        return _render_edit(request, engine_client_for(request).get_model_config(purpose))

    item = _item(request, purpose)
    if is_port(purpose):
        return _port_edit(request, item)
    config = purpose_config(purpose, item)
    form = ModelConfigForm(request.POST, placeholders=_placeholders(config))
    if not form.is_valid():
        return _render_edit(request, item, form=form)
    key = posted_idempotency_key(request)
    try:
        saved = engine_client_for(request).put_model_config(
            purpose, form.replacement(is_default=config.is_default), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            apply_engine_errors(exc, form, aliases=ALIASES)
            form.add_error(None, f"The Engine rejected the configuration: {exc.message}")
            return _render_edit(request, item, form=form)
        return _render_edit(request, item, form=form, engine_error=panel_for(exc))
    messages.success(
        request,
        f"Saved {purpose} — config revision {saved.config_revision}, idempotency key "
        f"{saved.idempotency_key or key}",
    )
    return redirect(_list_url())


def _key_missing(exc: EngineClientError) -> bool:
    return isinstance(exc, EngineError) and exc.code == "config_key_missing"


@require_http_methods(["GET", "HEAD", "POST"])
def model_api_key(request: HttpRequest, purpose: str) -> HttpResponse:
    """POST: ``PUT /admin/model-config/{purpose}/api-key`` with the posted key (204)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = _item(request, purpose)
    key_form = ApiKeyForm(request.POST)
    if not key_form.is_valid():
        return _render_edit(request, item, key_form=key_form)
    key = posted_idempotency_key(request)
    try:
        engine_client_for(request).put_model_api_key(
            purpose, key_form.cleaned_data["api_key"], idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if _key_missing(exc):
            return _render_edit(request, item, key_missing=True)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            apply_engine_errors(exc, key_form)
            return _render_edit(request, item, key_form=key_form)
        return _render_edit(request, item, key_engine_error=panel_for(exc))
    messages.success(request, f"Key set for {purpose}")
    return redirect(_list_url())


# --- Confirm pages: clear the key, drop the override ------------------------------------------


@require_http_methods(["GET", "HEAD", "POST"])
def model_api_key_clear(request: HttpRequest, purpose: str) -> HttpResponse:
    fallback = parent_of(purpose) or "default"
    if is_port(purpose):
        consequence = PORT_KEY_CLEARED
    elif purpose == "default":
        consequence = "Purposes without a key of their own will have none."
    else:
        consequence = f"Calls will use {fallback}'s key, or fail if it has none."
    page = ConfirmPage(
        title="Clear API key",
        question=f"Clear the API key for {{name}}? {consequence}",
        emphasis=purpose,
        confirm_label="Clear key",
        cancel_url=safe_next(request, _edit_url(purpose)),
        facts=(("Purpose", purpose),),
    )

    def perform(key: str) -> HttpResponse:
        client = engine_client_for(request)
        try:
            client.delete_model_api_key(purpose, idempotency_key=key)
        except EngineClientError as exc:
            if not _key_missing(exc):
                raise
            log_engine_error(request, exc)
            return _render_edit(request, client.get_model_config(purpose), key_missing=True)
        messages.success(request, f"Key cleared for {purpose}")
        return redirect(_list_url())

    return confirm_action(request, page, perform)


@require_http_methods(["GET", "HEAD", "POST"])
def model_drop(request: HttpRequest, purpose: str) -> HttpResponse:
    """Drop the purpose's own row. ``default`` has no button; a forced POST shows the Engine's
    refusal (409) in the error panel. A port has no drop (404, no call)."""
    _droppable(purpose)
    parent = parent_of(purpose)
    falls_back = (
        f"Every field falls back to its parent purpose ({parent}) or default."
        if parent
        else "Every field falls back to default."
    )
    page = ConfirmPage(
        title="Drop override",
        question=f"Drop {{name}}'s override? {falls_back}",
        emphasis=purpose,
        lines=("Its own API key is also removed if the Engine stores it per purpose.",),
        confirm_label="Drop override",
        cancel_url=safe_next(request, _list_url()),
        facts=(("Purpose", purpose),),
    )

    def perform(key: str) -> HttpResponse:
        dropped = engine_client_for(request).delete_model_config(purpose, idempotency_key=key)
        if dropped is None:
            messages.info(request, f"Nothing to drop — {purpose} has no override")
        else:
            uses = f"{parent} or default" if parent else "default"
            messages.success(
                request,
                f"{purpose} now uses {uses} — config revision {dropped.config_revision}, "
                f"idempotency key {dropped.idempotency_key or key}",
            )
        return redirect(_list_url())

    return confirm_action(request, page, perform)


# --- The embedding port (#59) -----------------------------------------------------------------


def _shown(value: object) -> str:
    return NOT_SET if value is None else str(value)


def _change(old: object, new: object) -> str:
    return f"{_shown(old)} → {_shown(new)}"


def _port_confirm(
    request: HttpRequest, item: ModelConfigItem, form: EmbeddingConfigForm
) -> ConfirmPage:
    """#134's confirm page for a model change: old → new for each field, the posted values
    and the snapshot riding along so the Confirm POST re-validates them."""
    data = form.cleaned_data
    hidden = {name: request.POST.get(name, "") for name in ("base_url", "model", "timeout")}
    return ConfirmPage(
        title="Change the embedding model",
        question="Change the embedding model to {name}?",
        emphasis=data["model"],
        lines=(REEMBED,),
        confirm_label="Change model and re-embed",
        cancel_url=_edit_url(item.purpose),
        facts=(
            ("Model", _change(item.model, data["model"])),
            ("Base URL", _change(item.base_url, data["base_url"] or None)),
            ("timeout (s)", _change(item.params.get("timeout"), data["timeout"])),
        ),
        hidden={**hidden, SNAPSHOT_FIELD: _snapshot(item)},
    )


def _port_edit(request: HttpRequest, item: ModelConfigItem) -> HttpResponse:
    """POST for a port: field validation, then the no-change check (no call), then, for a
    different model, the confirm page (a POST without ``confirmed=1`` always re-renders it),
    then the ``PUT``. No fresh read first: the Engine probes against its row at write time."""
    form = EmbeddingConfigForm(request.POST)
    if not form.is_valid():
        return _render_edit(request, item, form=form)
    if form.same_as(item.base_url, item.model, item.params.get("timeout")):
        messages.info(request, NO_CHANGE)
        return redirect(_list_url())
    changes_model = form.model_changes(item.model)

    def perform(key: str) -> HttpResponse:
        return _port_put(request, item, form, key, changes_model=changes_model)

    if not changes_model:
        return perform(posted_idempotency_key(request))
    return confirm_action(
        request, _port_confirm(request, item, form), perform, require_confirmed=True
    )


def _port_put(
    request: HttpRequest,
    item: ModelConfigItem,
    form: EmbeddingConfigForm,
    key: str,
    *,
    changes_model: bool,
) -> HttpResponse:
    try:
        saved = engine_client_for(request).put_model_config(
            item.purpose, form.replacement(), idempotency_key=key
        )
    except EngineClientError as exc:
        log_engine_error(request, exc)
        if isinstance(exc, EngineError) and exc.code == "embedding_unreachable":
            reason = exc.details.get("reason") or "unknown"
            form.add_error(None, UNREACHABLE.format(reason=reason))
            return _render_edit(request, item, form=form)
        if isinstance(exc, EngineError) and exc.http_status == 422:
            apply_engine_errors(exc, form, aliases=ALIASES)
            return _render_edit(request, item, form=form)
        note = MAY_STILL_APPLY if isinstance(exc, EngineUnreachable) else ""
        return _render_edit(
            request, item, form=form, engine_error=panel_for(exc), engine_error_note=note
        )
    if saved.config_revision == item.config_revision:
        messages.info(request, NO_CHANGE)
    elif changes_model:
        messages.success(
            request,
            f"Embedding model changed — re-embedding started (config revision "
            f"{saved.config_revision})",
        )
        return redirect(run_url())
    else:
        messages.success(
            request,
            f"Saved {item.purpose} — config revision {saved.config_revision}, idempotency key "
            f"{saved.idempotency_key or key}",
        )
    return redirect(_list_url())


# --- The re-embed run (#150) ------------------------------------------------------------------

RUN_SECTION = "console/models/_reembed_run.html"


@require_safe
def reembed(request: HttpRequest) -> HttpResponse:
    """``GET /admin/reembed/status`` (one page, ``limit=100``, ``after`` from the query) and
    one story-list read. An HTMX request (the section's own 5 s poll) gets the run section
    alone, which never renders flash messages."""
    after = request.GET.get("after") or None
    poll = is_htmx(request)
    run = read_run(request, engine_client_for(request), after=after, poll=poll)
    context: dict[str, Any] = {
        "run": run,
        "poll_url": run_url(after),
        "poll_trigger": POLL_TRIGGER,
        "retrying_text": RETRYING_TEXT,
        "refresh_url": run_url(after),
        "first_url": run_url(),
        "next_url": run_url(run.status.next_cursor)
        if run.status and run.status.next_cursor
        else None,
        "retry_url": reverse("models:reembed-retry"),
        "retry_label": RETRY_LABEL,
        "no_run_text": NO_RUN,
        "no_stories_text": NO_STORIES,
        "idempotency_key": new_ulid(),
    }
    return render(request, RUN_SECTION if poll else "console/models/reembed.html", context)


@require_http_methods(["GET", "HEAD", "POST"])
def reembed_retry(request: HttpRequest) -> HttpResponse:
    """**Retry failed**: #134's confirmed mode (no call on GET or on a POST without
    ``confirmed=1``), then ``POST /admin/reembed/retry`` and back to the run's first page. An
    Engine error on the POST renders #43's panel (``confirm_action``'s default)."""
    page = ConfirmPage(
        title="Retry failed re-embeds",
        question=RETRY_QUESTION,
        confirm_label=RETRY_LABEL,
        cancel_url=run_url(),
    )

    def perform(key: str) -> HttpResponse:
        retried = engine_client_for(request).retry_reembed(idempotency_key=key)
        if retried.retried:
            messages.success(request, retried_text(len(retried.retried)))
        else:
            messages.info(request, NOTHING_TO_RETRY)
        return redirect(run_url())

    return confirm_action(request, page, perform, require_confirmed=True)
