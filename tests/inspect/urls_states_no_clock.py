"""The Console's routes with the real States pages (#242) and no ``inspect:clock`` (D19): the
States clock line then shows no "Clock" link (whether or not #243 has landed)."""

from epistrel_console import urls as real
from tests.inspect.urls_world_stub_sections import mounted

urlpatterns = mounted([p for p in real.inspect_patterns[0] if getattr(p, "name", None) != "clock"])
