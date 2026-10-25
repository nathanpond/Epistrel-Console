"""URL configuration for the Console (layout: #43's engine-wide conventions)."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import URLPattern, include, path, register_converter

from console.auth import OperatorAuthenticationForm
from console.ids import UlidConverter
from console.views import (
    acting,
    catalog_characters,
    catalog_lifecycle,
    catalog_scenarios,
    engine_panel,
    feedback,
    history,
    media,
    media_picker,
    media_proxy,
    models,
    offboarding,
    operations,
    ownership,
    personas,
    play,
    policies,
    principals,
    profile_defaults,
    profile_edit,
    profiles,
    rating_suggestion,
    ratings,
    status,
    stories,
    story_characters,
    story_config,
    story_from_scenario,
    story_rating,
    story_settings,
    timeline,
)

register_converter(UlidConverter, "ulid")

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
        # #75: start a story from a scenario (step 1, step 2 and their POSTs).
        path("new/from-scenario/", story_from_scenario.from_scenario, name="from-scenario"),
        path("<str:story_id>/", play.play, name="detail"),
        path("<str:story_id>/turn/", play.turn, name="turn"),
        path("<str:story_id>/ingest/", play.ingest, name="ingest"),
        path("<str:story_id>/operations/<str:operation_id>/poll/", play.poll, name="poll"),
        path("<str:story_id>/inspector/", play.inspector, name="inspector"),
        path("<str:story_id>/transcript/", play.transcript, name="transcript"),
        path("<str:story_id>/history/", history.history, name="history"),
        # #83: rate, re-rate and retract feedback on one rendered message.
        path(
            "<str:story_id>/messages/<str:message_id>/feedback/",
            feedback.feedback,
            name="feedback",
        ),
        path(
            "<str:story_id>/messages/<str:message_id>/feedback/retract/",
            feedback.retract,
            name="feedback-retract",
        ),
        path(
            "<str:story_id>/creation-report/",
            story_from_scenario.creation_report,
            name="creation-report",
        ),
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
        # #85: the story's effective configuration, its overrides and its profile.
        path("<str:story_id>/config/", story_config.config_page, name="config"),
        path(
            "<str:story_id>/config/overrides/",
            story_config.overrides_page,
            name="config-overrides",
        ),
        path(
            "<str:story_id>/config/overrides/apply/",
            story_config.overrides_apply,
            name="config-overrides-apply",
        ),
        path(
            "<str:story_id>/config/profile/",
            story_config.profile_preview,
            name="config-profile",
        ),
        path(
            "<str:story_id>/config/profile/apply/",
            story_config.profile_apply,
            name="config-profile-apply",
        ),
        path(
            "<str:story_id>/characters/new/",
            story_characters.character_new,
            name="character-new",
        ),
        path(
            "<str:story_id>/characters/<str:character_id>/promote/",
            story_characters.character_promote,
            name="character-promote",
        ),
        # #80: change the story's rating, confirm a tighten, follow its check.
        path("<str:story_id>/rating/", story_rating.rating_page, name="rating"),
        path("<str:story_id>/rating/confirm/", story_rating.rating_confirm, name="rating-confirm"),
        path(
            "<str:story_id>/rating/checks/<str:job_id>/",
            story_rating.rating_check,
            name="rating-check",
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
        # #84: the deployment's profile defaults, in the schema-generated form.
        path(
            "profile-defaults/edit/",
            profile_defaults.profile_defaults_edit,
            name="profile-defaults",
        ),
    ],
    "engine",
)


def lifecycle_patterns(entry: str) -> list[URLPattern]:
    """Archive, unarchive, delete and listing status (#69): the same views for both entry types,
    told apart by ``entry``."""
    kwargs = {"entry": entry}
    return [
        path("<str:entry_id>/archive/", catalog_lifecycle.archive, kwargs, name="archive"),
        path("<str:entry_id>/unarchive/", catalog_lifecycle.unarchive, kwargs, name="unarchive"),
        path("<str:entry_id>/delete/", catalog_lifecycle.delete, kwargs, name="delete"),
        path(
            "<str:entry_id>/listing-status/",
            catalog_lifecycle.listing_status,
            kwargs,
            name="listing-status",
        ),
    ]


catalog_character_patterns = (
    [
        path("", catalog_characters.character_list, name="index"),
        path("new/", catalog_characters.character_new, name="new"),
        path("<str:character_id>/", catalog_characters.character_detail, name="detail"),
        path("<str:character_id>/edit/", catalog_characters.character_edit, name="edit"),
        path(
            "<str:character_id>/publish/",
            catalog_characters.character_publish,
            name="publish",
        ),
        path(
            "<str:character_id>/versions/",
            catalog_characters.character_versions,
            name="versions",
        ),
        *lifecycle_patterns(catalog_lifecycle.CHARACTER),
        # #82: the advisory rating suggestion (GET goes back to the detail page).
        path(
            "<str:entry_id>/rating-suggestion/",
            rating_suggestion.suggest,
            {"entry": "character"},
            name="rating-suggestion",
        ),
    ],
    "catalog_characters",
)

scenario_patterns = (
    [
        path("", catalog_scenarios.scenario_list, name="index"),
        path("new/", catalog_scenarios.scenario_new, name="new"),
        path("<str:scenario_id>/", catalog_scenarios.scenario_detail, name="detail"),
        path("<str:scenario_id>/edit/", catalog_scenarios.scenario_edit, name="edit"),
        # #74: a dry run, and the detail page's Publish.
        path(
            "<str:scenario_id>/validate/",
            catalog_scenarios.scenario_validate,
            name="validate",
        ),
        path(
            "<str:scenario_id>/publish/",
            catalog_scenarios.scenario_publish,
            name="publish",
        ),
        path(
            "<str:scenario_id>/versions/",
            catalog_scenarios.scenario_versions,
            name="versions",
        ),
        *lifecycle_patterns(catalog_lifecycle.SCENARIO),
        path(
            "<str:entry_id>/rating-suggestion/",
            rating_suggestion.suggest,
            {"entry": "scenario"},
            name="rating-suggestion",
        ),
    ],
    "scenarios",
)

persona_patterns = (
    [
        path("", personas.persona_list, name="index"),
        path("new/", personas.persona_new, name="new"),
        path("<str:persona_id>/", personas.persona_detail, name="detail"),
        path("<str:persona_id>/edit/", personas.persona_edit, name="edit"),
        path("<str:persona_id>/delete/", personas.persona_delete, name="delete"),
    ],
    "personas",
)

# The media library (#70), and #65's proxy every M3 page shows media with.
media_patterns = (
    [
        path("", media.media_list, name="index"),
        path("upload/", media.media_upload, name="upload"),
        # #71: the catalog editors' picker and slot-row partials (before the id routes).
        path("picker/", media_picker.media_picker, name="picker"),
        path("slot-row/", media_picker.media_slot_row, name="slot-row"),
        path("<str:media_id>/", media.media_detail, name="detail"),
        path("<str:media_id>/retire/", media.media_retire, name="retire"),
        path("<str:media_id>/content/", media_proxy.media_content, name="content"),
    ],
    "media",
)

# Engine profiles (#78): the list, a profile at its latest or any version, and its versions. Ids
# are plain strings (a preset's need not be a ULID), quoted into Engine paths by the client.
profile_patterns = (
    [
        path("", profiles.profile_list, name="index"),
        # #84: create, edit (and "Apply anyway"), copy and delete.
        path("new/", profile_edit.profile_new, name="new"),
        path("<str:profile_id>/", profiles.profile_detail, name="detail"),
        path("<str:profile_id>/versions/", profiles.profile_versions, name="versions"),
        path("<str:profile_id>/edit/", profile_edit.profile_edit, name="edit"),
        path("<str:profile_id>/apply/", profile_edit.profile_apply, name="apply"),
        path("<str:profile_id>/copy/", profile_edit.profile_copy, name="copy"),
        path("<str:profile_id>/delete/", profile_edit.profile_delete, name="delete"),
    ],
    "profiles",
)

principal_patterns = (
    [
        path("", principals.principal_index, name="index"),
        # <path:>: a principal may hold "/" (links quote it, and the view unquotes, #76).
        path("<path:principal>/", principals.principal_detail, name="detail"),
    ],
    "principals",
)

# Offboarding (#77): the plan form (its POST is the dry run), the start (POST only, from the
# preview's signed payload), the job page and its polled partial, and the list.
offboarding_patterns = (
    [
        path("", offboarding.offboarding_index, name="index"),
        path("new/", offboarding.offboarding_new, name="new"),
        path("start/", offboarding.offboarding_start, name="start"),
        path("<ulid:job_id>/", offboarding.offboarding_job, name="job"),
        path("<ulid:job_id>/progress/", offboarding.offboarding_progress, name="progress"),
    ],
    "offboarding",
)

# Owners and relations (#79): the shared panel's writes, for every owned type (``<entity_type>`` is
# the Engine's ``EntityType``; any other is 404). The removed relation and principal travel in the
# query string and the POST body, never a path segment (a principal may hold "/").
ownership_patterns = (
    [
        path("<str:entity_type>/<str:entity_id>/owner/", ownership.transfer_owner, name="owner"),
        path(
            "<str:entity_type>/<str:entity_id>/relations/add/",
            ownership.add_relation,
            name="add",
        ),
        path(
            "<str:entity_type>/<str:entity_id>/relations/remove/",
            ownership.remove_relation,
            name="remove",
        ),
    ],
    "ownership",
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
    path("catalog/characters/", include(catalog_character_patterns)),
    path("catalog/scenarios/", include(scenario_patterns)),
    path("personas/", include(persona_patterns)),
    path("media/", include(media_patterns)),
    path("profiles/", include(profile_patterns)),
    path("principals/", include(principal_patterns)),
    path("offboarding/", include(offboarding_patterns)),
    path("ownership/", include(ownership_patterns)),
    path("admin/", admin.site.urls),
]
