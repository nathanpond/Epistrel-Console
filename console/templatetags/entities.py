"""``{% load entities %}``: link an entity through the registry (#64, ``console.entities``).

``{{ row.entity_id|entity_link:row.entity_type }}`` renders the id linked to its Console page,
or in ``<code>`` when the type has no page (yet) or is unknown.
``{{ row.entity_type|entity_display }}`` is the type's display name.
"""

from django import template
from django.utils.safestring import SafeString

from console import entities

register = template.Library()


@register.filter
def entity_link(entity_id: str | None, entity_type: str | None) -> SafeString:
    return entities.entity_link(entity_type, entity_id)


@register.filter
def entity_display(entity_type: str | None) -> str:
    return entities.display_name(entity_type)
