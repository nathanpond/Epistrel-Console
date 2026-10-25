"""The owner and relations panel's forms (#79): transfer the owner, add a relation.

Both check everything the Console can before any Engine call: the principal with #64's rule
(:class:`~console.forms.principal.PrincipalField`, so ``system`` is refused), the relation name
with the Engine's (``[A-Za-z0-9_-]{1,64}``, case-sensitive) once surrounding spaces and tabs are
trimmed. Every refusal of a relation name has one text, :data:`RELATION_ERROR`.
"""

import re
from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from console.forms.principal import PrincipalField

RELATION_MAX = 64
RELATION_RULE = re.compile(r"[A-Za-z0-9_-]{1,64}")
# What the input's own ``pattern`` lets through: the rule with the spaces the form trims.
RELATION_PATTERN = r"[ \t]*[A-Za-z0-9_\-]{1,64}[ \t]*"
# The range is an en dash, written as an escape (ruff flags the character itself).
RELATION_ERROR = "Relation names are 1\u201364 letters, digits, `_` or `-`, with no spaces."
# The datalist's first suggestions; the entity's own relation names follow (#79).
RELATION_SUGGESTIONS = ("editor", "co_author", "player")


def clean_relation(value: str | None) -> str:
    """``value`` trimmed of surrounding spaces and tabs, or :class:`ValidationError` with
    :data:`RELATION_ERROR` (blank, too long, a character outside the rule, an inner space)."""
    trimmed = (value or "").strip(" \t")
    if not RELATION_RULE.fullmatch(trimmed):
        raise ValidationError(RELATION_ERROR, code="relation")
    return trimmed


class RelationNameField(forms.CharField):
    """A relation name: :func:`clean_relation`, rendered with ``pattern`` and ``maxlength``."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("strip", False)
        super().__init__(**kwargs)

    def widget_attrs(self, widget: forms.Widget) -> dict[str, Any]:
        return {
            **super().widget_attrs(widget),
            "pattern": RELATION_PATTERN,
            "maxlength": str(RELATION_MAX),
            "autocomplete": "off",
            "spellcheck": "false",
        }

    def clean(self, value: Any) -> str:
        return clean_relation(None if value is None else str(value))


class TransferForm(forms.Form):
    """The new owner. The input starts blank (its placeholder is the current owner)."""

    principal = PrincipalField(label="New owner")

    def __init__(self, *args: Any, current: str = "", **kwargs: Any) -> None:
        kwargs.setdefault("auto_id", "id_transfer_%s")
        super().__init__(*args, **kwargs)
        if current:
            self.fields["principal"].widget.attrs["placeholder"] = current


class RelationForm(forms.Form):
    """A relation to add: the related principal and the relation's name, whose input lists
    ``datalist`` (the suggestions' ``<datalist>`` id) when given."""

    principal = PrincipalField(label="Principal")
    relation = RelationNameField(label="Relation")

    def __init__(self, *args: Any, datalist: str = "", **kwargs: Any) -> None:
        kwargs.setdefault("auto_id", "id_relation_%s")
        super().__init__(*args, **kwargs)
        if datalist:
            self.fields["relation"].widget.attrs["list"] = datalist


def relation_suggestions(names: list[str]) -> list[str]:
    """:data:`RELATION_SUGGESTIONS`, then ``names`` (the entity's own relation names), each once
    (case-sensitive, as the Engine compares them)."""
    return list(dict.fromkeys([*RELATION_SUGGESTIONS, *names]))
