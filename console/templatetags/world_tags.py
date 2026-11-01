"""The M6 World and Effects template filters (#235; D5, D6, D11): ``{% load world_tags %}``.

- ``{{ id|outbox_id }}`` → ``<code class="outbox-id">FULL</code>``: an outbox id (or an
  ``effect_id``) is the provider's idempotency key, so it is shown in full, never shortened by
  convention 2; "—" for a blank value.
- ``{{ clock|clock_value }}`` → ``console.world.clock_value_html`` (D6).
- ``{{ value|state_value:state }}`` → D5's ``state_value_text`` with the ``StoryState``'s type
  and unit (``None``: the type inferred, no unit), ``undefined`` wrapped in
  ``<span class="undefined">``.

D17: these filters, like the helpers behind them in ``console/world.py`` and
``console/effects.py``, are the one rendering of each value; page templates use them and never
format these values themselves.
"""

from typing import Any

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine_errors import EMPTY
from console.world import UNDEFINED, clock_value_html, state_value_text

register = template.Library()


@register.filter
def outbox_id(value: Any) -> SafeString | str:
    """The full id in ``<code class="outbox-id">`` (escaped), "—" for ``None`` or blank."""
    if value is None or value == "":
        return EMPTY
    return format_html('<code class="outbox-id">{}</code>', str(value))


@register.filter
def clock_value(value: Any) -> SafeString:
    """A ``ClockValue``/``WorldClock`` as ``<span class="clock" title="offset N min">``."""
    return clock_value_html(value)


@register.filter
def state_value(value: Any, state: Any = None) -> SafeString | str:
    """D5's text for ``value``, typed by ``state`` (a ``StoryState``) when given."""
    kind = getattr(state, "type", None) if state is not None else None
    unit = getattr(state, "unit", None) if state is not None else None
    text = state_value_text(
        value, kind if isinstance(kind, str) else None, unit if isinstance(unit, str) else None
    )
    if value is None:
        return format_html('<span class="undefined">{}</span>', UNDEFINED)
    return text
