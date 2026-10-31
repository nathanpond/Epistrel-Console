"""The Console's routes with no Knowledge section registered (#189): the Knowledge landing renders
the empty shell, whatever #190/#192/#193 have registered since."""

from tests.inspect.urls_knowledge_exposures_only import mounted, without_sections

urlpatterns = mounted(without_sections())
