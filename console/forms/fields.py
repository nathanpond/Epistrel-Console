"""Shared M3 form fields (#64, M3 convention 6).

:class:`JsonBoxField` is a textarea holding raw JSON for an Engine field the Console has no
structured form for (a ``state_schema``, a ``metadata`` object, a list of rules). It validates
before any Engine call: the text must parse as strict JSON (no ``NaN``/``Infinity``, which
Python's parser would accept, and no key given twice in one object) and its top level must be
``top_type`` (``dict`` or ``list``). A blank box cleans to ``empty`` (default ``None``: an edit
sends ``null``; a create omits it). An initial Python value renders as indented JSON.

Usage::

    state_schema = JsonBoxField(dict, empty=None, required=False, label="State schema")
    rules = JsonBoxField(list, empty=[], required=False)
"""

import json
from typing import Any

from django import forms
from django.core.exceptions import ValidationError

TOP_NAMES = {dict: "a JSON object ({…})", list: "a JSON array ([…])"}


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """An object's pairs as a dict; a key given twice (which ``json.loads`` would silently keep
    the last of) is refused (#68)."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


class JsonBoxField(forms.CharField):
    widget = forms.Textarea(attrs={"rows": 8, "cols": 80, "spellcheck": "false"})

    def __init__(self, top_type: type, empty: Any = None, **kwargs: Any) -> None:
        if top_type not in TOP_NAMES:
            raise ValueError("JsonBoxField: top_type is dict or list")
        self.top_type = top_type
        self.empty = empty
        kwargs.setdefault("strip", True)
        super().__init__(**kwargs)

    def prepare_value(self, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return json.dumps(value, indent=2, ensure_ascii=False)

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if text is None or text in self.empty_values:
            return self.empty
        try:
            parsed = json.loads(
                text, parse_constant=_refuse_constant, object_pairs_hook=_refuse_duplicates
            )
        except ValueError as exc:
            raise ValidationError(f"Enter valid JSON: {exc}", code="invalid_json") from exc
        if not isinstance(parsed, self.top_type):
            raise ValidationError(f"Enter {TOP_NAMES[self.top_type]}.", code="wrong_type")
        return parsed

    def validate(self, value: Any) -> None:
        if self.required and (value is None or value == self.empty):
            raise ValidationError(self.error_messages["required"], code="required")

    def has_changed(self, initial: Any, data: Any) -> bool:
        try:
            return bool(self.to_python(data) != initial)
        except ValidationError:
            return True
