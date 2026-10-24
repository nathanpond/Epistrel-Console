"""Models (#46): every model purpose the Engine lists, which model each uses and where each value
comes from; editing a purpose (``PUT``), dropping its override, and setting or clearing its API
key. ``embedding`` is read-only here (configured in M4, #59): its action URLs answer 404 without
calling the Engine.

Every action redirects to ``/models/``. The edit page carries the purpose as it was read in a
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

from console.confirm import ConfirmPage, confirm_action, posted_idempotency_key, safe_next
from console.engine.client import EngineClientError, EngineError
from console.engine.models import ModelConfigItem
from console.engine_access import engine_client_for
from console.engine_errors import ErrorPanel, log_engine_error, panel_for
from console.engine_form_errors import apply_engine_errors
from console.forms import STOP_MAX, ApiKeyForm, ModelConfigForm
from console.ids import new_ulid
from console.model_config import (
    PARAM_LABELS,
    PARAMS,
    PurposeConfig,
    engine_version,
    guide_link,
    is_read_only,
    parent_of,
    purpose_config,
    purposes,
)

SNAPSHOT_FIELD = "snapshot"
SNAPSHOT_SALT = "console.models.snapshot"
# A 422 at ``params.<name>`` belongs on that field; any stop line on ``stop``.
ALIASES = {f"params.{name}": name for name in PARAMS} | {
    f"params.stop[{index}]": "stop" for index in range(STOP_MAX)
}


def _editable(purpose: str) -> None:
    """Read-only purposes have no edit, drop or key URLs (no Engine call is made)."""
    if is_read_only(purpose):
        raise Http404(f"{purpose} is read-only here")


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
    form: ModelConfigForm | None = None,
    key_form: ApiKeyForm | None = None,
    engine_error: ErrorPanel | None = None,
    key_missing: bool = False,
    key_engine_error: ErrorPanel | None = None,
) -> HttpResponse:
    config = purpose_config(item.purpose, item)
    if form is None:
        form = ModelConfigForm(initial=_initial(config), placeholders=_placeholders(config))
    context: dict[str, Any] = {
        "purpose": config,
        "config_revision": item.config_revision,
        "form": form,
        "key_form": key_form or ApiKeyForm(),
        "engine_error": engine_error,
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
    validate, then ``PUT`` the full replacement and return to the list."""
    _editable(purpose)
    if request.method != "POST":
        return _render_edit(request, engine_client_for(request).get_model_config(purpose))

    item = _item(request, purpose)
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
    _editable(purpose)
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
    _editable(purpose)
    fallback = parent_of(purpose) or "default"
    consequence = (
        "Purposes without a key of their own will have none."
        if purpose == "default"
        else f"Calls will use {fallback}'s key, or fail if it has none."
    )
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
    refusal (409) in the error panel."""
    _editable(purpose)
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
