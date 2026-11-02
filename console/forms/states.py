"""The one-State page's GET form (#242): D4's viewpoint picker plus D7's "As of" boundary."""

from typing import Any

from django import forms

from console.engine.client import EngineError
from console.forms.world import BoundaryField, WorldViewpointForm

AS_OF_HELP = "A turn (12), or a turn and slot (12+1); blank for now"
AS_OF_FIELD = "query.as_of"
HEAD_TEXT = "The story's head is turn {head}"


class StateAsOfForm(WorldViewpointForm):
    """``viewpoint`` (D4) and ``as_of`` (D7's ``BoundaryField``, optional: blank sends no
    ``as_of``, ``12⁺¹`` is sent as ``12+1``). No HTML ``min``/``max`` or ``maxlength`` (C10)."""

    as_of = BoundaryField(
        label="As of",
        required=False,
        help_text=AS_OF_HELP,
        widget=forms.TextInput(),
    )

    def query(self) -> dict[str, Any]:
        """The read's ``viewpoint`` and ``as_of`` keyword arguments (valid form only)."""
        return {**super().query(), "as_of": self.cleaned_data.get("as_of") or None}

    def apply_engine_error(self, exc: EngineError) -> None:
        """A ``validation_error`` on ``query.as_of`` beside As of ("The story's head is turn
        <head>" when ``details.head`` is an int, else the Engine's message); anything else as
        D4's form places it."""
        details = exc.details if isinstance(exc.details, dict) else {}
        if exc.code == "validation_error" and details.get("field") == AS_OF_FIELD:
            head = details.get("head")
            if isinstance(head, int) and not isinstance(head, bool):
                self.add_error("as_of", HEAD_TEXT.replace("{head}", str(head)))
            else:
                self.add_error("as_of", exc.message)
            return
        super().apply_engine_error(exc)
