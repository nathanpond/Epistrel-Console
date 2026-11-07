"""The Console's routes without ``inspect:settle-now`` (#256), ``inspect:compensate`` (#264) and
``inspect:resolve-listing`` (#326): the Reconciliation rows (#249) then carry no action links
(#64's nav rule)."""

from typing import Any

from epistrel_console import urls as real
from tests.inspect.urls_knowledge_exposures_only import mounted

DROPPED = {"settle-now", "compensate", "resolve-listing"}


def _patterns() -> list[Any]:
    return [p for p in real.inspect_patterns[0] if getattr(p, "name", None) not in DROPPED]


urlpatterns = mounted(_patterns())
