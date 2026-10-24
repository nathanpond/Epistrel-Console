"""Deployment settings (#50): one hand-written form over ``PUT /admin/settings``.

One field per top-level ``SettingsPatch`` property except ``profile_defaults`` (shown read-only,
edited by M3 #84), each with the bounds the recorded ``SettingsBody`` states (#43) and the
schema default as help text. A guard test compares the fields with the recorded schema.

It is a merge-patch form (#43): the page carries the original settings, signed, and
:meth:`DeploymentSettingsForm.patch` returns only the settings whose value changed. Lists are
one entry per line and sent whole when changed. Only the three nullable settings
(:data:`NULLABLE`) have a "Clear" checkbox (``clear_<name>``), which sends ``null`` to unset them;
no other setting can send ``null`` and there is no reset-to-default.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django import forms

STORY_LIFETIME = "story_lifetime"
DAYS = "days"
RETENTION_DAYS_MIN, RETENTION_DAYS_MAX = 1, 3650  # feedback_retention's integer branch
URL_MAX = 2048
ID_MAX = 64
FALLBACK_MAX = 500
LIST_MAX = 50
CLEAR_PREFIX = "clear_"
NAME_PLACEHOLDER_HELP = "{name} is replaced by the character's name."
# The settings that accept null (unset): no hook, no base URL, no ceiling.
NULLABLE = ("media_moderation_webhook_url", "public_base_url", "max_characters_at_start_ceiling")
# Items of the list settings, as ``SettingsBody`` states them.
FEEDBACK_TAG = re.compile(r"^[a-z0-9_]{1,40}$")
MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$")
LISTING_STATUS = re.compile(
    r"^[\t ]*[^\x00-\x1f\x7f-\x9f\t ]([^\x00-\x1f\x7f-\x9f]{0,38}[^\x00-\x1f\x7f-\x9f\t ])?[\t ]*$"
)

# The schema default of each setting that states one, shown as help text ("Default: …").
DEFAULTS: dict[str, Any] = {}
# Units the setting's name or its Engine meaning gives, shown after the input.
UNITS: dict[str, str] = {}


def _help(default: Any, extra: str = "") -> str:
    shown = "unset" if default is None else _shown(default)
    text = f"Default: {shown}."
    return f"{extra} {text}" if extra else text


def _shown(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value == STORY_LIFETIME:
        return "story lifetime"
    return str(value)


def _int(
    name: str, lo: int, hi: int, default: int | None, unit: str = "", *, required: bool = True
) -> forms.IntegerField:
    DEFAULTS[name] = default
    if unit:
        UNITS[name] = unit
    return forms.IntegerField(
        required=required, min_value=lo, max_value=hi, help_text=_help(default)
    )


def _number(name: str, lo: float, hi: float, default: float, unit: str = "") -> forms.FloatField:
    DEFAULTS[name] = default
    if unit:
        UNITS[name] = unit
    return forms.FloatField(
        min_value=lo,
        max_value=hi,
        help_text=_help(default),
        widget=forms.NumberInput(attrs={"step": "any"}),
    )


def _bool(name: str, default: bool) -> forms.BooleanField:
    DEFAULTS[name] = default
    return forms.BooleanField(required=False, help_text=_help(default))


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
    dropped). Each entry must match ``item``; the count is bounded as the schema states."""

    widget = forms.Textarea(attrs={"rows": 5})

    def __init__(
        self,
        *,
        item: re.Pattern[str],
        item_rule: str,
        min_items: int,
        max_items: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(required=False, **kwargs)
        self.item = item
        self.item_rule = item_rule
        self.min_items = min_items
        self.max_items = max_items

    def prepare_value(self, value: Any) -> Any:
        return "\n".join(map(str, value)) if isinstance(value, list) else value

    def to_python(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v) for v in value]
        text = str(value or "")
        return [line.strip() for line in text.splitlines() if line.strip()]

    def validate(self, value: Any) -> None:
        entries: list[str] = value
        bad = [entry for entry in entries if not self.item.match(entry)]
        if bad:
            raise forms.ValidationError(f"Not {self.item_rule}: {', '.join(bad)}")
        if len(entries) < self.min_items:
            raise forms.ValidationError(f"List at least {self.min_items} entry.")
        if len(entries) > self.max_items:
            raise forms.ValidationError(f"List at most {self.max_items} entries.")


def _lines(
    name: str, item: re.Pattern[str], item_rule: str, *, min_items: int, extra: str
) -> LinesField:
    return LinesField(
        item=item,
        item_rule=item_rule,
        min_items=min_items,
        max_items=LIST_MAX,
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


def _clear(name: str) -> forms.BooleanField:
    return forms.BooleanField(
        required=False, label="Clear", help_text=f"Send null: unset {name.replace('_', ' ')}."
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
    (
        "Memory and retrieval",
        (
            "memory_importance_half_life_turns",
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
    """One setting on the page: its field, unit suffix and (nullable only) Clear checkbox."""

    name: str
    field: forms.BoundField
    unit: str
    clear: forms.BoundField | None


class DeploymentSettingsForm(forms.Form):
    """Every deployment setting but ``profile_defaults``. ``original`` is the settings as
    read (``SettingsBody`` JSON); ``ratings`` the default-rating select's ``(id, label)``
    options, or ``None`` when the ratings could not be read (the field is then read-only)."""

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
    # Memory and retrieval
    memory_importance_half_life_turns = _int(
        "memory_importance_half_life_turns", 1, 1000000, 200, "turns"
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

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        original: Mapping[str, Any],
        ratings: Sequence[tuple[str, str]] | None,
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
                )
                for name in names
            ]
            yield title, rows

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
            if not _same(value, self.original.get(name)):
                body[name] = value
        return body


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
