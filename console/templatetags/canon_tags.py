"""The Canon rows' computed parts (#137, :mod:`console.canon`), so ``_fact_row.html`` and
``_proposition.html`` render a :class:`~console.engine.models.FactBody` or a
:class:`~console.engine.models.Proposition` as the Engine sent it (#146 and #147 include
them with their own reads). The page's ``story_id`` (in every Inspect context) links predicate
names, turns and fact ids (``{% lineage_url %}``, #138)."""

from collections.abc import Mapping
from typing import Any

from django import template

from console.canon import FactView, PropositionView
from console.canon import fact_view as _fact_view
from console.canon import lineage_url as _lineage_url
from console.canon import proposition_view as _proposition_view
from console.engine.models import FactBody, Proposition

register = template.Library()


def _names(value: Any) -> Mapping[str, str]:
    return value if isinstance(value, Mapping) else {}


@register.simple_tag(takes_context=True)
def proposition_view(
    context: template.Context, proposition: Proposition, predicate_names: Any, cast_names: Any
) -> PropositionView:
    """``{% proposition_view proposition predicate_names cast_names as p %}``."""
    story_id = context.get("story_id")
    return _proposition_view(
        proposition,
        _names(predicate_names),
        _names(cast_names),
        story_id if isinstance(story_id, str) else None,
    )


@register.simple_tag(takes_context=True)
def fact_view(context: template.Context, fact: FactBody, cast_names: Any) -> FactView:
    """``{% fact_view fact cast_names as v %}``."""
    story_id = context.get("story_id")
    return _fact_view(fact, _names(cast_names), story_id if isinstance(story_id, str) else None)


@register.simple_tag(takes_context=True)
def lineage_url(
    context: template.Context, fact_id: str, subject: Any = None, predicate: Any = None
) -> str:
    """``{% lineage_url fact_id subject predicate %}``: convention 3's fact-id link, the lineage
    view (#138), with ``subject`` and ``predicate`` when both are given (else id only)."""
    story_id = context.get("story_id")
    return _lineage_url(
        str(story_id),
        fact_id,
        subject if isinstance(subject, str) else None,
        predicate if isinstance(predicate, str) else None,
    )
