"""Deployment settings (#50): one hand-written form over ``PUT /admin/settings``.

One field per top-level ``SettingsPatch`` property except ``profile_defaults`` (shown read-only,
edited by M3 #84), each with the bounds the recorded ``SettingsBody`` states (#43) and the
schema default as help text. A guard test compares the fields with the recorded schema.

It is a merge-patch form (#43): the page carries the original settings, signed, and
:meth:`DeploymentSettingsForm.patch` returns only the settings whose value changed. Lists are
one entry per line and sent whole when changed (``provider_allowed_hosts`` is compared as a set
of lowercased entries, #241). Only the four nullable settings
(:data:`NULLABLE`) have a "Clear" checkbox (``clear_<name>``), which sends ``null`` to unset them;
no other setting can send ``null`` and there is no reset-to-default.

Engine M7 adds five families: history rewrites, directions and the dependency search ("History
rewrites and directions") and the Lite reach and author facts ("Lite rewrites and author
facts") (#322), none of them nullable; and the story bundles' retention and import limits
("Story bundles"), the certification canary and re-certification ("Certification") and the
strongest model tier ("Model tiers") (#323). Of those, only
``certification_recertify_interval_s`` is nullable (``null``: never re-certify in full; the
Engine's ``core/deployment.py`` ``NULLABLE`` lists it, though the route's description names only
three). ``model_tiers`` is :class:`ModelTierField`, a select of the models
``/admin/model-config`` lists, cleaned to ``{"strongest": <id or None>}`` and sent only as that
object (never a top-level ``null``). Byte and second values stay in the Engine's units; their
help shows the human form ("4.0 GB", "7 days").
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django import forms
from django.template.defaultfilters import filesizeformat

from console.raw import plural

STORY_LIFETIME = "story_lifetime"
DAYS = "days"
RETENTION_DAYS_MIN, RETENTION_DAYS_MAX = 1, 3650  # feedback_retention's integer branch
URL_MAX = 2048
ID_MAX = 64
FALLBACK_MAX = 500
LIST_MAX = 50
CLEAR_PREFIX = "clear_"
FRACTION_BOUNDS = "Must be above 0 and at most 1"
NAME_PLACEHOLDER_HELP = "{name} is replaced by the character's name."
# The settings that accept null (unset): no hook, no base URL, no ceiling, no full re-certification.
NULLABLE = (
    "media_moderation_webhook_url",
    "public_base_url",
    "max_characters_at_start_ceiling",
    "certification_recertify_interval_s",
)
DAY_S = 86400
MODEL_TIERS = "model_tiers"
STRONGEST = "strongest"
MODEL_TIER_LABEL = 'Strongest model (for "@strongest" routes)'
MODEL_TIER_NOT_SET = 'Not set — each "@strongest" purpose uses its own configured model'
MODEL_TIER_UNLISTED = "{id} (not in the model configuration — saving it again is refused)"
MODEL_TIER_HELP = (
    'The model Max\'s "@strongest" routes resolve to; it must be listed under Models. A change '
    "applies at each story's next turn and changes its fingerprint."
)
MODEL_TIER_READ_ONLY = "The model configuration could not be read; shown read-only."
CANARY_HELP = (
    "Off by default: every canary run spends real model calls. Re-checks each in-use valid "
    "certification every interval."
)
RECERTIFY_CLEAR_HELP = "Send null: never re-certify in full"
# The code label a row shows when it is not the setting's name.
CODE_LABELS = {MODEL_TIERS: "model_tiers.strongest"}
# Items of the list settings, as ``SettingsBody`` states them.
FEEDBACK_TAG = re.compile(r"^[a-z0-9_]{1,40}$")
MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")
LISTING_STATUS = re.compile(
    r"^[\t ]*[^\x00-\x1f\x7f-\x9f\t ]([^\x00-\x1f\x7f-\x9f]{0,38}[^\x00-\x1f\x7f-\x9f\t ])?[\t ]*$"
)
# ``provider_allowed_hosts`` (#241): a host, an IPv4 or bracketed IPv6 address, optional ``:port``.
PROVIDER_HOST = re.compile(
    r"^(?:[A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]|[0-9A-Fa-f:]{2,45})(?::[0-9]{1,5})?$"
)
PROVIDER_HOST_MAX = 260
PROVIDER_HOSTS_MAX = 100
PROVIDER_HOST_RULE = "a host, IPv4/IPv6 address or host:port"
PROVIDER_HOSTS_HELP = (
    "One per line: a host, an IPv4 address or a bracketed IPv6 address, optionally with :port "
    "(a bare entry matches any port). An http provider's URLs must match an entry. Empty refuses "
    "every http provider; removing a host makes providers that call it fail at call time. "
    "Checked when a provider registers and on each call."
)

# The schema default of each setting that states one, shown as help text ("Default: …").
DEFAULTS: dict[str, Any] = {}
# Units the setting's name or its Engine meaning gives, shown after the input.
UNITS: dict[str, str] = {}


def _help(default: Any, extra: str = "", *, shown: str | None = None) -> str:
    """ "<extra> Default: <default>." (``shown`` overrides how the default is written)."""
    if shown is None:
        shown = "unset" if default is None else _shown(default)
    text = f"Default: {shown}."
    return f"{extra} {text}" if extra else text


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value == STORY_LIFETIME:
        return "story lifetime"
    return str(value)


def _bytes(value: int) -> str:
    """ "4294967296 (4.0 GB)": the raw value, then ``filesizeformat``'s human form (its
    non-breaking space made plain)."""
    return f"{value} ({filesizeformat(value).replace('\xa0', ' ')})"


def _days(seconds: int) -> str:
    """Whole days as E23 pluralises them ("1 day", "7 days")."""
    return plural(seconds // DAY_S, "day")


def _int(
    name: str,
    lo: int,
    hi: int,
    default: int | None,
    unit: str = "",
    *,
    required: bool = True,
    extra: str = "",
    shown: str | None = None,
) -> forms.IntegerField:
    DEFAULTS[name] = default
    if unit:
        UNITS[name] = unit
    return forms.IntegerField(
        required=required,
        min_value=lo,
        max_value=hi,
        help_text=_help(default, extra, shown=shown),
    )


def _number(
    name: str, lo: float, hi: float, default: float, unit: str = "", *, extra: str = ""
) -> forms.FloatField:
    DEFAULTS[name] = default
    if unit:
        UNITS[name] = unit
    return forms.FloatField(
        min_value=lo,
        max_value=hi,
        help_text=_help(default, extra),
        widget=forms.NumberInput(attrs={"step": "any"}),
    )


def _fraction(name: str, default: float, extra: str = "") -> forms.FloatField:
    """A float in (0, 1] with no HTML ``min``/``max`` (C10) and no unit: the exclusive minimum
    is a ``clean_<name>`` check (``_number``'s inclusive ``min_value`` can't express it)."""
    DEFAULTS[name] = default
    return forms.FloatField(
        required=True,
        help_text=_help(default, extra),
        widget=forms.NumberInput(attrs={"step": "any"}),
    )


def _bool(name: str, default: bool, *, extra: str = "") -> forms.BooleanField:
    DEFAULTS[name] = default
    return forms.BooleanField(required=False, help_text=_help(default, extra))


def _text(name: str, max_length: int, default: str | None, *, required: bool) -> forms.CharField:
    DEFAULTS[name] = default
    return forms.CharField(
        required=required,
        min_length=1 if required else None,
        max_length=max_length,
        strip=True,
        help_text=_help(default),
    )


class LinesField(forms.Field):
    """A list setting as a textarea, one entry per line (blank lines and surrounding spaces
    dropped). Each entry must match ``item``; the count is bounded as the schema states.

    ``lowercase`` lowercases each entry and drops duplicates (the first kept) before the checks;
    the "Not …" error still lists the entries as typed. ``item_max_length`` bounds each entry.
    ``set_semantics`` makes :meth:`DeploymentSettingsForm.patch` compare the list as a set."""

    widget = forms.Textarea(attrs={"rows": 5})
    set_semantics: bool = False

    def __init__(
        self,
        *,
        item: re.Pattern[str],
        item_rule: str,
        min_items: int,
        max_items: int,
        lowercase: bool = False,
        item_max_length: int | None = None,
        set_semantics: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(required=False, **kwargs)
        self.item = item
        self.item_rule = item_rule
        self.min_items = min_items
        self.max_items = max_items
        self.lowercase = lowercase
        self.item_max_length = item_max_length
        self.set_semantics = set_semantics
        self._typed: dict[str, str] = {}

    def prepare_value(self, value: Any) -> Any:
        return "\n".join(map(str, value)) if isinstance(value, list) else value

    def to_python(self, value: Any) -> list[str]:
        if isinstance(value, list):
            lines = [str(v) for v in value]
        else:
            text = str(value or "")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not self.lowercase:
            return lines
        self._typed = {}
        for line in lines:
            self._typed.setdefault(line.lower(), line)
        return list(self._typed)

    def validate(self, value: Any) -> None:
        entries: list[str] = value
        bad = [entry for entry in entries if not self._fits(entry)]
        if bad:
            shown = [self._typed.get(entry, entry) for entry in bad]
            raise forms.ValidationError(f"Not {self.item_rule}: {', '.join(shown)}")
        if len(entries) < self.min_items:
            raise forms.ValidationError(f"List at least {self.min_items} entry.")
        if len(entries) > self.max_items:
            raise forms.ValidationError(f"List at most {self.max_items} entries.")

    def _fits(self, entry: str) -> bool:
        if self.item_max_length is not None and len(entry) > self.item_max_length:
            return False
        return bool(self.item.match(entry))


def _lines(
    name: str,
    item: re.Pattern[str],
    item_rule: str,
    *,
    min_items: int,
    extra: str,
    max_items: int = LIST_MAX,
) -> LinesField:
    return LinesField(
        item=item,
        item_rule=item_rule,
        min_items=min_items,
        max_items=max_items,
        help_text=f"One per line; the whole list is sent when it changes. {extra}".strip(),
    )


class RetentionWidget(forms.MultiWidget):
    """``feedback_retention``: a story-lifetime / days radio and the days input."""

    def __init__(self) -> None:
        choices = ((STORY_LIFETIME, "Story lifetime"), (DAYS, "Days:"))
        super().__init__(
            (forms.RadioSelect(choices=choices), forms.NumberInput(attrs={"min": 1, "max": 3650}))
        )

    def decompress(self, value: Any) -> list[Any]:
        if isinstance(value, int) and not isinstance(value, bool):
            return [DAYS, value]
        return [STORY_LIFETIME, None]


class RetentionField(forms.MultiValueField):
    """``feedback_retention``: ``"story_lifetime"`` or a number of days (1 to 3650)."""

    widget = RetentionWidget

    def __init__(self, **kwargs: Any) -> None:
        fields = (
            forms.ChoiceField(choices=((STORY_LIFETIME, "Story lifetime"), (DAYS, "Days"))),
            forms.IntegerField(
                required=False, min_value=RETENTION_DAYS_MIN, max_value=RETENTION_DAYS_MAX
            ),
        )
        super().__init__(fields, require_all_fields=False, **kwargs)

    def compress(self, data_list: Any) -> str | int | None:
        if not data_list:
            return None
        mode, days = [*list(data_list), None, None][:2]
        if mode == STORY_LIFETIME:
            return STORY_LIFETIME
        if days is None:
            raise forms.ValidationError("Enter the number of days, or choose story lifetime.")
        return int(days)


def _retention(name: str) -> RetentionField:
    DEFAULTS[name] = STORY_LIFETIME
    return RetentionField(help_text=_help(STORY_LIFETIME))


def _clear(name: str, help_text: str | None = None) -> forms.BooleanField:
    return forms.BooleanField(
        required=False,
        label="Clear",
        help_text=help_text or f"Send null: unset {name.replace('_', ' ')}.",
    )


def strongest(value: Any) -> str | None:
    """The ``strongest`` id of a ``model_tiers`` value (``{"strongest": id}``); a missing, null
    or malformed value, or a blank id, is ``None``."""
    if isinstance(value, Mapping):
        value = value.get(STRONGEST)
    return value if isinstance(value, str) and value else None


class ModelTierField(forms.ChoiceField):
    """``model_tiers`` as a select of model ids (#323): its initial is the stored
    ``{"strongest": id}`` (unwrapped for the select, disabled included) and it cleans to
    ``{"strongest": <id or None>}`` ("" is "Not set")."""

    def prepare_value(self, value: Any) -> Any:
        return strongest(value) or ""

    def to_python(self, value: Any) -> str:
        if isinstance(value, Mapping) or value is None:
            value = strongest(value) or ""
        return str(super().to_python(value))

    def clean(self, value: Any) -> dict[str, str | None]:
        chosen = super().clean(value)
        return {STRONGEST: chosen or None}


def _model_tiers() -> ModelTierField:
    DEFAULTS[MODEL_TIERS] = None
    return ModelTierField(
        required=False, label=MODEL_TIER_LABEL, help_text=_help(None, MODEL_TIER_HELP)
    )


# Sections in page order: (title, setting names). Every field is in exactly one.
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "General",
        (
            "default_content_rating_id",
            "default_engine_profile_id",
            "minor_age_threshold",
            "default_safe_fallback",
        ),
    ),
    (
        "Turns",
        (
            "turn_queue_max",
            "idempotency_result_window",
            "structured_retry_budget",
            "lite_summary_chunk",
            "worker_heartbeat_timeout",
            "timer_lag_alert",
        ),
    ),
    (
        "Caps",
        (
            "max_custom_instructions_chars",
            "max_pinned_memories",
            "max_pinned_per_character",
            "max_pinned_per_story",
            "max_characters_at_start_default",
            "max_characters_at_start_ceiling",
            "thunks_max_open",
        ),
    ),
    (
        "Media",
        (
            "media_allowed_types",
            "media_max_bytes",
            "media_multipart_threshold",
            "media_upload_ttl",
            "media_signed_url_ttl",
            "media_sweep_interval",
            "media_moderation_webhook_url",
            "media_moderation_timeout_ms",
            "public_base_url",
        ),
    ),
    (
        "Feedback",
        (
            "feedback_tags",
            "feedback_text_max_chars",
            "feedback_retention",
            "feedback_regen_window_s",
            "feedback_export",
        ),
    ),
    ("Catalog", ("listing_status_values", "require_primary_image", "validation_model_calls")),
    ("External providers", ("provider_allowed_hosts",)),
    (
        "Memory and retrieval",
        (
            "memory_importance_half_life_turns",
            "minor_hot_budget_fraction",
            "neighborhood_max_facts",
            "protected_index_max",
            "retrieval_vector_k",
            "retrieval_lexical_k",
            "retrieval_rrf_k",
            "retrieval_rehydrate_max_rows",
            "embedding_batch_max",
            "reembed_batch_size",
            "embedding_hnsw_m",
            "embedding_hnsw_ef_construction",
            "embedding_hnsw_ef_search",
        ),
    ),
    (
        "Verification and gates",
        (
            "entailment_max_records",
            "cascade_threshold",
            "uncertain_below",
            "extraction_shadow_sample_rate",
            "gate_promotion_min_cases",
            "gate_promotion_min_agreement",
            "rating_check_model_calls_per_record",
        ),
    ),
    (
        "History rewrites and directions",
        (
            "rewrite_ambiguity_margin",
            "rewrite_max_open_sessions",
            "rewrite_session_ttl_minutes",
            "rewrite_stale_retries",
            "directive_steer_ttl_minutes",
            "dependency_search_max_depth",
            "dependency_search_max_results",
        ),
    ),
    (
        "Lite rewrites and author facts",
        (
            "lite_author_facts_max",
            "lite_reach_chunk_messages",
            "lite_reach_chunk_tokens",
            "lite_reach_max_calls",
            "lite_reach_parallelism",
            "lite_reach_deadline_seconds",
        ),
    ),
    (
        "Story bundles",
        (
            "bundle_export_ttl_hours",
            "bundle_max_uncompressed_bytes",
            "bundle_max_entries",
            "bundle_max_entry_bytes",
        ),
    ),
    (
        "Certification",
        (
            "certification_canary_enabled",
            "certification_canary_interval_s",
            "certification_canary_sample_size",
            "certification_canary_seeds",
            "certification_recertify_interval_s",
        ),
    ),
    ("Model tiers", (MODEL_TIERS,)),
    (
        "Retention",
        (
            "audit_retention_days",
            "job_retention_days",
            "offboarding_report_retention_days",
            "rating_check_retention_days",
            "readset_change_retention_days",
            "escalation_retention_days",
            "extraction_shadow_retention_days",
        ),
    ),
    ("Erasure", ("erasure_shared_media",)),
)


@dataclass(frozen=True)
class SettingRow:
    """One setting on the page: its field, unit suffix and (nullable only) Clear checkbox;
    ``code`` is the code label (the name unless :data:`CODE_LABELS` says otherwise) and
    ``label`` the field's own label shown beside it ("" when it sets none)."""

    name: str
    field: forms.BoundField
    unit: str
    clear: forms.BoundField | None
    code: str = ""
    label: str = ""


class DeploymentSettingsForm(forms.Form):
    """Every deployment setting but ``profile_defaults``. ``original`` is the settings as
    read (``SettingsBody`` JSON); ``ratings`` the default-rating select's ``(id, label)``
    options, or ``None`` when the ratings could not be read (the field is then read-only);
    ``models`` likewise the strongest-model select's options, or ``None`` when the model
    configuration could not be read."""

    # General
    default_content_rating_id = forms.ChoiceField(help_text="The rating new stories get.")
    default_engine_profile_id = _text(
        "default_engine_profile_id", ID_MAX, "00000000000000000000PRF001", required=True
    )
    minor_age_threshold = _int("minor_age_threshold", 0, 120, 18, "years")
    default_safe_fallback = forms.CharField(
        min_length=1,
        max_length=FALLBACK_MAX,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_help("{name} changes the subject.", NAME_PLACEHOLDER_HELP),
    )
    DEFAULTS["default_safe_fallback"] = "{name} changes the subject."
    # Turns
    turn_queue_max = _int("turn_queue_max", 1, 50, 5, "turns")
    idempotency_result_window = _int("idempotency_result_window", 1, 720, 24, "hours")
    structured_retry_budget = _int("structured_retry_budget", 0, 10, 2, "retries")
    lite_summary_chunk = _int("lite_summary_chunk", 2, 200, 10, "messages")
    worker_heartbeat_timeout = _number("worker_heartbeat_timeout", 1, 3600, 60.0, "s")
    timer_lag_alert = _number("timer_lag_alert", 1, 86400, 300.0, "s")
    # Caps
    max_custom_instructions_chars = _int("max_custom_instructions_chars", 1, 20000, 4000, "chars")
    max_pinned_memories = _int("max_pinned_memories", 1, 500, 50)
    max_pinned_per_character = _int("max_pinned_per_character", 1, 10000, 200)
    max_pinned_per_story = _int("max_pinned_per_story", 1, 100000, 2000)
    max_characters_at_start_default = _int("max_characters_at_start_default", 1, 1000, 16)
    max_characters_at_start_ceiling = _int(
        "max_characters_at_start_ceiling", 1, 1000, None, required=False
    )
    thunks_max_open = _int("thunks_max_open", 1, 100000, 500)
    # Media
    media_allowed_types = _lines(
        "media_allowed_types", MEDIA_TYPE, "a media type", min_items=1, extra="e.g. image/png."
    )
    media_max_bytes = _int("media_max_bytes", 1, 10737418240, 52428800, "bytes")
    media_multipart_threshold = _int(
        "media_multipart_threshold", 1024, 10737418240, 8388608, "bytes"
    )
    media_upload_ttl = _int("media_upload_ttl", 60, 604800, 86400, "s")
    media_signed_url_ttl = _int("media_signed_url_ttl", 1, 86400, 300, "s")
    media_sweep_interval = _int("media_sweep_interval", 60, 604800, 3600, "s")
    media_moderation_webhook_url = _text(
        "media_moderation_webhook_url", URL_MAX, None, required=False
    )
    media_moderation_timeout_ms = _int("media_moderation_timeout_ms", 100, 60000, 5000, "ms")
    public_base_url = _text("public_base_url", URL_MAX, None, required=False)
    # Feedback
    feedback_tags = _lines(
        "feedback_tags",
        FEEDBACK_TAG,
        "1 to 40 of a-z 0-9 _",
        min_items=0,
        extra="May be empty.",
    )
    feedback_text_max_chars = _int("feedback_text_max_chars", 1, 20000, 2000, "chars")
    feedback_retention = _retention("feedback_retention")
    feedback_regen_window_s = _int("feedback_regen_window_s", 1, 86400, 300, "s")
    feedback_export = _bool("feedback_export", False)
    # Catalog
    listing_status_values = _lines(
        "listing_status_values",
        LISTING_STATUS,
        "1 to 40 printable characters",
        min_items=1,
        extra="In order; a new catalog entry takes the first.",
    )
    require_primary_image = _bool("require_primary_image", False)
    validation_model_calls = _int("validation_model_calls", 0, 1000, 0, "calls")
    # External providers (#241): order means nothing to the Engine, so it is compared as a set.
    provider_allowed_hosts = LinesField(
        item=PROVIDER_HOST,
        item_rule=PROVIDER_HOST_RULE,
        min_items=0,
        max_items=PROVIDER_HOSTS_MAX,
        lowercase=True,
        item_max_length=PROVIDER_HOST_MAX,
        set_semantics=True,
        help_text=PROVIDER_HOSTS_HELP,
    )
    # Memory and retrieval
    memory_importance_half_life_turns = _int(
        "memory_importance_half_life_turns", 1, 1000000, 200, "turns"
    )
    minor_hot_budget_fraction = _fraction(
        "minor_hot_budget_fraction",
        0.2,
        "Share of the per-character hot-memory budget a minor character's unpinned hot memories "
        "are held to (above 0, at most 1).",
    )
    neighborhood_max_facts = _int("neighborhood_max_facts", 1, 1000, 50)
    protected_index_max = _int("protected_index_max", 1, 100000, 5000)
    retrieval_vector_k = _int("retrieval_vector_k", 1, 1000, 50)
    retrieval_lexical_k = _int("retrieval_lexical_k", 1, 1000, 50)
    retrieval_rrf_k = _int("retrieval_rrf_k", 1, 1000, 60)
    retrieval_rehydrate_max_rows = _int("retrieval_rehydrate_max_rows", 0, 500, 20, "rows")
    embedding_batch_max = _int("embedding_batch_max", 1, 256, 64)
    reembed_batch_size = _int("reembed_batch_size", 1, 10000, 500)
    embedding_hnsw_m = _int("embedding_hnsw_m", 2, 100, 16)
    embedding_hnsw_ef_construction = _int("embedding_hnsw_ef_construction", 4, 1000, 64)
    embedding_hnsw_ef_search = _int("embedding_hnsw_ef_search", 1, 1000, 40)
    # Verification and gates
    entailment_max_records = _int("entailment_max_records", 1, 200, 30, "records")
    cascade_threshold = _int("cascade_threshold", 1, 1000, 5)
    uncertain_below = _number("uncertain_below", 0, 1, 0.5)
    extraction_shadow_sample_rate = _number("extraction_shadow_sample_rate", 0, 1, 0.1)
    gate_promotion_min_cases = _int("gate_promotion_min_cases", 1, 1000000, 200, "cases")
    gate_promotion_min_agreement = _number("gate_promotion_min_agreement", 0, 1, 0.98)
    rating_check_model_calls_per_record = _int(
        "rating_check_model_calls_per_record", 0, 100, 2, "calls"
    )
    # History rewrites and directions
    rewrite_ambiguity_margin = _number(
        "rewrite_ambiguity_margin",
        0,
        1,
        0.15,
        extra="Two entity candidates whose scores are this close are asked about, not guessed.",
    )
    rewrite_max_open_sessions = _int(
        "rewrite_max_open_sessions",
        1,
        100,
        5,
        "sessions",
        extra="Open rewrite sessions one story may hold (refused with rewrite_session_limit "
        "past it).",
    )
    rewrite_session_ttl_minutes = _int(
        "rewrite_session_ttl_minutes",
        10,
        43200,
        1440,
        "minutes",
        extra="An idle session expires after this.",
    )
    rewrite_stale_retries = _int(
        "rewrite_stale_retries",
        0,
        10,
        2,
        "retries",
        extra="How often an apply-directly rewrite whose plan went stale re-plans and retries.",
    )
    directive_steer_ttl_minutes = _int(
        "directive_steer_ttl_minutes",
        10,
        43200,
        1440,
        "minutes",
        extra='An unused "steer the next reply" direction is dropped after this.',
    )
    dependency_search_max_depth = _int(
        "dependency_search_max_depth",
        1,
        10,
        3,
        "levels",
        extra="How deep one dependency search follows derives chains.",
    )
    dependency_search_max_results = _int(
        "dependency_search_max_results",
        1,
        100000,
        500,
        "results",
        extra="The most dependents one search answers (reported truncated past it).",
    )
    # Lite rewrites and author facts
    lite_author_facts_max = _int(
        "lite_author_facts_max",
        1,
        1000,
        50,
        "facts",
        extra="The most active lasting author facts a Lite story holds (refused with "
        "author_fact_limit past it).",
    )
    lite_reach_chunk_messages = _int(
        "lite_reach_chunk_messages",
        1,
        500,
        60,
        "lines",
        extra="Transcript lines one Lite reach call reads.",
    )
    lite_reach_chunk_tokens = _int(
        "lite_reach_chunk_tokens",
        500,
        200000,
        12000,
        "tokens",
        extra="A reach chunk closes early past this estimate.",
    )
    lite_reach_max_calls = _int(
        "lite_reach_max_calls",
        2,
        500,
        24,
        "calls",
        extra="The most reach calls one pass makes; past it the rest is reported unsearched "
        "(truncated).",
    )
    lite_reach_parallelism = _int(
        "lite_reach_parallelism",
        1,
        32,
        4,
        "chunks",
        extra="Chunks one reach pass searches at once.",
    )
    lite_reach_deadline_seconds = _int(
        "lite_reach_deadline_seconds",
        5,
        3600,
        120,
        "s",
        extra="A whole reach pass's deadline.",
    )
    # Story bundles (FR-STORE-7)
    bundle_export_ttl_hours = _int(
        "bundle_export_ttl_hours",
        1,
        720,
        24,
        "hours",
        extra="How long an export's archive is kept before the retention sweep deletes it.",
    )
    bundle_max_uncompressed_bytes = _int(
        "bundle_max_uncompressed_bytes",
        1048576,
        68719476736,
        4294967296,
        "bytes",
        extra="The largest import accepted.",
        shown=_bytes(4294967296),
    )
    bundle_max_entries = _int("bundle_max_entries", 10, 10000000, 100000, "entries")
    bundle_max_entry_bytes = _int(
        "bundle_max_entry_bytes",
        1048576,
        68719476736,
        1073741824,
        "bytes",
        shown=_bytes(1073741824),
    )
    # Certification (FR-PROF-17): the canary is off by default, as every run spends model calls.
    certification_canary_enabled = _bool("certification_canary_enabled", False, extra=CANARY_HELP)
    certification_canary_interval_s = _int(
        "certification_canary_interval_s",
        3600,
        7776000,
        604800,
        "s",
        extra=f"604800 s is {_days(604800)}.",
    )
    certification_canary_sample_size = _int(
        "certification_canary_sample_size", 10, 1000, 50, "cases"
    )
    certification_canary_seeds = _int("certification_canary_seeds", 1, 5, 1, "seeds")
    certification_recertify_interval_s = _int(
        "certification_recertify_interval_s",
        86400,
        31536000,
        None,
        "s",
        required=False,
        extra=f"86400–31536000 s is {86400 // DAY_S}–{_days(31536000)}.",  # noqa: RUF001
    )
    # Model tiers (#227)
    model_tiers = _model_tiers()
    # Retention
    audit_retention_days = _int("audit_retention_days", 1, 3650, 90, "days")
    job_retention_days = _int("job_retention_days", 1, 3650, 7, "days")
    offboarding_report_retention_days = _int(
        "offboarding_report_retention_days", 1, 3650, 90, "days"
    )
    rating_check_retention_days = _int("rating_check_retention_days", 1, 3650, 30, "days")
    readset_change_retention_days = _int("readset_change_retention_days", 1, 3650, 7, "days")
    escalation_retention_days = _int("escalation_retention_days", 1, 3650, 90, "days")
    extraction_shadow_retention_days = _int("extraction_shadow_retention_days", 1, 3650, 90, "days")
    # Erasure
    erasure_shared_media = forms.ChoiceField(
        choices=(("purge", "purge"), ("retire", "retire")),
        help_text=_help("purge"),
    )
    DEFAULTS["erasure_shared_media"] = "purge"
    # The Clear checkboxes of the nullable settings.
    clear_media_moderation_webhook_url = _clear("media_moderation_webhook_url")
    clear_public_base_url = _clear("public_base_url")
    clear_max_characters_at_start_ceiling = _clear("max_characters_at_start_ceiling")
    clear_certification_recertify_interval_s = _clear(
        "certification_recertify_interval_s", RECERTIFY_CLEAR_HELP
    )

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        original: Mapping[str, Any],
        ratings: Sequence[tuple[str, str]] | None,
        models: Sequence[tuple[str, str]] | None,
    ) -> None:
        self.original = dict(original)
        initial = {name: self.original.get(name) for name in setting_names()}
        super().__init__(data, initial=initial)
        rating = self.fields["default_content_rating_id"]
        if isinstance(rating, forms.ChoiceField):
            if ratings is None:
                current = str(self.original.get("default_content_rating_id", ""))
                rating.choices = [(current, current)]
                rating.disabled = True
                rating.help_text = "The ratings could not be read; shown read-only."
            else:
                rating.choices = list(ratings)
        tier = self.fields[MODEL_TIERS]
        if isinstance(tier, ModelTierField):
            if models is None:
                stored = strongest(self.original.get(MODEL_TIERS))
                tier.choices = [(stored, stored) if stored else ("", MODEL_TIER_NOT_SET)]
                tier.disabled = True
                tier.help_text = MODEL_TIER_READ_ONLY
            else:
                tier.choices = list(models)

    def setting_names(self) -> tuple[str, ...]:
        return setting_names()

    def sections(self) -> Iterator[tuple[str, list[SettingRow]]]:
        for title, names in SECTIONS:
            rows = [
                SettingRow(
                    name=name,
                    field=self[name],
                    unit=UNITS.get(name, ""),
                    clear=self[CLEAR_PREFIX + name] if name in NULLABLE else None,
                    code=CODE_LABELS.get(name, name),
                    label=str(self.fields[name].label or ""),
                )
                for name in names
            ]
            yield title, rows

    def clean_minor_hot_budget_fraction(self) -> float:
        """``SettingsBody``: ``exclusiveMinimum`` 0, ``maximum`` 1 (#168)."""
        value: float = self.cleaned_data["minor_hot_budget_fraction"]
        if not 0 < value <= 1:
            raise forms.ValidationError(FRACTION_BOUNDS)
        return value

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        data = self.cleaned_data
        for name in NULLABLE:
            if name not in data:
                continue  # already invalid
            if data.get(CLEAR_PREFIX + name):
                data[name] = None
            elif data[name] in (None, "") and self.original.get(name) is not None:
                self.add_error(name, "Enter a value, or tick Clear to unset it.")
            elif data[name] == "":
                data[name] = None
        return cleaned

    def patch(self) -> dict[str, Any]:
        """The ``SettingsPatch`` body: only the settings whose value differs from the
        original (empty when nothing changed). Call after ``is_valid()``."""
        body: dict[str, Any] = {}
        for name in setting_names():
            field = self.fields[name]
            if field.disabled or name not in self.cleaned_data:
                continue
            value = self.cleaned_data[name]
            if isinstance(field, ModelTierField):
                chosen = strongest(value)
                if chosen != strongest(self.original.get(name)):
                    body[name] = {STRONGEST: chosen}  # never a top-level null
            elif isinstance(field, LinesField) and field.set_semantics:
                changed = _set_change(value, self.original.get(name))
                if changed is not None:
                    body[name] = changed
            elif not _same(value, self.original.get(name)):
                body[name] = value
        return body


def _set_change(new: list[str], old: Any) -> list[str] | None:
    """C9's set rule (#241): ``None`` when ``new`` holds the same entries as ``old`` (lowercased,
    deduplicated; a missing or null original is ``[]``), else the list to send: the original's
    stored order with removed entries dropped and new ones appended in the order typed."""
    stored = list(dict.fromkeys(str(e).lower() for e in old)) if isinstance(old, list) else []
    if set(new) == set(stored):
        return None
    kept = [entry for entry in stored if entry in new]
    return kept + [entry for entry in new if entry not in stored]


def _same(new: Any, old: Any) -> bool:
    """Equal as JSON values: numbers by value (``60`` is ``60.0``), never ``True`` and ``1``."""
    if isinstance(new, bool) or isinstance(old, bool):
        return type(new) is type(old) and new == old
    if isinstance(new, int | float) and isinstance(old, int | float):
        return float(new) == float(old)
    return bool(new == old) and type(new) is type(old)


def setting_names() -> tuple[str, ...]:
    """Every setting the form edits (its fields minus the Clear checkboxes), in page order."""
    return tuple(name for _, names in SECTIONS for name in names)


# --- the profile-defaults table ---------------------------------------------------------------


def profile_default_rows(document: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Every leaf of the ``profile_defaults`` document as ``(dotted path, value)`` in Engine
    order. Objects and maps (``models.purposes``) flatten by key; anything else (``null``,
    lists, scalars, an empty object) is a leaf."""
    if not isinstance(document, dict) or (not document and prefix):
        return [(prefix, document)] if prefix else []
    rows: list[tuple[str, Any]] = []
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            rows.extend(profile_default_rows(value, path))
        else:
            rows.append((path, value))
    return rows
