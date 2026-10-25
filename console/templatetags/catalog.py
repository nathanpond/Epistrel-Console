"""``{% load catalog %}``: catalog-entry page helpers (#69).

``{% disabled_if_archived entity "edit" %}`` renders a disabled button titled "Archived —
unarchive to edit" when ``entity.status`` is ``archived``, and nothing otherwise: the page shows
it in place of a control that saves the draft, publishes or starts a story (convention 15).
"""

from typing import Any

from django import template

register = template.Library()

ARCHIVED = "archived"


def archived_title(verb: str) -> str:
    return f"Archived — unarchive to {verb}"


@register.inclusion_tag("console/catalog/_disabled_button.html")
def disabled_if_archived(entity: Any, verb: str, text: str = "") -> dict[str, Any]:
    return {
        "archived": getattr(entity, "status", None) == ARCHIVED,
        "title": archived_title(verb),
        "text": text or verb.capitalize(),
    }
