"""The shared inputs of every M6 World form (#235): the viewpoint picker, number, delta and
scalar values, story-time and as-of boundaries.

The D-list text below is the M6 cross-story conventions' own (M6 milestone description), verbatim.

**D4 — Viewpoint picker (owner #235; `console/forms/world.py`).** The World tab's reads that
take a viewpoint (States, one State, scheduled events) use `WorldViewpointForm`. It has one
`viewpoint` select: "Omniscient" (`omniscient`) first and the initial value, never the story's
default viewpoint (founder decision 9). Then comes each cast member as `character:<id>` in the
Engine's cast order, the player marked " (player)". The choices come from
`console.forms.inspect.viewpoint_choices(cast, requested)`, which #235 extracts from
`SceneForm._choices` (SceneForm calls it, unchanged). A value outside the choices is the field
error `VIEWPOINT_NOT_IN_CAST` and no read happens. Without the story read, only "Omniscient"
and the raw requested value are offered (SceneForm's rule). A 422 `viewpoint_unresolved` lands
on the field. `console.world.viewpoint_line(viewpoint, names, *, note=STATES_NOTE,
omniscient_note="")` gives "Viewpoint: Omniscient" (plus " — <omniscient_note>" when given), or
"Viewpoint: Alice — <note>" for a character (C4 label, `{label}` in `note` filled with it).
`STATES_NOTE` = "hidden States are left out", so a States page reads "Viewpoint: Alice — hidden
States are left out". The scheduled-events section (#244) passes its own notes ("every canon
event"; "only events {label} takes part in, and {label}'s own belief-only schedules"). The
client sends `viewpoint=` on these reads even though the recorded schema doesn't declare it
there (#232).

**D5 — State values (owner #235; `console/world.py`).** `state_value_text(value, type,
unit=None) -> str` formats a value:
- JSON `null` → "undefined", rendered by templates as `<span
  class="undefined">undefined</span>`;
- `number` (an int or float, not a bool) → `f"{v:,}"`, then " <unit>" when the State has a unit
  ("1,200 gold");
- `boolean` → "true" / "false";
- `enum` and `text` → verbatim. These are author values, not Engine enums, so C3's `humanised`
  does not apply;
- a value that doesn't fit its type, or an unknown type, → compact JSON (`json.dumps(...,
  ensure_ascii=False)`).

**The external-value exception.** On External values (#248) a JSON `null` reads "unknown", not
"undefined": there it means the provider gave no value (`source: unknown`, or `last_known` with
nothing fresh), not a State not yet set. It renders as `<span
class="undefined">unknown</span>`, the same styling. Every other page reads `null` as
"undefined".

`state_values(values, names, cast_order)` → `[(label, text)]` for a per-character State, in
cast order and then unknown ids, labelled by C4. `values` `null` → one row "undefined".
`signed_delta_text(delta, unit=None)` → "+5 gold" / "\u22123 gold" (U+2212) / "+0".
`change_text(old, new, type, unit)` → "<old> → <new>". The Engine's own enums on a State
(`scope`, `visibility`, `owner`, `type`) go through C3.

**Value inputs (owner #235; `console/forms/world.py`).** One number rule for every M6 form:
- `parse_number(text) -> int | float | None`: an ASCII signed decimal (`12`, `+5`, `-3`, `2.5`,
  or with U+2212 `\u22123`) → an `int` without a dot, else a `float`; anything else (an exponent, a
  thousands separator, `NaN`, non-ASCII digits, a bare sign) → `None`. Its field error is
  `NUMBER_INVALID` "Enter a number (e.g. 12, -3 or 0.5)". #251's set form, #261's constraints
  and initial value, and #267's rows use it.
- `DeltaField`: blank → `None`, else `parse_number`, failing with `DELTA_INVALID` "Enter a
  signed number, e.g. -500 or 2.5". #264 (compensate) and #260 (ingest) use it.
- `ScalarValueField`: blank → `None`; `true`/`false` → `bool`; an ASCII number (the
  `parse_number` rule) → `int`/`float`; a JSON string in double quotes → that string; any other
  text → the stripped text. Help `VALUE_HELP`: "Numbers and true/false are sent as such; put
  text in double quotes to send "12" or "true" as text." #264 and #260 use it; #251 doesn't
  (its form knows the State's type).

None carries an HTML `min`/`max` (C10).

**D6 — Clock values, story-minute offsets and time inputs (owner #235; `console/world.py`,
`console/forms/world.py`).** A clock value always shows the Engine's `display` verbatim. The
Console never computes a date or a time of day. `clock_value_html(value)` renders `<span
class="clock" title="offset <offset> min"><display></span>`. `clock_change_text(before, after)`
→ "<before.display> → <after.display>", or "<display> (unchanged)" when the offsets are equal.
A bare minutes value the Engine sends without a display (`planned`, `stopped_clock_at`, a skip
cap) reads `offset_text(m)` = "+HHh MMm", byte for byte the Engine's `world_clock.display`
(hours not wrapped into days), with "min" units in `title`. Time inputs use `PlannedField`
(#253, #262, #268 `planned`; #252 `to`): ASCII digits → an int offset; `HH:MM`
(00\u201323, 00\u201359) →
the string, sent as given; anything else → "Enter minutes from the story's start (e.g. 130) or
a time of day (HH:MM)". No HTML min/max (C10).

**D7 — Positions, slots and as-of boundaries (owner #235; `console/world.py`).** Every
five-part position M6 serves is rendered through C1's `knowledge_position_text` and linked with
`knowledge_position_url`. That covers `since_position`, a write's `slot`,
`ClockAdvanced.position` and the Engine's other position maps. A slot position therefore reads
"turn 12⁺¹ · start" (an inter-turn slot sits at beat 0, Engine `world_clock/slots.py`).
`five_part(mapping) -> NarrativePosition | None` converts a map, and a malformed map is `None`,
shown as "—". A map missing only `step` defaults it to 0: the clock anchor's `anchored_at` is
four-part (`{turn, variant, slot_k, beat}`), and #243 passes it straight in. A map missing any
other key is malformed. The Engine's `N⁺k` position string (`position` on write answers) is
never shown as text: it goes in the row's More as ("Engine position", value). Boundary input
(#242's "as of", #209's slot): `BoundaryField` accepts `12`, `12+1` and `12⁺¹` and cleans to
`"12"` / `"12+1"`, the Engine's `as_of` form. The client sends it as given and httpx encodes
`+` as `%2B`. Anything else → "Enter a turn (12) or a turn and slot (12+1)".
`boundary_text(as_of)` → "as the story stands" for `None`, "through turn 12", "through turn
12⁺¹". `slot_turn_text(turn, k)` → "turn 12⁺¹" (C1's superscripts from
`console.positions.SUPERSCRIPT`; `k` 0 or `None` → "turn 12") is the one "turn N⁺ᵏ" text for a
slot boundary that isn't a full position: #209's rewind/fork slot field and #258's reports use
it.

**D17 — Where things live, and the import rule (owner #235).** `console/world.py` holds D5\u2013D9
(with D4's `viewpoint_line`, D7's `slot_turn_text`, D8's
`config_revision_text`/`CONFIG_REVISION_TEXT`, `nothing_changed`/`ENGINE_NOTHING_CHANGED` and
`SlotResult.engine_position`, D9's exported `READONLY_EXTERNAL_TEXT`, `PLACE_UNKNOWN`,
`STATE_NOT_FOUND` and `settle_reason_text`, and D18's `SLOTS_NOT_LISTED`), `console/effects.py`
D10\u2013D13 (with `reconciliation_text` and `outbox_list_notice_html`) and D19's
`engine_operation_url` and `provider_url`, `console/confirm.py` D8's `ConfirmPage` extensions,
`console/forms/world.py` D4's `WorldViewpointForm`, D5's value inputs
(`parse_number`/`NUMBER_INVALID`, `DeltaField`/`DELTA_INVALID`,
`ScalarValueField`/`VALUE_HELP`), D6's `PlannedField` and D7's `BoundaryField`,
`console/templatetags/world_tags.py` the `outbox_id`, `clock_value` and `state_value` filters,
and `console/inspect.py` D1\u2013D3. Each module's docstring carries its Ds. Page stories import
from these modules and never redefine a helper, a wording constant or a gate. **Storyless pages
and `erasure_pending`.** `HeldStoryMiddleware` fires only on URLs carrying a story id, so a
page with none (#250's `operations:engine`, #240/#246's provider pages) handles a 503
`erasure_pending` itself: #87's `_held.html` fragment inside its own frame, naming a story only
when it knows one (#250's rule).
"""

import json
import math
import re
from collections.abc import Sequence
from typing import Any

from django import forms
from django.http import QueryDict

from console.cast import OMNISCIENT, Character
from console.engine.client import EngineError
from console.forms.inspect import VIEWPOINT_NOT_IN_CAST, VIEWPOINT_UNRESOLVED, viewpoint_choices

NUMBER_INVALID = "Enter a number (e.g. 12, -3 or 0.5)"
DELTA_INVALID = "Enter a signed number, e.g. -500 or 2.5"
VALUE_HELP = (
    'Numbers and true/false are sent as such; put text in double quotes to send "12" or "true" '
    "as text."
)
PLANNED_INVALID = "Enter minutes from the story's start (e.g. 130) or a time of day (HH:MM)"
BOUNDARY_INVALID = "Enter a turn (12) or a turn and slot (12+1)"

MINUS = "\u2212"
_NUMBER = re.compile(r"^[+-]?[0-9]+(\.[0-9]+)?$")
_TIME_OF_DAY = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_BOUNDARY = re.compile(r"^([0-9]+)(?:\+([0-9]+))?$")
_FROM_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺", "0123456789+")


def parse_number(text: Any) -> int | float | None:
    """An ASCII signed decimal (``12``, ``+5``, ``-3``, ``2.5``, or ``\u22123`` with U+2212) → an
    ``int`` without a dot, else a finite ``float``; anything else → ``None``."""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if value.startswith(MINUS):
        value = "-" + value[1:]
    if not _NUMBER.match(value):
        return None
    if "." not in value:
        return int(value)
    number = float(value)
    return number if math.isfinite(number) else None


class DeltaField(forms.CharField):
    """A signed amount (#264, #260): blank → ``None``, else :func:`parse_number`."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return None
        number = parse_number(text)
        if number is None:
            raise forms.ValidationError(DELTA_INVALID, code="invalid")
        return number


class ScalarValueField(forms.CharField):
    """A scalar value with no State type known (#264, #260): blank → ``None``; ``true``/``false``
    → a bool; a :func:`parse_number` number; a double-quoted JSON string → that string; any
    other text → the stripped text ("null" stays text)."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("required", False)
        kwargs.setdefault("help_text", VALUE_HELP)
        super().__init__(**kwargs)

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return None
        if text in ("true", "false"):
            return text == "true"
        number = parse_number(text)
        if number is not None:
            return number
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            try:
                decoded = json.loads(text)
            except ValueError:
                return text
            if isinstance(decoded, str):
                return decoded
        return text


class PlannedField(forms.CharField):
    """A story-time (D6): ASCII digits → minutes from the story's start (``int``); ``HH:MM``
    (00\u201323, 00\u201359) → the string, sent as given; anything else →
    :data:`PLANNED_INVALID`."""

    def __init__(self, **kwargs: Any) -> None:
        messages = {"required": PLANNED_INVALID, **kwargs.pop("error_messages", {})}
        super().__init__(error_messages=messages, **kwargs)

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return None
        if text.isascii() and text.isdigit():
            return int(text)
        if _TIME_OF_DAY.match(text):
            return text
        raise forms.ValidationError(PLANNED_INVALID, code="invalid")


class BoundaryField(forms.CharField):
    """An as-of boundary (D7): ``12``, ``12+1`` or ``12⁺¹`` → ``"12"`` / ``"12+1"`` (the Engine's
    ``as_of`` form, normalised through ``int``); anything else → :data:`BOUNDARY_INVALID`."""

    def to_python(self, value: Any) -> Any:
        text = super().to_python(value)
        if not text:
            return None
        match = _BOUNDARY.match(text.translate(_FROM_SUPERSCRIPT))
        if match is None:
            raise forms.ValidationError(BOUNDARY_INVALID, code="invalid")
        turn, slot = match.group(1), match.group(2)
        return f"{int(turn)}" if slot is None else f"{int(turn)}+{int(slot)}"


class WorldViewpointForm(forms.Form):
    """D4's picker for the World tab's reads (States, one State, scheduled events): one
    ``viewpoint`` select, "Omniscient" first and the initial value whatever the story's default
    (founder decision 9), then the cast (``viewpoint_choices``). With no ``viewpoint`` in the
    data the form binds ``omniscient`` and reads at once."""

    viewpoint = forms.ChoiceField(
        label="Viewpoint",
        error_messages={"invalid_choice": VIEWPOINT_NOT_IN_CAST, "required": VIEWPOINT_NOT_IN_CAST},
    )

    def __init__(
        self, data: QueryDict | dict[str, Any], *, cast: Sequence[Character] | None
    ) -> None:
        bound = data.copy()
        if "viewpoint" not in bound:
            bound["viewpoint"] = OMNISCIENT
        self.cast = cast
        self.initial_viewpoint = OMNISCIENT
        self.choices = viewpoint_choices(cast, bound.get("viewpoint") or "")
        super().__init__(bound)
        viewpoint = self.fields["viewpoint"]
        if isinstance(viewpoint, forms.ChoiceField):
            viewpoint.choices = self.choices

    def query(self) -> dict[str, Any]:
        """The read's ``viewpoint`` keyword argument (valid form only)."""
        return {"viewpoint": self.cleaned_data["viewpoint"]}

    def apply_engine_error(self, exc: EngineError) -> None:
        """``viewpoint_unresolved`` or a 422 on ``query.viewpoint`` → beside Viewpoint; anything
        else above the form."""
        if exc.code == VIEWPOINT_UNRESOLVED or exc.details.get("field") == "query.viewpoint":
            self.add_error("viewpoint", VIEWPOINT_NOT_IN_CAST)
            return
        self.add_error(None, exc.message)
