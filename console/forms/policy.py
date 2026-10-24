"""Guardrail policy forms, shared by the deployment's policies (#47) and a story's own (#54).

:class:`PolicyContentForm` is a policy's content: name, targets (``applies_to``), the rule,
age condition and message. Its :meth:`~PolicyContentForm.content` is the body every policy
write shares (``PolicyReplacement``; ``StoryPolicyItem`` and ``PolicyFields`` add their scope
and id to it). ``effect`` is never a field: it is always sent as ``"deny"``.

One "rule type" select sets the rule and with it the policy's ``kind``. Every rule input is
shown (no JS) and only the chosen type's is validated:

- ``wordlist``: one word or phrase per line; ``regex``: one Python pattern per line (each must
  compile); ``entity_list``: one name per line. Lines are trimmed, blank lines dropped and
  duplicates removed.
- ``model_assisted``: a prompt and ``on_uncertain`` (``violation``, the conservative default,
  or ``pass``).

Which rule types are offered is the caller's: #47 offers :data:`DEPLOYMENT_RULE_TYPES`
(``entity_list`` names story characters, so it is #54's: pass :data:`STORY_RULE_TYPES`). A rule
of a type not offered (an ``entity_list`` rule on a global policy) is ``kept_rule``: it is shown
read-only and sent back unchanged.

:class:`PolicyCreateForm` adds the deployment form's optional id and its one combined scope
select ("Global", "Rating: Teen", …). #54 adds its own scope (story or one character) to a
:class:`PolicyContentForm` subclass instead.

Bounds are the recorded schema's (``PolicyFields``, ``WordlistRule``, ``RegexRule``,
``EntityListRule``, ``ModelAssistedRule``); anything else is left to the Engine's 422, which
:data:`POLICY_ERROR_PATHS` places on these fields.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from django import forms

from console.engine.models import (
    AGE_CONDITIONS,
    POLICY_SCOPES,
    POLICY_TARGETS,
    Policy,
    PolicyKind,
    PolicyScope,
)

NAME_MAX = 80
MESSAGE_MAX = 500
PROMPT_MAX = 4000
WORD_MAX, WORDS_MAX = 200, 2000
PATTERN_MAX, PATTERNS_MAX = 500, 20
ENTITY_NAME_MAX, ENTITY_NAMES_MAX = 200, 2000

EFFECT = "deny"  # the only effect: lower scopes add policies and never relax them
RULE_LABELS = {
    "wordlist": "Wordlist (deterministic)",
    "regex": "Regex (deterministic)",
    "entity_list": "Entity list (deterministic)",
    "model_assisted": "Model-assisted (a guardrail_check model call per check)",
}
DEPLOYMENT_RULE_TYPES = ("wordlist", "regex", "model_assisted")
STORY_RULE_TYPES = ("wordlist", "regex", "entity_list", "model_assisted")
# The input that holds each rule type's entries.
RULE_INPUTS = {
    "wordlist": "words",
    "regex": "patterns",
    "entity_list": "names",
    "model_assisted": "prompt",
}
ON_UNCERTAIN_CHOICES = [
    ("violation", "violation — treat it as a breach (conservative)"),
    ("pass", "pass — let it through"),
]
NO_AGE_CONDITION = ""  # sent as null
AGE_CONDITION_LABELS = {
    "minor_involved_or_present": "minor_involved_or_present — only when a minor is involved "
    "or present",
}
AGE_CONDITION_CHOICES = [(NO_AGE_CONDITION, "none")] + [
    (value, AGE_CONDITION_LABELS.get(value, value)) for value in AGE_CONDITIONS
]
GLOBAL_SCOPE = "global"
RATING_PREFIX = "rating:"
# The Engine's default targets for a new policy (``applies_to`` omitted).
DEFAULT_TARGETS = ["text_input", "text_output"]


def target_label(target: str) -> str:
    """A target as the checkboxes show it: image targets are used from M8."""
    return f"{target} (used from M8)" if target.startswith("image_") else target


def kind_of(rule_type: str) -> PolicyKind:
    return "model_assisted" if rule_type == "model_assisted" else "deterministic"


def entry_lines(text: str) -> list[str]:
    """One entry per line: trimmed, blank lines dropped, duplicates removed (first kept)."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        entry = line.strip()
        if entry:
            seen.setdefault(entry, None)
    return list(seen)


def rating_scope_value(rating_id: str) -> str:
    """The scope select's value for a rating: ``rating:<id>``."""
    return f"{RATING_PREFIX}{rating_id}"


def policy_initial(policy: Policy) -> dict[str, Any]:
    """A :class:`PolicyContentForm`'s initial values from a policy as the Engine returned it."""
    rule = policy.rule
    initial: dict[str, Any] = {
        "name": policy.name,
        "applies_to": list(policy.applies_to),
        "rule_type": policy.rule_type,
        "age_condition": policy.age_condition or NO_AGE_CONDITION,
        "message": policy.message,
    }
    for rule_type, name in RULE_INPUTS.items():
        if policy.rule_type != rule_type:
            continue
        value = rule.get(name)
        if isinstance(value, list):
            initial[name] = "\n".join(str(entry) for entry in value)
        elif isinstance(value, str):
            initial[name] = value
    on_uncertain = rule.get("on_uncertain")
    if isinstance(on_uncertain, str):
        initial["on_uncertain"] = on_uncertain
    return initial


class PolicyContentForm(forms.Form):
    """A policy's content; see the module docstring."""

    name = forms.CharField(label="Name", max_length=NAME_MAX)
    applies_to = forms.MultipleChoiceField(
        label="Applies to",
        choices=[(target, target_label(target)) for target in POLICY_TARGETS],
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Choose at least one target."},
    )
    rule_type = forms.ChoiceField(label="Rule type")
    words = forms.CharField(
        label="Words",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Wordlist: one word or phrase per line; whole words, any case.",
    )
    patterns = forms.CharField(
        label="Patterns",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Regex: one Python regular expression per line, searched anywhere, any case.",
    )
    prompt = forms.CharField(
        label="Prompt",
        max_length=PROMPT_MAX,
        required=False,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Model-assisted: what the guardrail_check model evaluates.",
    )
    on_uncertain = forms.ChoiceField(
        label="When the model is uncertain",
        choices=ON_UNCERTAIN_CHOICES,
        initial="violation",
        required=False,
        help_text="Model-assisted only.",
    )
    age_condition = forms.ChoiceField(
        label="Age condition", choices=AGE_CONDITION_CHOICES, required=False
    )
    message = forms.CharField(
        label="Message",
        max_length=MESSAGE_MAX,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="What a refusal says when the policy fires.",
    )

    def __init__(
        self,
        *args: Any,
        rule_types: Sequence[str] = DEPLOYMENT_RULE_TYPES,
        kept_rule: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """``rule_types``: the rule types offered. ``kept_rule``: the policy's current rule
        when its type is not offered; it stays selectable and is sent back unchanged."""
        super().__init__(*args, **kwargs)
        choices = [(rule_type, RULE_LABELS[rule_type]) for rule_type in rule_types]
        self.kept_rule = dict(kept_rule) if kept_rule is not None else None
        kept_type = self.kept_rule.get("type") if self.kept_rule is not None else None
        if isinstance(kept_type, str) and kept_type not in rule_types:
            label = RULE_LABELS.get(kept_type, kept_type)
            choices.append((kept_type, f"{label} — kept as is, not editable here"))
        else:
            self.kept_rule = None
        field = self.fields["rule_type"]
        assert isinstance(field, forms.ChoiceField)  # noqa: S101 - declared above
        field.choices = choices
        if "entity_list" in rule_types:
            self.fields["names"] = forms.CharField(
                label="Names",
                required=False,
                widget=forms.Textarea(attrs={"rows": 4}),
                help_text="Entity list: one name per line. Blank at story or character scope: "
                "the story's characters.",
            )
        # The rule inputs follow the rule type select, in RULE_INPUTS order.
        order = [name for name in self.fields if name not in RULE_INPUTS.values()]
        at = order.index("rule_type") + 1
        inputs = [name for name in RULE_INPUTS.values() if name in self.fields]
        self.order_fields([*order[:at], *inputs, *order[at:]])

    @property
    def kept_type(self) -> str | None:
        """The type of the rule kept as is, if any."""
        if self.kept_rule is None:
            return None
        kept = self.kept_rule.get("type")
        return kept if isinstance(kept, str) else None

    # --- validation -------------------------------------------------------------------------

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        assert cleaned is not None  # noqa: S101 - Form.clean returns cleaned_data
        rule_type = cleaned.get("rule_type")
        if rule_type == "wordlist":
            self._entries("words", cleaned, WORD_MAX, WORDS_MAX, "word or phrase")
        elif rule_type == "regex":
            self._patterns(cleaned)
        elif rule_type == "entity_list" and rule_type != self.kept_type:
            self._entries("names", cleaned, ENTITY_NAME_MAX, ENTITY_NAMES_MAX, "name", least=0)
        elif rule_type == "model_assisted":
            if not cleaned.get("prompt"):
                self.add_error("prompt", "Enter the prompt the guardrail check model evaluates.")
            if not cleaned.get("on_uncertain"):
                cleaned["on_uncertain"] = "violation"
        return cleaned

    def _entries(
        self,
        name: str,
        cleaned: dict[str, Any],
        longest: int,
        most: int,
        noun: str,
        *,
        least: int = 1,
    ) -> list[str] | None:
        entries = entry_lines(cleaned.get(name) or "")
        problems = []
        if len(entries) < least:
            problems.append(f"Enter at least one {noun}.")
        if len(entries) > most:
            problems.append(f"At most {most} entries ({len(entries)} given).")
        for number, entry in enumerate(entries, start=1):
            if len(entry) > longest:
                problems.append(f"Line {number} is longer than {longest} characters.")
        if problems:
            self.add_error(name, problems)
            return None
        cleaned[name] = entries
        return entries

    def _patterns(self, cleaned: dict[str, Any]) -> None:
        patterns = self._entries("patterns", cleaned, PATTERN_MAX, PATTERNS_MAX, "pattern")
        if patterns is None:
            return
        problems = []
        for number, pattern in enumerate(patterns, start=1):
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                problems.append(f"Pattern {number} does not compile: {exc}.")
        if problems:
            self.add_error("patterns", problems)

    # --- request bodies ---------------------------------------------------------------------

    def kind(self) -> PolicyKind:
        return kind_of(self.cleaned_data["rule_type"])

    def rule(self) -> dict[str, Any]:
        """The ``rule`` object for the chosen type. Call after ``is_valid()``."""
        data = self.cleaned_data
        rule_type: str = data["rule_type"]
        if self.kept_rule is not None and rule_type == self.kept_type:
            return dict(self.kept_rule)
        if rule_type == "model_assisted":
            return {
                "type": rule_type,
                "prompt": data["prompt"],
                "on_uncertain": data["on_uncertain"],
            }
        name = RULE_INPUTS[rule_type]
        return {"type": rule_type, name: data[name]}

    def content(self) -> dict[str, Any]:
        """The fields every policy write shares (a ``PolicyReplacement`` body): name, targets in
        the Engine's order, rule, age condition (``None`` for none), message and ``deny``."""
        data = self.cleaned_data
        chosen = set(data["applies_to"])
        return {
            "name": data["name"],
            "applies_to": [target for target in POLICY_TARGETS if target in chosen],
            "rule": self.rule(),
            "age_condition": data["age_condition"] or None,
            "message": data["message"],
            "effect": EFFECT,
        }


class PolicyCreateForm(PolicyContentForm):
    """A new global or rating policy (``POST /admin/policies``): an optional id and the scope.

    ``ratings`` are the scope select's ``(id, label)`` rating options (non-retired, in display
    order), or ``None`` when they could not be read: rating scope is then unavailable.
    """

    id = forms.CharField(
        label="Id (slug)",
        required=False,
        help_text="Optional. Blank: the Engine assigns a ULID. Fixed once created.",
    )
    scope = forms.CharField(label="Scope", widget=forms.Select)

    field_order = ("id", "scope")

    def __init__(
        self, *args: Any, ratings: Sequence[tuple[str, str]] | None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.ratings = list(ratings) if ratings is not None else None
        options = [(GLOBAL_SCOPE, "Global")] + [
            (rating_scope_value(rating_id), f"Rating: {label}")
            for rating_id, label in self.ratings or []
        ]
        self.fields["scope"].widget = forms.Select(choices=options)

    def clean_scope(self) -> tuple[str, str | None]:
        """``(scope, scope_id)``: ``global`` or a known rating; a bare rating is refused."""
        value: str = self.cleaned_data["scope"]
        if value == GLOBAL_SCOPE:
            return GLOBAL_SCOPE, None
        if value == "rating" or value.startswith(RATING_PREFIX):
            if self.ratings is None:
                raise forms.ValidationError(
                    "The ratings could not be read, so rating scope is unavailable."
                )
            rating_id = value.removeprefix(RATING_PREFIX) if value != "rating" else ""
            if not rating_id:
                raise forms.ValidationError("A rating-scoped policy needs a rating: choose one.")
            if rating_id not in {known for known, _ in self.ratings}:
                raise forms.ValidationError("Choose one of the listed ratings.")
            return "rating", rating_id
        raise forms.ValidationError("Choose Global or one of the ratings.")

    def fields_body(self) -> dict[str, Any]:
        """The ``PolicyFields`` body: :meth:`content` with the scope (``scope_id`` for a rating)
        and the id when one was given."""
        scope, scope_id = self.cleaned_data["scope"]
        body: dict[str, Any] = {}
        if self.cleaned_data["id"]:
            body["id"] = self.cleaned_data["id"]
        body["scope"] = scope
        if scope_id is not None:
            body["scope_id"] = scope_id
        body.update(self.content())
        return body


_FIXED_ERROR_PATHS = {"rule": "rule_type", "rule.type": "rule_type", "scope_id": "scope"}


class PolicyErrorPaths(Mapping[str, str]):
    """Where an Engine 422 on a policy body belongs (``apply_engine_errors``' ``aliases``).

    The Engine names a rule failure with the rule's type in the path
    (``rule.wordlist.words``, ``rule.regex.patterns[1]``, ``rule.model_assisted.prompt``);
    those, and list items (``applies_to[0]``), go to the input the form shows them in.
    """

    _RULE = re.compile(
        r"^rule\.(?:(?:wordlist|regex|entity_list|model_assisted)\.)?"
        r"(?P<field>words|patterns|names|prompt|on_uncertain)(?:\[\d+\])?$"
    )
    _ITEM = re.compile(r"^(?P<field>applies_to)\[\d+\]$")

    def __getitem__(self, path: str) -> str:
        if path in _FIXED_ERROR_PATHS:
            return _FIXED_ERROR_PATHS[path]
        match = self._RULE.match(path) or self._ITEM.match(path)
        if match is None:
            raise KeyError(path)
        return match["field"]

    def __iter__(self) -> Iterator[str]:
        return iter(_FIXED_ERROR_PATHS)

    def __len__(self) -> int:
        return len(_FIXED_ERROR_PATHS)


POLICY_ERROR_PATHS = PolicyErrorPaths()


# --- The deployment's policy list (#47) --------------------------------------------------------

SCOPE_FILTER_CHOICES = [
    ("", "All"),
    ("global", "Global"),
    ("rating", "Rating"),
    ("story", "Story"),
    ("character", "Character"),
]


class PolicyFilterForm(forms.Form):
    """``?scope=`` and ``?retired=1`` (sent as ``include_retired=1``). An unknown scope is
    ignored (every scope, no ``scope`` sent)."""

    scope = forms.ChoiceField(choices=SCOPE_FILTER_CHOICES, required=False)
    retired = forms.BooleanField(label="Show retired", required=False)

    def chosen(self) -> tuple[PolicyScope | None, bool]:
        """``(scope, include_retired)``."""
        if not self.is_valid():
            return None, self.data.get("retired") == "1"
        scope = self.cleaned_data["scope"]
        chosen: PolicyScope | None = scope if scope in POLICY_SCOPES else None
        return chosen, bool(self.cleaned_data["retired"])
