"""E14's upgrade mode (#295): the one field every M7 form that changes a story's profile asks
for — #307 (assignment and override confirm), #310 (upgrade pinned stories) and #320 (import).

``UpgradeModeField`` is a ``ChoiceField`` over :data:`UPGRADE_MODE_CHOICES` (``backfill``
initial), with :data:`UPGRADE_MODE_HELP`; it cleans to ``"backfill"`` or ``"forward_only"`` and is
sent as ``upgrade_mode`` (never ``upgradeMode``). :class:`UpgradeModeForm` is a one-field form for
the pages that bind it on its own; with ``form_id`` its radios carry ``form="<form_id>"``, so they
can sit outside the form they submit with (``config-confirm``, ``profile-apply``). The wording of
a stored value is ``console.upgrades.upgrade_mode_text``.
"""

from typing import Any

from django import forms

UPGRADE_MODE_FIELD = "upgrade_mode"
UPGRADE_MODE_CHOICES = (
    ("backfill", "Backfill the earlier turns (the story pauses while it runs)"),
    ("forward_only", "Forward only (earlier turns keep a coverage gap)"),
)
UPGRADE_MODE_HELP = (
    "Only matters when the change turns on something that produces data; otherwise it's ignored"
)
UPGRADE_MODE_LABEL = "Earlier turns"
DEFAULT_UPGRADE_MODE = "backfill"


class UpgradeModeField(forms.ChoiceField):
    """E14's field: ``backfill`` (initial) or ``forward_only``, as radios with the help."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("choices", UPGRADE_MODE_CHOICES)
        kwargs.setdefault("initial", DEFAULT_UPGRADE_MODE)
        kwargs.setdefault("help_text", UPGRADE_MODE_HELP)
        kwargs.setdefault("label", UPGRADE_MODE_LABEL)
        kwargs.setdefault("widget", forms.RadioSelect)
        super().__init__(**kwargs)


class UpgradeModeForm(forms.Form):
    """One required ``upgrade_mode`` field (``initial_mode`` pre-selected); ``form_id`` puts
    ``form="<form_id>"`` on its radios."""

    upgrade_mode = UpgradeModeField(required=True)

    def __init__(
        self,
        data: Any = None,
        *,
        form_id: str | None = None,
        initial_mode: str = DEFAULT_UPGRADE_MODE,
    ) -> None:
        super().__init__(data, initial={UPGRADE_MODE_FIELD: initial_mode})
        if form_id:
            self.fields[UPGRADE_MODE_FIELD].widget.attrs["form"] = form_id

    def mode(self) -> str:
        """The cleaned mode (call after ``is_valid()``)."""
        return str(self.cleaned_data[UPGRADE_MODE_FIELD])
