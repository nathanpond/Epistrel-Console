"""The Console's routes with no World section registered (#239's empty World shell): the real
patterns minus ``inspect:states``, ``inspect:state`` (#242) and ``inspect:clock``."""

from tests.inspect.urls_world_stub_sections import mounted, without_stubbed

urlpatterns = mounted(without_stubbed())
