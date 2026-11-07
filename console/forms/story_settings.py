"""A story's settings page forms (#54): settings, one story policy, pinned memories and custom
instructions. Each section has its own form and its own write.

- :class:`StorySettingsForm` edits the seventeen settable ``StorySettingsPatch`` fields as a
  merge patch (#43): the original values travel in the page's signed snapshot,
  :meth:`~StorySettingsForm.patch` holds only the fields whose value changed, and each field's
  "reset to default" checkbox (``reset_<name>``) sends ``null``. Bounds are the recorded
  schema's ranges; everything else is the Engine's 422. Engine M5's six (#197) are selects
  built with C7 (:mod:`console.story_settings`): the three nullable ones offer
  "Engine default (…)" first, and a stored value outside the choices is kept as one more.
  Engine M6's co-author knowledge (#233) is a required select like player authority. Engine
  M7's (#318) backfill conflict policy and ambiguous retcon timing are required selects too;
  its prune policy is a :class:`PrunePolicyField`, a days input that sends
  ``{"older_than_days": n}`` (blank: ``null``, off), compared as JSON.
- :class:`StoryPolicyForm` is #47's :class:`~console.forms.policy.PolicyContentForm` with the
  story's rule types (``entity_list`` included), a scope (the story or one character) and an
  ``effect`` (``deny``, or ``allow`` so the Engine's ``cannot_relax`` is reachable); its
  :meth:`~StoryPolicyForm.item` is one ``StoryPolicyItem``.
- :class:`PinnedFormSet` holds the pinned memories in order (add, remove, up and down are
  :mod:`console.formset_rows` buttons); :class:`CustomInstructionsForm` the instructions.
  Both count code points after NFC normalisation and trimming, as the Engine does.
"""

import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from django import forms
from django.forms import formset_factory
from django.forms.formsets import BaseFormSet

from console.cast import CastMember, character_choices, character_of, viewpoint_for
from console.engine.models import Policy
from console.forms.policy import (
    POLICY_ERROR_PATHS,
    STORY_RULE_TYPES,
    PolicyContentForm,
    policy_initial,
)
from console.raw import CELL_LIMIT, compact_json, plural
from console.story_settings import (
    BACKFILL_CONFLICT_HELP,
    BOOLEAN_SELECT,
    CO_AUTHOR_KNOWLEDGE_HELP,
    LABELS,
    MULTI_CHARACTER,
    NULLABLE,
    PINNED_ITEM_MAX,
    PRUNE_DAYS,
    PRUNE_MAX_DAYS,
    PRUNE_MIN_DAYS,
    PRUNE_POLICY_HELP,
    RETCON_TIMING_HELP,
    SETTABLE,
    SETTING_CHOICES,
    Marker,
    engine_default_choice,
    flash_name,
    kept_choices,
    marker,
    null_default_text,
    policy_days,
    same_value,
    select_bool,
    tie_break_label,
)
from console.wording import humanised

RESET_PREFIX = "reset_"
PRUNE_POLICY = "prune_policy"
NO_OVERRIDE = ""
QUEUE_NOTE = "Used only when turn concurrency is queue. 1 to 50."


def text_length(text: str) -> tuple[str, int]:
    """``text`` NFC-normalised and trimmed, and its length in code points (the Engine's rule)."""
    normal = unicodedata.normalize("NFC", text).strip()
    return normal, len(normal)


# --- settings -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingRow:
    """One setting on the page: its input, its reset checkbox and its marker."""

    name: str
    field: forms.BoundField
    reset: forms.BoundField
    marker: Marker


class PrunePolicyField(forms.IntegerField):
    """``prune_policy`` as a day count (#318): blank cleans to ``None`` (``null``, off), a number
    to ``{"older_than_days": n}``. Its bounds are the recorded ``PrunePolicy.older_than_days``."""

    MIN_DAYS = PRUNE_MIN_DAYS
    MAX_DAYS = PRUNE_MAX_DAYS

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        super().__init__(min_value=self.MIN_DAYS, max_value=self.MAX_DAYS, **kwargs)

    def clean(self, value: Any) -> dict[str, int] | None:
        days = super().clean(value)
        return None if days is None else {PRUNE_DAYS: days}


def prune_policy_baseline(stored: object) -> dict[str, int] | None:
    """What an untouched prune policy input cleans to: the stored policy when well formed
    (:func:`~console.story_settings.policy_days`), else ``None`` (blank)."""
    days = policy_days(stored)
    return None if days is None else {PRUNE_DAYS: days}


def prune_policy_text(value: Mapping[str, Any] | None) -> str:
    """The flash's value: "older than 30 days" ("1 day", E23), or "off" for ``null``."""
    if value is None:
        return "off"
    return f"older than {plural(value[PRUNE_DAYS], 'day')}"


class StorySettingsForm(forms.Form):
    """The seventeen settable story settings. ``original`` is ``settings`` as read; ``cast`` the
    viewpoint options; ``deployment`` ``GET /admin/settings`` (``None``: unread) for markers."""

    turn_concurrency = forms.ChoiceField(
        choices=[("reject", "reject"), ("queue", "queue")],
        help_text="reject refuses an overlapping turn (423 story_busy); queue queues it.",
    )
    turn_queue_max = forms.IntegerField(
        required=False, min_value=1, max_value=50, help_text=QUEUE_NOTE
    )
    default_output_viewpoint = forms.ChoiceField(
        required=False,
        help_text="The viewpoint a read or turn falls back to when it names none.",
    )
    regenerate_retry_budget = forms.IntegerField(
        min_value=0, max_value=10, help_text="0 to 10 retries."
    )
    lite_summary_chunk = forms.IntegerField(
        required=False, min_value=2, max_value=200, help_text="2 to 200 rows per summary segment."
    )
    audit_retention_days = forms.IntegerField(
        required=False, min_value=1, max_value=3650, help_text="1 to 3650 days."
    )
    allow_new_arrivals = forms.BooleanField(
        required=False,
        help_text="Whether a director pass or a render may introduce new arrivals.",
    )
    # Engine M5 (#197) and M6 (#233): never null in the read, so required selects with no
    # Engine default.
    player_authority = forms.ChoiceField(
        help_text="Lite stories get this as a prompt instruction only."
    )
    co_author_knowledge = forms.ChoiceField(help_text=CO_AUTHOR_KNOWLEDGE_HELP)
    player_view = forms.ChoiceField()
    interaction_mode = forms.ChoiceField(required=False)
    sequence_order = forms.ChoiceField(required=False)
    simultaneous_tie_break = forms.ChoiceField(required=False)
    reactive_interjections = forms.TypedChoiceField(choices=BOOLEAN_SELECT, coerce=select_bool)
    # Engine M7 (#318): the read fills the two enums in, so they are required as above.
    prune_policy = PrunePolicyField(help_text=PRUNE_POLICY_HELP)
    backfill_conflict_policy = forms.ChoiceField(help_text=BACKFILL_CONFLICT_HELP)
    ambiguous_retcon_timing = forms.ChoiceField(help_text=RETCON_TIMING_HELP)

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        original: Mapping[str, Any],
        cast: Sequence[CastMember],
        deployment: Mapping[str, Any] | None = None,
    ) -> None:
        self.original = {name: original.get(name) for name in SETTABLE}
        self.deployment = deployment
        found = original.get("inactive")
        self.inactive = found if isinstance(found, Mapping) else None
        initial = {name: ("" if value is None else value) for name, value in self.original.items()}
        stored_bool = self.original["reactive_interjections"]
        if isinstance(stored_bool, bool):
            initial["reactive_interjections"] = "true" if stored_bool else "false"
        stored_policy = self.original[PRUNE_POLICY]
        days = policy_days(stored_policy)
        initial[PRUNE_POLICY] = "" if days is None else days
        super().__init__(data, initial=initial)
        if stored_policy is not None and days is None:
            # An odd stored policy renders blank; say what it holds (E23's compact JSON).
            self.fields[PRUNE_POLICY].help_text = (
                f"{PRUNE_POLICY_HELP} (stored: {compact_json(stored_policy, CELL_LIMIT)}; "
                "saving a value or Reset replaces it)"
            )
        for name in SETTABLE:
            self.fields[name].label = LABELS[name]
            self.fields[RESET_PREFIX + name] = forms.BooleanField(
                required=False, label="Reset to default", help_text="Sends null."
            )
        viewpoint = self.fields["default_output_viewpoint"]
        assert isinstance(viewpoint, forms.ChoiceField)  # noqa: S101 - declared above
        choices = [(NO_OVERRIDE, "— no override (default) —")] + [
            (viewpoint_for(cid), label) for cid, label in character_choices(cast)
        ]
        current = self.original["default_output_viewpoint"]
        if isinstance(current, str) and current not in {value for value, _ in choices}:
            unknown = character_of(current) is None
            choices.append((current, f"{current} ({'not a character' if unknown else 'unknown'})"))
        viewpoint.choices = choices
        self._m5_choices()

    def _m5_choices(self) -> None:
        """C7: each M5, M6 and M7 select's choices, a stored value they lack kept as one more."""
        selects = (
            "player_authority",
            "co_author_knowledge",
            "player_view",
            *MULTI_CHARACTER,
            "backfill_conflict_policy",
            "ambiguous_retcon_timing",
        )
        for name in selects:
            field = self.fields[name]
            assert isinstance(field, forms.ChoiceField)  # noqa: S101 - declared above
            label = tie_break_label if name == "simultaneous_tie_break" else humanised
            kept = kept_choices(SETTING_CHOICES[name], self.original[name], label)
            if name in MULTI_CHARACTER:
                kept = [engine_default_choice(null_default_text(name, self.inactive)), *kept]
            field.choices = kept

    def rows(self) -> Iterator[SettingRow]:
        for name in SETTABLE:
            yield SettingRow(
                name=name,
                field=self[name],
                reset=self[RESET_PREFIX + name],
                marker=marker(name, self.original[name], self.deployment, inactive=self.inactive),
            )

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        data = self.cleaned_data
        for name in SETTABLE:
            if data.get(RESET_PREFIX + name):
                data[name] = None
                self.errors.pop(name, None)  # a reset ignores what the input held
            elif name in NULLABLE and data.get(name) in ("", None) and name in data:
                data[name] = None  # blank: no override
        return cleaned

    def patch(self) -> dict[str, Any]:
        """The merge patch: each setting whose value changed (``null`` for a reset), in page
        order; empty when nothing changed. Call after ``is_valid()``. The prune policy compares with
        what its input rendered (C9): an odd stored policy renders blank, so a blank sends
        nothing."""
        body: dict[str, Any] = {}
        for name in SETTABLE:
            if name not in self.cleaned_data:
                continue
            value = self.cleaned_data[name]
            original = self.original[name]
            baseline = prune_policy_baseline(original) if name == PRUNE_POLICY else original
            if self.cleaned_data.get(RESET_PREFIX + name):
                if original is not None:
                    body[name] = None
            elif not same_value(value, baseline):
                body[name] = value
        return body

    def changes(self, patch: Mapping[str, Any]) -> list[str]:
        """The flash's "<Name>: <value>" for each key of ``patch``: "default" for ``null``, the
        chosen option's label for a select, "On" / "Off" for a boolean, else ``str(value)``."""
        return [
            f"{flash_name(LABELS[name])}: {self._shown(name, value)}"
            for name, value in patch.items()
        ]

    def _shown(self, name: str, value: Any) -> str:
        if name == PRUNE_POLICY:
            return prune_policy_text(value)
        if value is None:
            return "default"
        if isinstance(value, bool):
            return "On" if value else "Off"
        field = self.fields[name]
        if isinstance(field, forms.ChoiceField):
            for option, label in cast(Iterable[tuple[Any, Any]], field.choices):
                if option == value:
                    return str(label)
        return str(value)


# Engine paths a settings 422 names that the form shows elsewhere.
SETTINGS_ERROR_PATHS = {
    "viewpoint_unresolved": "default_output_viewpoint",
    "prune_policy.older_than_days": PRUNE_POLICY,
}


# --- one story policy -----------------------------------------------------------------------------

STORY_SCOPE = "story"
CHARACTER_SCOPE = "character"
CHARACTER_PREFIX = "character:"
EFFECT_CHOICES = [
    ("deny", "deny"),
    ("allow", "allow — refused: a story policy can only add restrictions"),
]


def scope_value(policy: Policy) -> str:
    """The scope select's value for a stored policy."""
    if policy.scope == CHARACTER_SCOPE and policy.scope_id:
        return f"{CHARACTER_PREFIX}{policy.scope_id}"
    return STORY_SCOPE


def story_policy_initial(policy: Policy) -> dict[str, Any]:
    """A :class:`StoryPolicyForm`'s initial values for editing ``policy``."""
    return {
        **policy_initial(policy),
        "policy_id": policy.id,
        "scope": scope_value(policy),
        "effect": policy.effect if policy.effect in ("deny", "allow") else "deny",
    }


class StoryPolicyForm(PolicyContentForm):
    """One story or character policy (``StoryPolicyItem``). ``policy_id`` (hidden) is set when
    editing; a new policy is sent without an id and the Engine mints it."""

    shows_effect = True

    policy_id = forms.CharField(required=False, widget=forms.HiddenInput)
    scope = forms.ChoiceField(label="Scope", initial="story")
    effect = forms.ChoiceField(
        label="Effect",
        choices=EFFECT_CHOICES,
        initial="deny",
        help_text="Story policies add restrictions; allow is refused by the Engine.",
    )

    field_order = ("policy_id", "scope")

    def __init__(self, *args: Any, cast: Sequence[CastMember], **kwargs: Any) -> None:
        super().__init__(*args, rule_types=STORY_RULE_TYPES, **kwargs)
        scope = self.fields["scope"]
        assert isinstance(scope, forms.ChoiceField)  # noqa: S101 - declared above
        scope.choices = [(STORY_SCOPE, "Story")] + [
            (f"{CHARACTER_PREFIX}{cid}", f"Character: {label}")
            for cid, label in character_choices(cast)
        ]
        # Effect sits after the message, the last content field.
        order = [name for name in self.fields if name != "effect"]
        self.order_fields([*order, "effect"])

    def item(self) -> dict[str, Any]:
        """The ``StoryPolicyItem``: the policy content with the chosen effect and scope, and
        the id when editing. Call after ``is_valid()``."""
        data = self.cleaned_data
        item: dict[str, Any] = {}
        if data.get("policy_id"):
            item["id"] = data["policy_id"]
        scope: str = data["scope"]
        if scope.startswith(CHARACTER_PREFIX):
            item["scope"] = CHARACTER_SCOPE
            item["scope_id"] = scope.removeprefix(CHARACTER_PREFIX)
        else:
            item["scope"] = STORY_SCOPE
        item.update(self.content())
        item["effect"] = data["effect"]
        return item


class StoryPolicyErrorPaths(Mapping[str, str]):
    """Where a 422 on ``policies[0]`` belongs: ``policies[0].<path>`` is placed as #47's
    :data:`~console.forms.policy.POLICY_ERROR_PATHS` places ``<path>`` (``scope_id`` goes to
    the scope select)."""

    _ITEM = re.compile(r"^policies\[0\]\.(?P<rest>.+)$")

    def __getitem__(self, path: str) -> str:
        match = self._ITEM.match(path)
        if match is None:
            raise KeyError(path)
        rest = match["rest"]
        return POLICY_ERROR_PATHS.get(rest, rest)

    def __iter__(self) -> Iterator[str]:
        return (f"policies[0].{path}" for path in POLICY_ERROR_PATHS)

    def __len__(self) -> int:
        return len(POLICY_ERROR_PATHS)


STORY_POLICY_ERROR_PATHS = StoryPolicyErrorPaths()


# --- pinned memories ------------------------------------------------------------------------------

PINNED_PREFIX = "pins"
BLANK_PIN = "A pinned memory cannot be blank — remove the row instead."


class PinnedItemForm(forms.Form):
    """One pinned memory: its Engine id (hidden; blank for a new row) and its text."""

    id = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)
    text = forms.CharField(
        required=False, strip=False, widget=forms.Textarea(attrs={"rows": 2, "cols": 80})
    )

    def clean_text(self) -> str:
        text, length = text_length(self.cleaned_data.get("text") or "")
        if not text:
            raise forms.ValidationError(BLANK_PIN)
        if length > PINNED_ITEM_MAX:
            raise forms.ValidationError(
                f"At most {PINNED_ITEM_MAX:,} characters ({length:,} given)."
            )
        return text

    def item(self) -> dict[str, Any]:
        data = self.cleaned_data
        return {"id": data["id"], "text": data["text"]} if data["id"] else {"text": data["text"]}


if TYPE_CHECKING:
    PinnedFormSetBase = BaseFormSet[PinnedItemForm]
else:
    PinnedFormSetBase = BaseFormSet


class BasePinnedFormSet(PinnedFormSetBase):
    """The pinned memories in order: at most ``cap`` (the deployment's ``max_pinned_memories``);
    zero is allowed (it clears them)."""

    cap = 50

    def clean(self) -> None:
        super().clean()
        if len(self.forms) > self.cap:
            raise forms.ValidationError(
                f"At most {self.cap} pinned memories (the deployment's max_pinned_memories); "
                f"{len(self.forms)} given."
            )

    def items(self) -> list[dict[str, Any]]:
        """The request's ``items``; call after ``is_valid()``."""
        return [form.item() for form in self.forms]


# formset_factory builds a subclass of ``formset=``; the stubs type it as the generic base.
PinnedFormSetClass = cast(
    type[BasePinnedFormSet],
    formset_factory(PinnedItemForm, formset=BasePinnedFormSet, extra=0, max_num=10_000),
)


def pinned_formset(
    data: Mapping[str, Any] | None,
    *,
    cap: int,
    initial: Sequence[Mapping[str, Any]] | None = None,
) -> BasePinnedFormSet:
    formset = PinnedFormSetClass(data, prefix=PINNED_PREFIX, initial=initial)
    formset.cap = cap
    return formset


# --- custom instructions --------------------------------------------------------------------------


class CustomInstructionsForm(forms.Form):
    """The story's custom instructions; whitespace only (or empty) clears them."""

    text = forms.CharField(
        label="Custom instructions",
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 6, "cols": 80}),
    )

    def __init__(self, *args: Any, cap: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cap = cap
        self.fields["text"].help_text = (
            f"Standing guidance in every prompt, never facts. Up to {cap:,} characters; "
            "empty clears them."
        )

    def clean_text(self) -> str:
        text, length = text_length(self.cleaned_data.get("text") or "")
        if length > self.cap:
            raise forms.ValidationError(f"At most {self.cap:,} characters ({length:,} given).")
        return text
