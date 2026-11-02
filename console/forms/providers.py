"""The provider registration form (#246): register and replace an external-data provider.

One field per ``ProviderSpec`` member, each named after its Engine member so a 422 at
``body.<member>`` lands on it (#43's placement), plus ``name`` when registering. The form starts
from the recorded schema's defaults and **always sends every member** (:meth:`ProviderForm.spec`):
``PUT`` replaces every field of a registration, so a member left out would silently take its
default. The Console checks only what it can check exactly (the name pattern, number ranges,
the JSON-object shape and the write-capable declarations, the Engine's ``validate_spec`` rule);
the manifest's own rules (placeholders, paths, URLs, hosts) stay the Engine's, which names the
refused member precisely (``invalid_manifest``).

Selects (C7): write-capable is "No"/"Yes"; ``confirmation`` and the two nullable booleans offer
"—" for null. A stored enum value the Console's choices don't list (a newer Engine) is kept as
one extra choice "<value> (unknown)", so an unchanged Save stays a no-op. Numbers are ASCII
digits in a ``NumberInput(step=1)`` with no HTML ``min``/``max`` (C10).
"""

import json
from collections.abc import Mapping
from typing import Any, cast

from django import forms

from console.engine_api_check import RECORDED
from console.forms.fields import JsonBoxField
from console.providers import is_provider_name
from console.wording import humanised

NAME_INVALID = "Use 1–64 lowercase letters, digits, _, . or -, starting with a letter"  # noqa: RUF001
FRESHNESS_INVALID = "Enter a whole number from 1 to 525,600"
CONFIRM_WAIT_INVALID = "Enter a whole number from 1 to 300"
WRITE_NEEDS = "A write-capable provider declares this"
READ_ONLY_NEEDS_NONE = "Only a write-capable provider declares this — choose —"
FRESHNESS_HELP = "Blank: 60. Used only with Last known."
CHOOSE = "Choose…"
NULL = "—"
READ_ONLY_CONFIRMATION = "— (read-only provider)"
FRESHNESS_MAX = 525_600
CONFIRM_WAIT_MAX = 300

_SCHEMAS = RECORDED["components"]["schemas"]
SPEC_SCHEMA: Mapping[str, Any] = _SCHEMAS["ProviderSpec"]
_PROPERTIES: Mapping[str, Any] = SPEC_SCHEMA["properties"]
# Every ``ProviderSpec`` member, in the recorded schema's order (what ``spec()`` sends).
SPEC_FIELDS: tuple[str, ...] = tuple(_PROPERTIES)
WRITE_FIELDS = ("confirmation", "dedupes_dispatch", "returns_resulting_values")
ENUM_FIELDS = ("trust_tier", "transport", "confirmation", "unavailability")
BOOL_FIELDS = ("write_capable", "dedupes_dispatch", "returns_resulting_values")


def _enum(member: str) -> list[str]:
    prop = _PROPERTIES.get(member, {})
    for option in [prop, *prop.get("anyOf", [])]:
        if isinstance(option.get("enum"), list):
            return [str(v) for v in option["enum"]]
    return []


def _description(member: str) -> str:
    return str(_PROPERTIES.get(member, {}).get("description", ""))


def schema_defaults() -> dict[str, Any]:
    """Each ``ProviderSpec`` member's recorded default (``None`` when it declares none)."""
    return {member: _PROPERTIES[member].get("default") for member in SPEC_FIELDS}


def example_manifest() -> Any:
    """The recorded ``ProviderRegistration`` example's manifest, or ``None`` without one."""
    examples = _SCHEMAS.get("ProviderRegistration", {}).get("examples") or []
    first = examples[0] if examples and isinstance(examples[0], dict) else {}
    return first.get("manifest")


def canonical(value: Any) -> str:
    """A JSON value as canonical text: ``true`` and ``1``, or ``1`` and ``1.0``, differ."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def same_spec(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """The no-op check: the manifest compared as canonical JSON, the other members by plain
    equality (``type`` too, so ``True`` never equals ``1``)."""
    for member in SPEC_FIELDS:
        left, right = a.get(member), b.get(member)
        if member == "manifest":
            if canonical(left) != canonical(right):
                return False
        elif type(left) is not type(right) or left != right:
            return False
    return True


def _select_bool(value: str) -> bool:
    return value == "true"


def _text(value: Any) -> Any:
    """An initial value as the select/number widgets compare it."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return value


def _number_widget() -> forms.NumberInput:
    return forms.NumberInput(attrs={"step": 1})


class _WholeNumber(forms.CharField):
    """ASCII digits (leading zeros accepted) from ``low`` to ``high``; blank → ``None`` when
    ``blank_ok``, else the range message."""

    def __init__(self, *, low: int, high: int, invalid: str, blank_ok: bool, **kwargs: Any) -> None:
        self.low, self.high, self.invalid, self.blank_ok = low, high, invalid, blank_ok
        kwargs.setdefault("widget", _number_widget())
        super().__init__(required=False, **kwargs)

    def to_python(self, value: Any) -> int | None:  # type: ignore[override]
        text = super().to_python(value)
        if text is None or text in self.empty_values:
            if self.blank_ok:
                return None
            raise forms.ValidationError(self.invalid, code="required")
        if not (text.isascii() and text.isdigit()) or not self.low <= int(text) <= self.high:
            raise forms.ValidationError(self.invalid, code="invalid")
        return int(text)


def _choices(member: str, first: list[tuple[str, str]], current: Any) -> list[tuple[str, str]]:
    choices = [*first, *((v, humanised(v)) for v in _enum(member))]
    if isinstance(current, str) and current and current not in {v for v, _ in choices}:
        choices.append((current, f"{current} (unknown)"))
    return choices


class ProviderForm(forms.Form):
    """``ProviderForm(data, *, registering, initial)``: ``initial`` holds Engine values (a
    read's members, or :func:`schema_defaults` when registering); Name exists only when
    registering."""

    name = forms.CharField(
        label="Name",
        strip=True,
        widget=forms.TextInput(attrs={"maxlength": 64, "spellcheck": "false"}),
    )
    trust_tier = forms.ChoiceField(label="Trust tier")
    transport = forms.ChoiceField(label="Transport")
    manifest = JsonBoxField(
        dict,
        empty=None,
        label="Manifest",
        widget=forms.Textarea(attrs={"rows": 20, "cols": 80, "spellcheck": "false"}),
    )
    write_capable = forms.TypedChoiceField(
        label="Write-capable",
        choices=[("false", "No"), ("true", "Yes")],
        coerce=_select_bool,
    )
    confirmation = forms.TypedChoiceField(
        label="Confirmation", required=False, empty_value=None, coerce=str
    )
    dedupes_dispatch = forms.TypedChoiceField(
        label="Dedupes dispatch",
        required=False,
        empty_value=None,
        coerce=_select_bool,
        choices=[("", NULL), ("true", "Yes"), ("false", "No")],
    )
    returns_resulting_values = forms.TypedChoiceField(
        label="Returns resulting values",
        required=False,
        empty_value=None,
        coerce=_select_bool,
        choices=[("", NULL), ("true", "Yes"), ("false", "No")],
    )
    unavailability = forms.ChoiceField(label="When unavailable")
    freshness_minutes = _WholeNumber(
        label="Freshness window (minutes)",
        low=1,
        high=FRESHNESS_MAX,
        invalid=FRESHNESS_INVALID,
        blank_ok=True,
        help_text=FRESHNESS_HELP,
    )
    confirm_wait_max_seconds = _WholeNumber(
        label="Confirm wait (max, seconds)",
        low=1,
        high=CONFIRM_WAIT_MAX,
        invalid=CONFIRM_WAIT_INVALID,
        blank_ok=False,
    )

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        registering: bool,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        values = dict(initial or {})
        super().__init__(data, initial={k: _text(v) for k, v in values.items()})
        self.registering = registering
        if not registering:
            del self.fields["name"]
        choose = [("", CHOOSE)]
        fields = self.fields
        trust = cast("forms.ChoiceField", fields["trust_tier"])
        transport = cast("forms.ChoiceField", fields["transport"])
        confirmation = cast("forms.ChoiceField", fields["confirmation"])
        unavailable = cast("forms.ChoiceField", fields["unavailability"])
        trust.choices = _choices("trust_tier", choose, values.get("trust_tier"))
        transport.choices = _choices("transport", choose, values.get("transport"))
        confirmation.choices = _choices(
            "confirmation", [("", READ_ONLY_CONFIRMATION)], values.get("confirmation")
        )
        unavailable.choices = _choices("unavailability", [], values.get("unavailability"))
        fields["manifest"].help_text = _description("manifest")
        fields["unavailability"].help_text = _description("unavailability")

    def clean_name(self) -> str:
        value = self.cleaned_data.get("name", "")
        if not is_provider_name(value):
            raise forms.ValidationError(NAME_INVALID, code="invalid")
        return str(value)

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if "write_capable" not in cleaned:
            return cleaned
        writes = cleaned["write_capable"]
        for member in WRITE_FIELDS:
            if member not in cleaned:
                continue
            value = cleaned[member]
            if writes and value is None:
                self.add_error(member, WRITE_NEEDS)
            elif not writes and value is not None:
                self.add_error(member, READ_ONLY_NEEDS_NONE)
        return cleaned

    def spec(self) -> dict[str, Any]:
        """Every ``ProviderSpec`` member with its cleaned value (``None`` included)."""
        return {member: self.cleaned_data[member] for member in SPEC_FIELDS}

    def registration(self) -> dict[str, Any]:
        """:meth:`spec` plus ``name`` (a ``ProviderRegistration``)."""
        return {"name": self.cleaned_data["name"], **self.spec()}
