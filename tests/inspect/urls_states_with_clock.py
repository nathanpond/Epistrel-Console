"""The Console's routes with the real States pages (#242) and a stub ``inspect:clock`` (#239's
stub view): the States clock line links "Clock" (before #243 lands, and after)."""

from epistrel_console import urls as real
from tests.inspect.urls_world_stub_sections import CLOCK, mounted

urlpatterns = mounted(
    [*(p for p in real.inspect_patterns[0] if getattr(p, "name", None) != "clock"), CLOCK]
)
