"""URL configuration for the Console (layout: #43's engine-wide conventions)."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from console.auth import OperatorAuthenticationForm
from console.views import models, operations, play, policies, ratings, status, stories
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
        path("<str:story_id>/", play.play, name="detail"),
        path("<str:story_id>/turn/", play.turn, name="turn"),
        path("<str:story_id>/operations/<str:operation_id>/poll/", play.poll, name="poll"),
        path("<str:story_id>/inspector/", play.inspector, name="inspector"),
        path("<str:story_id>/transcript/", play.transcript, name="transcript"),
        # #52 replaces this placeholder with the full history browser.
        path(
            "<str:story_id>/history/",
            placeholder("Story history", links=(("All stories", "/stories/"),)),
            name="history",
        ),
        path("<str:story_id>/archive/", stories.story_archive, name="archive"),
        path("<str:story_id>/unarchive/", stories.story_unarchive, name="unarchive"),
        path("<str:story_id>/delete/", stories.story_delete, name="delete"),
    ],
    "stories",
)

model_patterns = (
    [
        path("", models.model_list, name="list"),
        path("<str:purpose>/edit/", models.model_edit, name="edit"),
        path("<str:purpose>/drop/", models.model_drop, name="drop"),
        path("<str:purpose>/api-key/", models.model_api_key, name="api-key"),
        path("<str:purpose>/api-key/clear/", models.model_api_key_clear, name="api-key-clear"),
    ],
    "models",
)

policy_patterns = (
    [
        path("", policies.policy_list, name="list"),
        path("new/", policies.policy_new, name="new"),
        path("<str:policy_id>/", policies.policy_detail, name="detail"),
        path("<str:policy_id>/edit/", policies.policy_edit, name="edit"),
        path("<str:policy_id>/retire/", policies.policy_retire, name="retire"),
    ],
    "policies",
)

rating_patterns = (
    [
        path("", ratings.rating_list, name="list"),
        path("new/", ratings.rating_new, name="new"),
        path("<str:rating_id>/", ratings.rating_detail, name="detail"),
        path("<str:rating_id>/edit/", ratings.rating_edit, name="edit"),
        path("<str:rating_id>/retire/", ratings.rating_retire, name="retire"),
        path("<str:rating_id>/versions/", ratings.rating_versions, name="versions"),
        path("<str:rating_id>/versions/<int:version>/", ratings.rating_version, name="version"),
    ],
    "ratings",
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
    path("models/", include(model_patterns)),
    path("guardrails/policies/", include(policy_patterns)),
    path("guardrails/ratings/", include(rating_patterns)),
    # Placeholders: each owning M2 story replaces its own.
    path("engine/", placeholder("Engine"), name="engine"),
    path("admin/", admin.site.urls),
]
