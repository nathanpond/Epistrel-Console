"""URL configuration for the Console (layout: #43's engine-wide conventions)."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from console.auth import OperatorAuthenticationForm
from console.views import operations, status, stories
from console.views.placeholders import placeholder

operation_patterns = (
    [
        path("", operations.operation_list, name="list"),
        path("<int:pk>/", operations.operation_detail, name="detail"),
    ],
    "operations",
)

story_patterns = (
    [
        path("", stories.story_list, name="list"),
        path("new/", stories.story_new, name="new"),
        path("<str:story_id>/", stories.story_detail, name="detail"),
        path("<str:story_id>/archive/", stories.story_archive, name="archive"),
        path("<str:story_id>/unarchive/", stories.story_unarchive, name="unarchive"),
        path("<str:story_id>/delete/", stories.story_delete, name="delete"),
    ],
    "stories",
)

urlpatterns = [
    path("", status.status, name="status"),
    path("health", status.health, name="health"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(authentication_form=OperatorAuthenticationForm),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("operations/", include(operation_patterns)),
    path("stories/", include(story_patterns)),
    # Placeholders: each owning M2 story replaces its own.
    path("models/", placeholder("Models"), name="models"),
    path("guardrails/ratings/", placeholder("Guardrail ratings"), name="guardrail-ratings"),
    path("engine/", placeholder("Engine"), name="engine"),
    path("admin/", admin.site.urls),
]
