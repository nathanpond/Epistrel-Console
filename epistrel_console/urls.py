"""URL configuration for the Console (layout: #43's engine-wide conventions)."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import URLPattern, include, path, register_converter

from console.auth import OperatorAuthenticationForm
from console.ids import UlidConverter
from console.states import StateNameConverter
from console.views import (
    acting,
    amendments,
    backfill,
    catalog_characters,
    catalog_lifecycle,
    catalog_scenarios,
    certifications,
    certify,
    directives,
    engine_panel,
    erasure,
    feedback,
    history,
    inspect,
    inspect_beliefs,
    inspect_canon,
    inspect_clock,
    inspect_divergences,
    inspect_effects_trail,
    inspect_exposures,
    inspect_external,
    inspect_ledger,
    inspect_memories,
    inspect_memory,
    inspect_merge,
    inspect_predicates,
    inspect_reconciliation,
    inspect_scene,
    inspect_scheduled_events,
    inspect_state_declare,
    inspect_state_definition,
    inspect_state_proposals,
    inspect_state_set,
    inspect_states,
    line_edit,
    media,
    media_picker,
    media_proxy,
    models,
    offboarding,
    operations,
    ownership,
    perception,
    personas,
    play,
    policies,
    principals,
    profile_defaults,
    profile_edit,
    profile_upgrade,
    profiles,
    providers,
    rating_suggestion,
    ratings,
    rewrite_answers,
    rewrite_apply,
    rewrites,
    status,
    stories,
    story_characters,
    story_config,
    story_from_scenario,
    story_providers,
    story_rating,
    story_settings,
    timeline,
)

register_converter(UlidConverter, "ulid")
# #242: a State name as the Engine's ``StateName`` pattern allows (any other is a Django 404).
register_converter(StateNameConverter, "state_name")

operation_patterns = (
    [
        path("", operations.operation_list, name="list"),
        path("<int:pk>/", operations.operation_detail, name="detail"),
        path("evals/", operations.evals, name="evals"),
        # #250: the Engine's record of any operation id (normalised and checked in the view).
        path("engine/<str:operation_id>/", operations.engine_operation, name="engine"),
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
        # #300 (E11): the Play page's Direct tab POST.
        path("<str:story_id>/direct/", directives.direct, name="direct"),
        path("<str:story_id>/operations/<str:operation_id>/poll/", play.poll, name="poll"),
        path("<str:story_id>/inspector/", play.inspector, name="inspector"),
        path("<str:story_id>/transcript/", play.transcript, name="transcript"),
        # #185: Override a co-author notice for this session (no Engine call; ``<ulid:…>``).
        path(
            "<str:story_id>/notices/<ulid:notice_id>/override/",
            play.override_notice,
            name="override-notice",
        ),
        path("<str:story_id>/history/", history.history, name="history"),
        # #305 (E11): edit one recorded line from History (a line-edit rewrite session).
        path("<str:story_id>/history/line/", line_edit.line_edit, name="line-edit"),
        # #297 (E11): the story's Rewrite page and one rewrite session (``<ulid:…>``).
        path("<str:story_id>/rewrites/", rewrites.rewrites, name="rewrites"),
        path(
            "<str:story_id>/rewrites/<ulid:session_id>/",
            rewrites.rewrite,
            name="rewrite",
        ),
        # #298: the session's questions form posts here (POST only).
        path(
            "<str:story_id>/rewrites/<ulid:session_id>/answers/",
            rewrite_answers.answers,
            name="rewrite-answers",
        ),
        # #299 (E11): apply a session's plan (POST) and cancel it behind a confirm page.
        path(
            "<str:story_id>/rewrites/<ulid:session_id>/apply/",
            rewrite_apply.apply,
            name="rewrite-apply",
        ),
        path(
            "<str:story_id>/rewrites/<ulid:session_id>/cancel/",
            rewrite_apply.cancel,
            name="rewrite-cancel",
        ),
        # #302 (E11): the story's amendments and one amendment (``<ulid:…>``).
        path("<str:story_id>/amendments/", amendments.amendments, name="amendments"),
        path(
            "<str:story_id>/amendments/<ulid:amendment_id>/",
            amendments.amendment,
            name="amendment",
        ),
        # #303 (E11): plan an undo or a redo of an amendment as a rewrite session.
        path(
            "<str:story_id>/amendments/<ulid:amendment_id>/undo/",
            amendments.amendment_undo,
            name="amendment-undo",
        ),
        path(
            "<str:story_id>/amendments/<ulid:amendment_id>/redo/",
            amendments.amendment_redo,
            name="amendment-redo",
        ),
        # #195: one turn's perception trace, reconstructed per character.
        path(
            "<str:story_id>/turns/<int:turn>/perception/",
            perception.perception,
            name="perception",
        ),
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
        path(  # #63: pin the story to a profile version or follow the latest
            "<str:story_id>/config/version/",
            story_config.version_change,
            name="config-version",
        ),
        # #247: the story's provider bindings and the bind form for one provider.
        path("<str:story_id>/providers/", story_providers.providers, name="providers"),
        path(
            "<str:story_id>/providers/<str:name>/bind/",
            story_providers.provider_bind,
            name="provider-bind",
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
        path(
            "<str:story_id>/characters/<str:character_id>/participation/",
            story_characters.character_participation,
            name="character-participation",
        ),
        path(
            "<str:story_id>/characters/<str:character_id>/weight/",
            story_characters.character_weight,
            name="character-weight",
        ),
        # #80: change the story's rating, confirm a tighten, follow its check.
        path("<str:story_id>/rating/", story_rating.rating_page, name="rating"),
        path("<str:story_id>/rating/confirm/", story_rating.rating_confirm, name="rating-confirm"),
        path(
            "<str:story_id>/rating/checks/<str:job_id>/",
            story_rating.rating_check,
            name="rating-check",
        ),
        # #308: the story's Backfill page (?banner=1, ?partial=1) and its cancel confirm.
        path("<str:story_id>/backfill/", backfill.backfill_page, name="backfill"),
        path("<str:story_id>/backfill/cancel/", backfill.backfill_cancel, name="backfill-cancel"),
        # #309: start a gap fill or finish a paused one (one URL, ``mode``), resolve a conflict.
        path("<str:story_id>/backfill/start/", backfill.backfill_start, name="backfill-start"),
        # #314: request a certification run of the story's configuration, estimate first.
        path("<str:story_id>/certify/", certify.certify_story, name="certify"),
        path(
            "<str:story_id>/backfill/conflicts/<str:conflict_id>/",
            backfill.backfill_conflict,
            name="backfill-conflict",
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
        # #263: correct the latest turn's input (#185's Fix / Clarify).
        path("<str:story_id>/correct/<int:turn>/", timeline.correct, name="correct"),
    ],
    "stories",
)

# A story's Inspect area (#135, console/views/inspect.py): Overview at ``inspect/``, each other tab
# at ``inspect/<tab value>/`` named ``<tab value>`` (console.inspect.Tab). A tab registers here
# when its story lands; the bar shows only the tabs that resolve.
inspect_patterns = (
    [
        path("", inspect.overview, name="overview"),
        path("memory/", inspect.memory, name="memory"),
        # #145: request consolidation of a turn window (form, confirm, call).
        path("memory/consolidate/", inspect_memory.consolidate, name="consolidate"),
        # #236: every memory of the story, filtered and paged (Engine #531).
        path("memory/list/", inspect_memories.memory_list, name="memory-list"),
        path("retrieval/", inspect.retrieval, name="retrieval"),
        # #137: the story's canon facts on both time axes.
        path("canon/", inspect_canon.canon, name="canon"),
        # #138: one fact's lineage across supersedes (``<ulid:…>``: any other id is a 404).
        path("canon/<ulid:fact_id>/lineage/", inspect_canon.lineage, name="canon-lineage"),
        # #146: write author canon (commit and its result page again; lock, unlock, supersede).
        path("canon/new/", inspect_canon.new_fact, name="canon-new"),
        # #306: Merge entities (E11), a rewrite session of kind ``entity_merge``.
        path("canon/merge/", inspect_merge.entity_merge, name="entity-merge"),
        path("canon/written/", inspect_canon.written, name="canon-written"),
        path("canon/<ulid:fact_id>/lock/", inspect_canon.lock, name="canon-lock"),
        path("canon/<ulid:fact_id>/unlock/", inspect_canon.unlock, name="canon-unlock"),
        path("canon/<ulid:fact_id>/supersede/", inspect_canon.supersede, name="canon-supersede"),
        # #189: the Knowledge tab; its sections (knowledge/beliefs/, knowledge/divergences/,
        # knowledge/exposures/) are registered by #190, #192 and #193.
        path("knowledge/", inspect.knowledge, name="knowledge"),
        # #190: one character's beliefs (its partition) and the identities it holds.
        path("knowledge/beliefs/", inspect_beliefs.beliefs, name="beliefs"),
        # #194: direct a belief (first-order or nested) and its result page again.
        path("knowledge/beliefs/direct/", inspect_beliefs.direct, name="belief-direct"),
        path("knowledge/beliefs/written/", inspect_beliefs.written, name="belief-written"),
        # #192: the story's divergences (lies).
        path("knowledge/divergences/", inspect_divergences.divergences, name="divergences"),
        # #193: what witnesses heard (exposures) and whether each resolved.
        path("knowledge/exposures/", inspect_exposures.exposures, name="exposures"),
        # #239 (D3): the World and Effects tabs. Each landing redirects to its first open section;
        # the sections register below with their owners (console.inspect.WORLD_SECTIONS /
        # EFFECTS_SECTIONS): World's States world/states/ (#242), Proposed States
        # world/states/proposals/ (#266), Clock world/clock/ (#243), Scheduled events
        # world/scheduled-events/ (#244), External values world/external-values/ (#248); Effects'
        # Reconciliation effects/reconciliation/ (#249), Outbox effects/outbox/ (#325, E12) and
        # Effects trail effects/trail/ (#255).
        # Per-State pages live under the singular world/state/<name>/… (#242, #251, #265), so the
        # fixed world/states/new/ (#261) and world/states/proposals/ never shadow a State named
        # "new" or "proposals".
        path("world/", inspect.world, name="world"),
        # #242: the States section and one State as of a boundary.
        path("world/states/", inspect_states.states, name="states"),
        # #261: declare a State between turns, and its result again from the #44 row.
        path("world/states/new/", inspect_state_declare.state_new, name="state-new"),
        path(
            "world/states/new/declared/",
            inspect_state_declare.state_declared,
            name="state-declared",
        ),
        # #266: the Proposed States section, a 202 analysis, approve (a form page, and its result
        # again from the #44 row) and dismiss (a confirmed-mode confirm page).
        path(
            "world/states/proposals/",
            inspect_state_proposals.state_proposals,
            name="state-proposals",
        ),
        path(
            "world/states/proposals/analyze/",
            inspect_state_proposals.state_proposals_analyze,
            name="state-proposals-analyze",
        ),
        path(
            "world/states/proposals/approved/",
            inspect_state_proposals.state_proposal_approved,
            name="state-proposal-approved",
        ),
        path(
            "world/states/proposals/<ulid:proposal_id>/approve/",
            inspect_state_proposals.state_proposal_approve,
            name="state-proposal-approve",
        ),
        path(
            "world/states/proposals/<ulid:proposal_id>/dismiss/",
            inspect_state_proposals.state_proposal_dismiss,
            name="state-proposal-dismiss",
        ),
        path("world/state/<state_name:name>/", inspect_states.state, name="state"),
        # #251: set a State between turns, and its result again from the #44 row.
        path("world/state/<state_name:name>/set/", inspect_state_set.state_set, name="state-set"),
        path(
            "world/state/<state_name:name>/set/written/",
            inspect_state_set.state_written,
            name="state-written",
        ),
        # #265: change a State's definition (a configuration write).
        path(
            "world/state/<state_name:name>/definition/",
            inspect_state_definition.state_definition,
            name="state-definition",
        ),
        # #243: the Clock section and its settings page.
        path("world/clock/", inspect_clock.clock, name="clock"),
        path("world/clock/settings/", inspect_clock.clock_settings, name="clock-settings"),
        # #252: advance the clock between turns, and its result again from the #44 row.
        path("world/clock/advance/", inspect_clock.clock_advance, name="clock-advance"),
        path("world/clock/advanced/", inspect_clock.clock_advanced, name="clock-advanced"),
        # #244: the Scheduled events section.
        path(
            "world/scheduled-events/",
            inspect_scheduled_events.scheduled_events,
            name="scheduled-events",
        ),
        # #253: create a scheduled event, and the event writes' result page from the #44 row.
        path(
            "world/scheduled-events/new/",
            inspect_scheduled_events.scheduled_event_new,
            name="scheduled-event-new",
        ),
        path(
            "world/scheduled-events/written/",
            inspect_scheduled_events.scheduled_event_written,
            name="scheduled-event-written",
        ),
        # #262: reschedule (form) or cancel (confirm page) an existing canon event.
        path(
            "world/scheduled-events/<ulid:event_id>/reschedule/",
            inspect_scheduled_events.scheduled_event_reschedule,
            name="scheduled-event-reschedule",
        ),
        path(
            "world/scheduled-events/<ulid:event_id>/cancel/",
            inspect_scheduled_events.scheduled_event_cancel,
            name="scheduled-event-cancel",
        ),
        # #248: the External values section (what providers answered, and the facts citing it).
        path(
            "world/external-values/",
            inspect_external.external_values,
            name="external-values",
        ),
        path("effects/", inspect.effects, name="effects"),
        # #249: the Reconciliation section (every listed outbox row, each at #outbox-<id>).
        path(
            "effects/reconciliation/",
            inspect_reconciliation.reconciliation,
            name="reconciliation",
        ),
        # #256: settle a listed effect now (confirm page, then the call) and its result again.
        path(
            "effects/reconciliation/settled/",
            inspect_reconciliation.settled_now,
            name="settled-now",
        ),
        path(
            "effects/reconciliation/<str:outbox_id>/settle-now/",
            inspect_reconciliation.settle_now,
            name="settle-now",
        ),
        # #264: compensate a listed effect (form, confirm page, the call) and its result again.
        path(
            "effects/reconciliation/compensated/",
            inspect_reconciliation.compensated,
            name="compensated",
        ),
        path(
            "effects/reconciliation/<str:outbox_id>/compensate/",
            inspect_reconciliation.compensate,
            name="compensate",
        ),
        # #255: the Effects trail section (every State change the story's turns committed).
        path("effects/trail/", inspect_effects_trail.effects_trail, name="effects-trail"),
        # #136: the predicate registry, a proposal's decision and its result page again.
        path("predicates/", inspect_predicates.predicates, name="predicates"),
        path(
            "predicates/decision/",
            inspect_predicates.decision_report,
            name="predicate-decision",
        ),
        path(
            "predicates/<str:predicate_id>/approve/",
            inspect_predicates.approve,
            name="predicate-approve",
        ),
        path(
            "predicates/<str:predicate_id>/reject/",
            inspect_predicates.reject,
            name="predicate-reject",
        ),
        # #148: the story's thunks and promissory ledger.
        path("ledger/", inspect_ledger.ledger, name="ledger"),
        # #58: the scene at a turn and viewpoint, and an entity's profile (``<ulid:…>``: any
        # other id is a 404), both in the Scene tab.
        path("scene/", inspect_scene.scene, name="scene"),
        path("entities/<ulid:entity_id>/", inspect_scene.entity, name="entity"),
        # #199: model the entity as a minor character (form, write, PRG to the entity page).
        path(
            "entities/<ulid:entity_id>/model/",
            inspect_scene.entity_model,
            name="entity-model",
        ),
        # #143: a retrieved memory's row actions (``<ulid:…>``: any other id is a 404).
        path("memories/<ulid:memory_id>/pin/", inspect_memory.pin, name="memory-pin"),
        path("memories/<ulid:memory_id>/unpin/", inspect_memory.unpin, name="memory-unpin"),
        path(
            "memories/<ulid:memory_id>/quarantine/",
            inspect_memory.quarantine,
            name="memory-quarantine",
        ),
        path("memories/<ulid:memory_id>/release/", inspect_memory.release, name="memory-release"),
        # #144: sanitize a memory's text (form, confirm, call).
        path(
            "memories/<ulid:memory_id>/sanitize/",
            inspect_memory.sanitize,
            name="memory-sanitize",
        ),
        # #139: a turn's eval hooks and read set, the lazy block of the turn inspector and #44.
        path(
            "turns/<ulid:engine_operation_id>/trace/",
            inspect.turn_trace,
            name="turn-trace",
        ),
    ],
    "inspect",
)

model_patterns = (
    [
        path("", models.model_list, name="list"),
        path("embedding/reembed/", models.reembed, name="reembed"),
        path("embedding/reembed/retry/", models.reembed_retry, name="reembed-retry"),
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
        # #240: the deployment's external-data providers, read-only.
        path("providers/", providers.provider_list, name="providers"),
        path("providers/<str:name>/", providers.provider_detail, name="provider"),
        # #246: register (outside providers/<name>/: "new" is a valid provider name), replace,
        # delete.
        path("provider-registration/", providers.provider_new, name="provider-new"),
        path("providers/<str:name>/replace/", providers.provider_edit, name="provider-edit"),
        path("providers/<str:name>/delete/", providers.provider_delete, name="provider-delete"),
        # #84: the deployment's profile defaults, in the schema-generated form.
        path(
            "profile-defaults/edit/",
            profile_defaults.profile_defaults_edit,
            name="profile-defaults",
        ),
        # #313: the deployment's certifications, one record, and a certification run's job page.
        path("certifications/", certifications.certifications, name="certifications"),
        path(
            "certifications/<str:certification_id>/",
            certifications.certification,
            name="certification",
        ),
        path(
            "certification-runs/<str:run_id>/",
            certifications.certification_run,
            name="certification-run",
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
        # #310: upgrade the profile's pinned stories (E8's two stages on one URL).
        path(
            "<str:profile_id>/upgrade-pinned/",
            profile_upgrade.upgrade_pinned,
            name="upgrade-pinned",
        ),
        # #314: request a certification run, estimate first (E8's two stages on one URL).
        path("<str:profile_id>/certify/", certify.certify_profile, name="certify"),
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

# Erasure (#86): the form (GET-driven), the dry run (POST, rendered in place), the real run (POST
# only, from the preview's signed payload), the result read back from the operation log, the list.
erasure_patterns = (
    [
        path("", erasure.erasure_index, name="index"),
        path("new/", erasure.erasure_new, name="new"),
        path("preview/", erasure.erasure_preview, name="preview"),
        path("run/", erasure.erasure_run, name="run"),
        path("status/", erasure.erasure_status_partial, name="status"),
        path("reconcile/", erasure.erasure_reconcile, name="reconcile"),
        path("boundary/", erasure.erasure_boundary, name="boundary"),
        path("<str:erasure_id>/", erasure.erasure_detail, name="detail"),
    ],
    "erasure",
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
    path("stories/<str:story_id>/inspect/", include(inspect_patterns)),
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
    path("erasure/", include(erasure_patterns)),
    path("ownership/", include(ownership_patterns)),
    path("admin/", admin.site.urls),
]
