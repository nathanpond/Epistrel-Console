"""The Console's routes with one World section, a stub ``inspect:states`` (#239): with States off
there is no open section, so the World landing renders the shell."""

from tests.inspect.urls_world_stub_sections import STATES, SUB_PAGES, mounted, without_stubbed

urlpatterns = mounted([*without_stubbed(), STATES, *SUB_PAGES])
