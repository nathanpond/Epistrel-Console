"""Place an Engine 422's field errors on the Console form that produced the request (#43's
"422 placement" convention; first used by #45's new-story form).

The Engine names a failing field with a dotted path, list indexes in brackets:
``body.characters[2].true_age``. ``details.errors[]`` lists every failure (``field`` and
``message`` each); without it, ``details.field`` names the one failure and the error's
``message`` describes it. :func:`apply_engine_errors` strips the ``body.`` prefix and then

- ``name`` → the form's ``name`` field;
- ``characters[2].true_age`` → row 2 of the ``characters`` formset, its ``true_age`` field (a
  row path with no matching field goes to that row's non-field errors);
- anything else (no such field, no such row, ``query.…``) → the form's non-field errors,
  prefixed with the path so the operator can tell what it refers to.

``aliases`` redirects a path the form does not mirror (``settings.default_output_viewpoint`` →
``viewpoint``). Its keys may also be a ``details`` key (``unknown_actor``) or an error ``code``:
those place the error's ``message`` on the aliased path when present.

Messages are the Engine's, as-is. Call it after ``is_valid()`` (``add_error`` needs the forms
cleaned).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django import forms
from django.forms.formsets import BaseFormSet

from console.engine.client import EngineError

BODY_PREFIX = "body."
_ROW = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\[(?P<index>\d+)\](?:\.(?P<rest>.+))?$")
# Keys of ``details`` that describe the error itself, never an alias target.
_STRUCTURAL = frozenset({"field", "errors"})

type Rows = BaseFormSet[Any] | Sequence[forms.BaseForm]


@dataclass(frozen=True)
class FieldError:
    path: str  # as the Engine sent it
    message: str


def engine_field_errors(error: EngineError) -> list[FieldError]:
    """The error's field failures: ``details.errors[]`` when present, else ``details.field``
    with the error's message, else none."""
    details = error.details
    found: list[FieldError] = []
    listed = details.get("errors")
    if isinstance(listed, list):
        for item in listed:
            if isinstance(item, dict) and isinstance(item.get("field"), str):
                message = item.get("message")
                found.append(FieldError(item["field"], message if isinstance(message, str) else ""))
    if not found and isinstance(details.get("field"), str):
        found.append(FieldError(details["field"], error.message))
    return found


def apply_engine_errors(
    error: EngineError,
    form: forms.BaseForm,
    formsets: Mapping[str, Rows] | None = None,
    aliases: Mapping[str, str] | None = None,
) -> int:
    """Add ``error``'s field failures to ``form`` and ``formsets``; returns how many were placed.

    ``formsets`` maps a list name in the request body (``characters``) to the forms that built
    that list, **in the order they were sent** (a ``BaseFormSet``, or a list when blank rows were
    dropped before sending, so the Engine's index matches).
    """
    formsets = formsets or {}
    aliases = aliases or {}
    placed = 0
    for failure in engine_field_errors(error):
        path = _strip(failure.path)
        _place(aliases.get(path, path), failure.message or error.message, form, formsets)
        placed += 1
    for key in _alias_keys(error, aliases):
        _place(aliases[key], error.message, form, formsets)
        placed += 1
    return placed


def _strip(path: str) -> str:
    return path[len(BODY_PREFIX) :] if path.startswith(BODY_PREFIX) else path


def _alias_keys(error: EngineError, aliases: Mapping[str, str]) -> Iterable[str]:
    """Detail keys and the code that an alias names, when the error carries no field path."""
    if engine_field_errors(error):
        return ()
    keys = [k for k in error.details if k not in _STRUCTURAL and k in aliases]
    if not keys and error.code in aliases:
        keys = [error.code]
    return keys


def _place(path: str, message: str, form: forms.BaseForm, formsets: Mapping[str, Rows]) -> None:
    if path in form.fields:
        form.add_error(path, message)
        return
    match = _ROW.match(path)
    if match and match["name"] in formsets:
        rows = _forms(formsets[match["name"]])
        index = int(match["index"])
        if index < len(rows):
            row = rows[index]
            rest = match["rest"]
            if rest in row.fields:
                row.add_error(rest, message)
            else:
                row.add_error(None, f"{rest}: {message}" if rest else message)
            return
    form.add_error(None, f"{path}: {message}")


def _forms(rows: Rows) -> Sequence[forms.BaseForm]:
    return rows.forms if isinstance(rows, BaseFormSet) else rows
