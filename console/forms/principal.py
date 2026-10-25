"""Principal inputs (#64, M3 convention 7): one rule, one field, one place that remembers them.

- :func:`clean_principal` is the Engine's principal rule: surrounding spaces and tabs trimmed,
  then 1 to 256 characters, none in U+0000 to U+001F or U+007F to U+009F, and not exactly ``system``
  (unless ``allow_system``). The trimmed value is what gets stored and sent.
- :func:`clean_acting_principal` adds the acting selector's own refusal of any character above
  U+00FF: the acting principal travels in an HTTP header, which carries Latin-1 only.
- :class:`PrincipalField` is the form field every M3 principal input uses (owner, relation
  principal, player, transfer target, list filters, the #76 lookup): a text input with a
  ``<datalist>`` of :class:`~console.models.KnownPrincipal`. Pre-filling it with the acting
  principal is the view's job: ``initial={"owner": acting_principal(request)}``.
- :func:`remember_principals` adds the form's typed principals to ``KnownPrincipal`` once the
  whole form ``is_valid()``, before the Engine call (whatever the Engine then answers). GET list
  filters set ``remember=False``.

Refusals are checked in order (blank, control character, length, ``system``, Latin-1) and only
the first failing one is reported, with the texts in :data:`MESSAGES`.
"""

from collections.abc import Iterable
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from console.engine.client import PRINCIPAL_MAX, RESERVED_PRINCIPAL, is_control
from console.models import KnownPrincipal

KNOWN_CAP = 50  # the select and datalist offer at most this many known principals
LATIN1_MAX = 0xFF

MESSAGES = {
    "blank": "Principal can't be blank.",
    "length": f"Principal is longer than {PRINCIPAL_MAX} characters.",
    "control": "Principal contains a control character.",
    "system": f"`{RESERVED_PRINCIPAL}` is reserved for the Engine.",
    "latin1": "Principal can only use Latin-1 characters (it is sent in an HTTP header).",
}


def clean_principal(value: str | None, *, allow_system: bool = False) -> str:
    """``value`` trimmed of surrounding spaces and tabs, or :class:`ValidationError` (code
    ``blank``/``control``/``length``/``system``) naming the first rule it breaks."""
    trimmed = (value or "").strip(" \t")
    if not trimmed:
        raise ValidationError(MESSAGES["blank"], code="blank")
    if any(is_control(ch) for ch in trimmed):
        raise ValidationError(MESSAGES["control"], code="control")
    if len(trimmed) > PRINCIPAL_MAX:
        raise ValidationError(MESSAGES["length"], code="length")
    if trimmed == RESERVED_PRINCIPAL and not allow_system:
        raise ValidationError(MESSAGES["system"], code="system")
    return trimmed


def clean_acting_principal(value: str | None) -> str:
    """:func:`clean_principal`, then refuse any character above U+00FF (code ``latin1``)."""
    trimmed = clean_principal(value)
    if any(ord(ch) > LATIN1_MAX for ch in trimmed):
        raise ValidationError(MESSAGES["latin1"], code="latin1")
    return trimmed


def known_principals(limit: int = KNOWN_CAP) -> list[str]:
    """The known principals, most recently used first (then by principal), at most ``limit``."""
    return list(
        KnownPrincipal.objects.order_by("-last_used_at", "principal").values_list(
            "principal", flat=True
        )[:limit]
    )


def remember_principal(principal: str) -> None:
    """Add ``principal`` to ``KnownPrincipal``, or bump its ``last_used_at`` (retried once when a
    concurrent insert wins the unique constraint)."""
    for attempt in range(2):
        try:
            with transaction.atomic(durable=False):
                KnownPrincipal.objects.update_or_create(
                    principal=principal, defaults={"last_used_at": timezone.now()}
                )
            return
        except IntegrityError:
            if attempt:
                raise


def remember_principals(*forms_: forms.BaseForm) -> list[str]:
    """Remember every non-blank cleaned value of each form's :class:`PrincipalField` with
    ``remember=True``. Call it after ``form.is_valid()``, before the Engine call. Returns the
    principals remembered, in field order."""
    remembered: list[str] = []
    for form in forms_:
        for name, field in form.fields.items():
            if not isinstance(field, PrincipalField) or not field.remember:
                continue
            value = form.cleaned_data.get(name)
            if isinstance(value, str) and value and value not in remembered:
                remember_principal(value)
                remembered.append(value)
    return remembered


class PrincipalInput(forms.TextInput):
    """A text input with its own ``<datalist id="dl-<auto_id>">`` of known principals, so
    several principal fields on one page don't collide. The query runs only when it renders."""

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__({"autocomplete": "off", "spellcheck": "false", **(attrs or {})})

    @staticmethod
    def datalist_id(attrs: dict[str, Any] | None, name: str) -> str:
        return f"dl-{(attrs or {}).get('id') or name}"

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        attrs = {**(attrs or {}), "list": self.datalist_id(attrs, name)}
        return super().get_context(name, value, attrs)

    def render(
        self,
        name: str,
        value: Any,
        attrs: dict[str, Any] | None = None,
        renderer: Any = None,
    ) -> SafeString:
        listed = self.datalist_id({**self.attrs, **(attrs or {})}, name)
        options = format_html_join(
            "", '<option value="{}"></option>', ((p,) for p in known_principals())
        )
        datalist = format_html('<datalist id="{}">{}</datalist>', listed, options)
        return format_html("{}{}", super().render(name, value, attrs, renderer), datalist)


class PrincipalField(forms.CharField):
    """A principal input: :func:`clean_principal` (``allow_system=True`` for filters and the #76
    lookup, since ``system`` owns presets), rendered with :class:`PrincipalInput`.

    ``remember`` (default ``True``) marks the field for :func:`remember_principals`; GET list
    filters pass ``remember=False``. An optional field (``required=False``) cleans blank to
    ``""``. Values here may use any Unicode (they travel in JSON bodies or UTF-8 paths); only
    the acting selector is limited to Latin-1.
    """

    widget = PrincipalInput

    def __init__(self, *, allow_system: bool = False, remember: bool = True, **kwargs: Any) -> None:
        # Trimming is the rule's own (spaces and tabs only), not CharField's ``strip``.
        kwargs.setdefault("strip", False)
        kwargs.setdefault("max_length", None)
        super().__init__(**kwargs)
        self.allow_system = allow_system
        self.remember = remember

    def clean(self, value: Any) -> str:
        raw = "" if value is None else str(value)
        if not raw.strip(" \t") and not self.required:
            return ""
        return clean_principal(raw, allow_system=self.allow_system)


def principal_choices(own: str, current: str, known: Iterable[str]) -> list[tuple[str, str]]:
    """The acting selector's options: ``own`` first labelled "(me)", then ``current`` when it is
    someone else (always offered, even when its ``KnownPrincipal`` row is gone or past the cap),
    then the known principals. No blank option."""
    options = [(own, f"{own} (me)")]
    seen = {own}
    for principal in (current, *known):
        if principal not in seen:
            options.append((principal, principal))
            seen.add(principal)
    return options
