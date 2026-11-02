"""A story's binding to an external-data provider (#247): the ids the provider's manifest
substitutes for its ``{binding.<key>}`` placeholders.

- :func:`clean_bound_id` is the one bound-id rule: a value is sent **exactly as typed** (another
  system's identifier, never trimmed or case-folded); a value of only whitespace is blank
  (``None``); more than 256 characters is :data:`ID_TOO_LONG`. #259's creation rows import it, so
  an id reads the same whether bound at creation or later.
- :class:`BindingForm` asks for exactly the ids the manifest substitutes: one required
  ``id__<key>`` field per key of ``console.providers.binding_keys`` (in that order), then one
  optional field per existing id whose key the manifest no longer uses (blank drops it). A
  manifest with no ``{binding.<key>}`` gives one ``external_ids`` JSON box instead (D14). No
  field carries an HTML ``maxlength`` or ``required``, so the server's errors show.
- :meth:`BindingForm.external_ids` is the whole map to send: a ``PUT`` replaces the binding.

Key and length rules beyond the JSON box's basic shape are the Engine's (``validation_error``,
placed at the key by the view).

#259 adds the "Provider bindings" rows of both story creations:

- :class:`ProviderChoices` holds the registry as frozen :class:`ProviderRow` s (name, binding
  keys, trust tier, ``write_capable``), or the failed read's ``"<code> — <message>"``;
  :func:`read_provider_choices` reads it once. Its :meth:`ProviderChoices.payload` is what the
  blank form signs into its page, so a re-render and a fresh read render alike.
- :class:`BindingsForm` has one ``bind-<name>`` checkbox per registered provider and one
  ``bindid-<name>-<key>`` input per binding key; ``needed`` names lead (ticked at first, a name
  the registry lacks shown with no inputs). :meth:`BindingsForm.bindings` is the
  ``provider_bindings`` to send (``{}``: none ticked). Its local checks run only with
  ``checking`` (Create). :meth:`BindingsForm.place` puts ``binding_incomplete``,
  ``not_found`` (D18's undeclared 404, :func:`binding_not_found`), ``provider_unregistered``
  and a ``validation_error``'s binding failures on the rows.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BoundField
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from console.forms.fields import JsonBoxField

if TYPE_CHECKING:
    from console.engine.client import EngineClient, EngineClientError, EngineError
    from console.engine.models import Provider
    from console.engine_form_errors import FieldError

ID_PREFIX = "id__"
JSON_FIELD = "external_ids"
ID_MAX = 256
IDS_MAX = 32
ID_TOO_LONG = "Up to 256 characters"
IDS_TOO_MANY = "Up to 32 ids"
ID_NOT_STRING = "Each id is a string"
KEY_INVALID = "Ids are named with letters, digits and _ (up to 64)"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
JSON_HELP = 'A JSON object of id names to ids, e.g. {"store": "s-9"}'


def required_text(key: str) -> str:
    return f"Enter the id this provider's manifest substitutes for `{{binding.{key}}}`"


def field_name(key: str) -> str:
    return ID_PREFIX + key


def clean_bound_id(raw: str | None) -> str | None:
    """``None`` for a missing or whitespace-only value, else ``raw`` untrimmed; over 256
    characters is a ``ValidationError(ID_TOO_LONG)``."""
    if raw is None or not raw.strip():
        return None
    if len(raw) > ID_MAX:
        raise ValidationError(ID_TOO_LONG, code="too_long")
    return raw


class BoundIdField(forms.CharField):
    """One id, cleaned by :func:`clean_bound_id`; ``needed`` makes a blank value an error."""

    def __init__(self, *, key: str, needed: bool, **kwargs: Any) -> None:
        self.key = key
        self.needed = needed
        kwargs.update(required=False, strip=False)
        super().__init__(**kwargs)

    def widget_attrs(self, widget: forms.Widget) -> dict[str, Any]:
        return {}  # no maxlength (C10): a longer id shows the server's error

    def clean(self, value: Any) -> str | None:
        cleaned = clean_bound_id(value if isinstance(value, str) else None)
        if cleaned is None and self.needed:
            raise ValidationError(required_text(self.key), code="required")
        return cleaned


class BindingForm(forms.Form):
    """The bind form for one provider: ``keys`` the manifest's binding keys, ``existing`` the
    story's current ids there (``None``: no binding)."""

    use_required_attribute = False

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        keys: list[str],
        existing: Mapping[str, str] | None,
    ) -> None:
        super().__init__(data)
        self.keys = list(keys)
        self.existing = dict(existing) if existing is not None else None
        current = self.existing or {}
        self.kept = [k for k in current if k not in self.keys]
        if self.keys:
            for key in self.keys:
                self.fields[field_name(key)] = BoundIdField(
                    key=key,
                    needed=True,
                    label=format_html("<code>{}</code>", key),
                    initial=current.get(key),
                )
            for key in self.kept:
                self.fields[field_name(key)] = BoundIdField(
                    key=key,
                    needed=False,
                    label=format_html(
                        "<code>{}</code> (not used by the manifest — blank drops it)", key
                    ),
                    initial=current.get(key),
                )
        else:
            self.fields[JSON_FIELD] = JsonBoxField(
                dict,
                empty={},
                required=False,
                label=format_html("<code>{}</code>", JSON_FIELD),
                help_text=JSON_HELP,
                initial=self.existing,
            )

    @property
    def json_mode(self) -> bool:
        return not self.keys

    def clean_external_ids(self) -> dict[str, Any]:
        ids: dict[str, Any] = self.cleaned_data.get(JSON_FIELD) or {}
        if any(not isinstance(v, str) for v in ids.values()):
            raise ValidationError(ID_NOT_STRING, code="not_string")
        if any(not KEY_PATTERN.fullmatch(k) for k in ids):
            raise ValidationError(KEY_INVALID, code="key")
        if len(ids) > IDS_MAX:
            raise ValidationError(IDS_TOO_MANY, code="too_many")
        return ids

    def external_ids(self) -> dict[str, str]:
        """The full map to send (after ``is_valid()``): the manifest's keys in order, then the
        kept unused keys, blank ones dropped; or the JSON box's object as parsed."""
        if self.json_mode:
            return dict(self.cleaned_data.get(JSON_FIELD) or {})
        ids: dict[str, str] = {}
        for key in [*self.keys, *self.kept]:
            value = self.cleaned_data.get(field_name(key))
            if isinstance(value, str):
                ids[key] = value
        return ids

    def unchanged(self) -> bool:
        """A binding exists and :meth:`external_ids` equals it (order-insensitive)."""
        return self.existing is not None and self.external_ids() == self.existing


# --- #259: provider bindings at story creation ----------------------------------------------------

BIND_PREFIX = "bind-"
BINDID_PREFIX = "bindid-"
REQUIRED_PROVIDERS = "required_providers"  # the reason a scenario's own list gives
NO_PROVIDERS = "No providers are registered."
NO_KEYS_HELP = "Its manifest substitutes no ids: binding it records the link"
NEEDS_THIS_ID = "The provider's manifest needs this id"
NOT_REGISTERED_ROW = "Not registered: the Engine refuses to create the story"
BINDING_INCOMPLETE = "binding_incomplete"
NOT_FOUND = "not_found"
PROVIDER_UNREGISTERED = "provider_unregistered"
VALIDATION_ERROR = "validation_error"
BINDING_CODES = frozenset({BINDING_INCOMPLETE, NOT_FOUND, PROVIDER_UNREGISTERED})
_BINDING_PATH = re.compile(
    r"^provider_bindings\.(?P<name>.+?)"
    r"(?:\.external_ids(?:\.(?P<key>[A-Za-z0-9_]{1,64})(?P<deeper>[.\[].*)?)?)?$"
)
_EXTERNAL_IDS_PATH = re.compile(r"^external_ids(?:\.(?P<key>[A-Za-z0-9_]{1,64})(?:[.\[].*)?)?$")


def bind_field(name: str) -> str:
    """A provider row's "Bind" checkbox."""
    return BIND_PREFIX + name


def bindid_field(name: str, key: str) -> str:
    """One id input of a provider row, split at the last hyphen (keys have none), so provider
    ``a`` with key ``b`` never collides with provider ``a-b``'s checkbox."""
    return f"{BINDID_PREFIX}{name}-{key}"


def blank_key_text(key: str) -> str:
    return f"Enter this id: the manifest substitutes {{binding.{key}}}"


def untick_text(name: str) -> str:
    return f"Tick “Bind {name}” to send these ids, or clear them"


def not_found_text(name: str) -> str:
    return f"{name} isn't registered (anymore) — untick it or register it"


def unread_text(error: str) -> str:
    return (
        f"Couldn't read the provider registry ({error}) — the story is created without "
        "bindings; bind it later on its Providers page"
    )


def unregistered_text(name: str, *, required: bool) -> str:
    """A needed provider the registry lacks: a ``required_providers`` name is refused at
    creation; a name only an external State gives fails the story's turns."""
    if required:
        return (
            f"{name} — not registered: the Engine refuses to create the story "
            "(`provider_unregistered`)"
        )
    return (
        f"{name} — not registered: the story is created, but its turns fail "
        "(`provider_unbound`) until it is registered and bound on its Providers page"
    )


def bound_names_text(body: Mapping[str, Any]) -> str:
    """The success flash's " · bound to <names>" (row order, as sent), or ``""``."""
    sent = body.get("provider_bindings")
    if not isinstance(sent, Mapping) or not sent:
        return ""
    return " · bound to " + ", ".join(str(name) for name in sent)


def binding_not_found(exc: EngineError, body: Mapping[str, Any]) -> bool:
    """The undeclared (D18) 404 ``not_found`` naming, in ``details.provider``, a provider
    ``body`` bound: a binding refusal. Any other 404 is not."""
    sent = body.get("provider_bindings")
    provider = exc.details.get("provider")
    return (
        exc.http_status == 404
        and exc.code == NOT_FOUND
        and isinstance(provider, str)
        and isinstance(sent, Mapping)
        and provider in sent
    )


@dataclass(frozen=True)
class ProviderRow:
    """One registered provider as the creation rows need it: its binding keys (``binding_keys``
    of its manifest, first-use order), trust tier and ``write_capable``."""

    name: str
    keys: tuple[str, ...]
    trust_tier: str
    write_capable: bool

    @property
    def muted(self) -> str:
        return f"{self.trust_tier} · {'write-capable' if self.write_capable else 'read-only'}"


@dataclass(frozen=True)
class ProviderChoices:
    """The registry as the creation forms offer it: ``providers`` in ``list_providers``' order,
    or ``None`` when the read failed (``error`` says why)."""

    providers: list[ProviderRow] | None
    error: str = ""

    @classmethod
    def of(cls, providers: Sequence[Provider]) -> ProviderChoices:
        from console.providers import binding_keys

        return cls(
            [
                ProviderRow(p.name, tuple(binding_keys(p.manifest)), p.trust_tier, p.write_capable)
                for p in providers
            ]
        )

    def payload(self) -> dict[str, Any]:
        """What the signed ``provider_choices`` field carries: derived rows, never manifests."""
        if self.providers is None:
            return {"error": self.error}
        return {
            "rows": [[r.name, list(r.keys), r.trust_tier, r.write_capable] for r in self.providers]
        }

    @classmethod
    def from_payload(cls, raw: object) -> ProviderChoices | None:
        """:meth:`payload` read back; ``None`` for any other shape."""
        if not isinstance(raw, dict):
            return None
        if "rows" not in raw:
            error = raw.get("error")
            return cls(None, error) if isinstance(error, str) else None
        rows = raw["rows"]
        if not isinstance(rows, list):
            return None
        found: list[ProviderRow] = []
        for row in rows:
            if not (isinstance(row, list) and len(row) == 4):
                return None
            name, keys, tier, writable = row
            if not (
                isinstance(name, str)
                and isinstance(keys, list)
                and all(isinstance(k, str) for k in keys)
                and isinstance(tier, str)
                and isinstance(writable, bool)
            ):
                return None
            found.append(ProviderRow(name, tuple(keys), tier, writable))
        return cls(found)


def read_provider_choices(
    client: EngineClient, log: Callable[[EngineClientError], None]
) -> ProviderChoices:
    """``list_providers`` as :class:`ProviderChoices`; any client error (a 403 too) is logged
    through ``log`` and kept as ``"<code> — <message>"``, so creation goes on without
    bindings."""
    from console.engine.choices import error_text
    from console.engine.client import EngineClientError, EngineError

    try:
        listed = client.list_providers()
    except EngineClientError as exc:
        log(exc)
        text = f"{exc.code} — {exc.message}" if isinstance(exc, EngineError) else error_text(exc)
        return ProviderChoices(None, text)
    return ProviderChoices.of(listed.providers)


@dataclass(frozen=True)
class BindingRow:
    """One row of the fieldset as the template renders it. ``provider`` is ``None`` for a needed
    name the registry lacks (no checkbox, no inputs: :attr:`missing` says why)."""

    name: str
    provider: ProviderRow | None
    reasons: tuple[str, ...]
    checkbox: BoundField | None
    inputs: list[BoundField]
    link: str | None
    missing: str
    errors: list[str]

    @property
    def needed_text(self) -> str:
        if not self.reasons:
            return ""
        return "Needed by this scenario: " + ", ".join(self.reasons)


class BindingsForm(forms.Form):
    """The "Provider bindings" fieldset of both creation forms (#259): one row per name in
    ``needed`` (in order), then every other registered provider. Each registered row has a
    ``bind-<name>`` checkbox (ticked at first for a needed one) and one ``bindid-<name>-<key>``
    input per binding key. The local checks (a ticked row's blank key, ids in an unticked row,
    over-256) run only with ``checking`` (the Create press); otherwise the form is always
    valid and says nothing."""

    use_required_attribute = False

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        providers: ProviderChoices,
        needed: Sequence[str] = (),
        reasons: Mapping[str, Sequence[str]] | None = None,
        checking: bool = False,
    ) -> None:
        super().__init__(data)
        self.choices = providers
        self.checking = checking
        self.needed = list(dict.fromkeys(needed))
        self.reasons = {name: tuple(r) for name, r in (reasons or {}).items()}
        self.top_errors: list[str] = []
        self.row_errors: dict[str, list[str]] = {}
        registered = {row.name: row for row in providers.providers or []}
        self.registered = registered
        order: list[tuple[str, ProviderRow | None]] = []
        if providers.providers is not None:
            order = [(name, registered.get(name)) for name in self.needed]
            order += [(row.name, row) for row in providers.providers if row.name not in self.needed]
        self.order = order
        for name, row in order:
            if row is None:
                continue
            self.fields[bind_field(name)] = forms.BooleanField(
                required=False,
                label=f"Bind {name}",
                initial=name in self.needed,
                help_text="" if row.keys else NO_KEYS_HELP,
            )
            for key in row.keys:
                self.fields[bindid_field(name, key)] = BindIdField(
                    label=format_html("<code>{}</code>", key),
                    help_text=f"Fills {{binding.{key}}} in the manifest",
                )

    # --- what the page says -----------------------------------------------------------------

    @property
    def note(self) -> str:
        """The fieldset's one line instead of rows: the failed read, or no provider at all."""
        if self.choices.providers is None:
            text = unread_text(self.choices.error)
            if self.needed:
                text += f". This scenario needs: {', '.join(self.needed)}"
            return text
        if not self.order:
            return NO_PROVIDERS
        return ""

    def rows(self) -> list[BindingRow]:
        from console.effects import provider_url

        try:
            listing: str | None = reverse("engine:providers")
        except NoReverseMatch:  # D19: unlinked until #240's page is registered
            listing = None
        rows: list[BindingRow] = []
        for name, row in self.order:
            reasons = self.reasons.get(name, ())
            if row is None:
                rows.append(
                    BindingRow(
                        name,
                        None,
                        reasons,
                        None,
                        [],
                        listing,
                        unregistered_text(name, required=REQUIRED_PROVIDERS in reasons),
                        self.row_errors.get(name, []),
                    )
                )
                continue
            rows.append(
                BindingRow(
                    name,
                    row,
                    reasons,
                    self[bind_field(name)],
                    [self[bindid_field(name, key)] for key in row.keys],
                    provider_url(name),
                    "",
                    self.row_errors.get(name, []),
                )
            )
        return rows

    # --- checking and sending ---------------------------------------------------------------

    def _raw(self, name: str) -> str | None:
        value = self.data.get(name) if self.is_bound else None
        return value if isinstance(value, str) else None

    def _ticked(self, name: str) -> bool:
        return bool(self.cleaned_data.get(bind_field(name)))

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        if not self.checking:
            return cleaned
        for name, row in self.order:
            if row is None:
                continue
            if self._ticked(name):
                for key in row.keys:
                    field = bindid_field(name, key)
                    try:
                        value = clean_bound_id(self._raw(field))
                    except ValidationError as exc:
                        self.add_error(field, exc)
                        continue
                    if value is None:
                        self.add_error(field, blank_key_text(key))
            elif any(_typed(self._raw(bindid_field(name, k))) for k in row.keys):
                self.add_error(bind_field(name), untick_text(name))
        return cleaned

    def bindings(self) -> dict[str, dict[str, dict[str, str]]]:
        """``{name: {"external_ids": {key: id}}}`` for each ticked row (after ``is_valid()``),
        keys in ``binding_keys`` order, blank ones left out; ``{}`` when none is ticked."""
        if not hasattr(self, "cleaned_data"):
            return {}
        sent: dict[str, dict[str, dict[str, str]]] = {}
        for name, row in self.order:
            if row is None or not self._ticked(name):
                continue
            ids: dict[str, str] = {}
            for key in row.keys:
                try:
                    value = clean_bound_id(self._raw(bindid_field(name, key)))
                except ValidationError:  # only unchecked (``checking`` off) gets here
                    value = self._raw(bindid_field(name, key))
                if value is not None:
                    ids[key] = value
            sent[name] = {"external_ids": ids}
        return sent

    # --- placing the Engine's refusals ------------------------------------------------------

    def _row_error(self, name: str, message: str) -> None:
        """On the row's checkbox; a needed row without one keeps it beside the row; a name
        with no row goes to the top as "<name>: <message>"."""
        if bind_field(name) in self.fields:
            self.add_error(bind_field(name), message)
        elif any(n == name for n, _ in self.order):
            self.row_errors.setdefault(name, []).append(message)
        else:
            self.top_errors.append(f"{name}: {message}")

    def _key_error(self, name: str, key: str, message: str) -> None:
        if bindid_field(name, key) in self.fields:
            self.add_error(bindid_field(name, key), message)
        else:
            self._row_error(name, f"{key}: {message}")

    def place_failure(self, path: str, message: str) -> bool:
        """Place one ``validation_error`` failure at ``path`` (``body.`` prefix allowed) when it
        is about the bindings; ``False`` (nothing placed) otherwise."""
        path = path.removeprefix("body.")
        if path == "provider_bindings":
            self.top_errors.append(f"{path}: {message}")
            return True
        match = _BINDING_PATH.match(path)
        if match is not None:
            name, key = match["name"], match["key"]
            if not any(n == name for n, _ in self.order):
                self.top_errors.append(f"{path}: {message}")
            elif key is not None:
                self._key_error(name, key, message)
            else:
                self._row_error(name, message)
            return True
        match = _EXTERNAL_IDS_PATH.match(path)
        if match is None:
            return False
        key = match["key"]
        if key is None:
            self.top_errors.append(f"external_ids: {message}")
            return True
        placed = False
        for name, row in self.order:
            if row is not None and key in row.keys and self._ticked_safe(name):
                self.add_error(bindid_field(name, key), message)
                placed = True
        if not placed:
            self.top_errors.append(f"{key}: {message}")
        return True

    def _ticked_safe(self, name: str) -> bool:
        if hasattr(self, "cleaned_data"):
            return self._ticked(name)
        return bool(self._raw(bind_field(name)))

    def place(self, exc: EngineError) -> list[FieldError]:
        """Put a binding refusal on its rows (``binding_incomplete``, ``not_found``,
        ``provider_unregistered``) and a ``validation_error``'s binding failures on their
        inputs; returns the failures that aren't about the bindings (all of them for any other
        code), for the form's own placement."""
        from console.engine_form_errors import engine_field_errors

        details = exc.details
        if exc.code == BINDING_INCOMPLETE:
            provider = details.get("provider")
            missing = details.get("missing")
            keys = [k for k in missing if isinstance(k, str)] if isinstance(missing, list) else []
            if not isinstance(provider, str) or not any(n == provider for n, _ in self.order):
                self.top_errors.append(f"{provider}: {exc.message}")
            else:
                for key in keys:
                    self._key_error(provider, key, NEEDS_THIS_ID)
                if not keys:
                    self._row_error(provider, exc.message)
            return []
        if exc.code == NOT_FOUND:
            provider = details.get("provider")
            if isinstance(provider, str) and bind_field(provider) in self.fields:
                self.add_error(bind_field(provider), not_found_text(provider))
            else:
                self.top_errors.append(f"{provider}: {exc.message}")
            return []
        if exc.code == PROVIDER_UNREGISTERED:
            names = details.get("providers")
            for name in names if isinstance(names, list) else []:
                if isinstance(name, str):
                    self._row_error(name, NOT_REGISTERED_ROW)
            return []
        failures = engine_field_errors(exc)
        if exc.code != VALIDATION_ERROR:
            return failures
        return [f for f in failures if not self.place_failure(f.path, f.message or exc.message)]


def _typed(raw: str | None) -> bool:
    """Something other than blank was typed (:func:`clean_bound_id`'s blank rule; an over-long
    id counts as typed)."""
    try:
        return clean_bound_id(raw) is not None
    except ValidationError:
        return True


class BindIdField(forms.CharField):
    """A creation row's id input: never required, never stripped, no ``maxlength`` (C10);
    the checks are :class:`BindingsForm`'s, on Create only."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.update(required=False, strip=False)
        super().__init__(**kwargs)

    def widget_attrs(self, widget: forms.Widget) -> dict[str, Any]:
        return {}
