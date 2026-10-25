"""URL configuration for the Console (layout: #43's engine-wide conventions)."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from console.auth import OperatorAuthenticationForm
from console.views import (
    acting,
    engine_panel,
    history,
    models,
    operations,
    play,
    policies,
    ratings,
    status,
    stories,
    story_settings,
    timeline,
)

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
        path("<str:story_id>/ingest/", play.ingest, name="ingest"),
        path("<str:story_id>/operations/<str:operation_id>/poll/", play.poll, name="poll"),
        path("<str:story_id>/inspector/", play.inspector, name="inspector"),
        path("<str:story_id>/transcript/", play.transcript, name="transcript"),
        path("<str:story_id>/history/", history.history, name="history"),
        # The story's settings page (#54): GET the page, POST its settings form; each other
        # section posts to its own URL.
        path("<str:story_id>/settings/", story_settings.settings_page, name="settings"),
        path(
            "<str:story_id>/settings/policies/",
            story_settings.save_policy,
            name="settings-policy",
        ),
        path("<str:story_id>/settings/pinned/", story_settings.save_pinned, name="settings-pinned"),
        path(
            "<str:story_id>/settings/instructions/",
            story_settings.save_instructions,
            name="settings-instructions",
        ),
        path("<str:story_id>/archive/", stories.story_archive, name="archive"),
        path("<str:story_id>/unarchive/", stories.story_unarchive, name="unarchive"),
        path("<str:story_id>/delete/", stories.story_delete, name="delete"),
        # Timeline operations (#53): GET renders the confirm page (fork: its form), POST acts.
        path("<str:story_id>/regenerate/", timeline.regenerate, name="regenerate"),
        path("<str:story_id>/rewind/<int:turn>/", timeline.rewind, name="rewind"),
        path("<str:story_id>/undo-rewind/", timeline.undo_rewind, name="undo-rewind"),
        path("<str:story_id>/swap/", timeline.swap, name="swap"),
        path("<str:story_id>/fork/<int:turn>/", timeline.fork, name="fork"),
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

engine_patterns = (
    [
        path("", engine_panel.engine_home, name="home"),
        path("rebuild/", engine_panel.rebuild_pick, name="rebuild-pick"),
        # A result is a Console operation id (an integer); a story id is a ULID.
        path("rebuild/<int:pk>/", engine_panel.rebuild_result, name="rebuild-result"),
        path("rebuild/<str:story_id>/", engine_panel.rebuild, name="rebuild"),
        path("settings/", engine_panel.deployment_settings, name="settings"),
        path("config-changes/", engine_panel.config_changes, name="config-changes"),
    ],
    "engine",
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
    path("acting-as/", acting.acting_as, name="acting-as"),
    path("operations/", include(operation_patterns)),
    path("stories/", include(story_patterns)),
    path("models/", include(model_patterns)),
    path("guardrails/policies/", include(policy_patterns)),
    path("guardrails/ratings/", include(rating_patterns)),
    path("engine/", include(engine_patterns)),
    path("admin/", admin.site.urls),
]
