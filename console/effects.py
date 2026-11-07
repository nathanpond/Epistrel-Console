"""The shared wording of every M6 State change, outbox row and staged turn (#235): State-change
lines, outbox ids, the reconciliation count and the links to pages later stories register.

The D-list text below is the M6 cross-story conventions' own (M6 milestone description), verbatim.

**D10 — State-change wording: status, contingency, requested and dispatched (owner #235;
`console/effects.py`).** One `StateEffect` (#231) reads as `effect_line(effect, names, units)
-> EffectLine(state, scope, change, status, contingency, reason, notes)`:
- `state`: the name;
- `scope`: the C4 label of `scope_key`, "—" for a story-scoped State;
- `change`: D5's `change_text(old_value, new_value)`. When `delta` is set it is
  `signed_delta_text(delta)` with the change in parentheses;
- `status`: humanised (C3): committed, held, pending, released, converted, dropped;
- `contingency`: humanised, and left empty for `unconditional`, the common case;
- `reason`: the effect's `reason`, verbatim;
- `notes`, in this order:
  - "capped from <declared>" when `capped`;
  - "transfer in" / "transfer out" for `kind` `transfer_in` / `transfer_out`;
  - "other side <humanised counterparty>" when `counterparty`;
  - "requested of the consumer" when `owner == "external"`, with `effect_id` in More;
  - "dispatched · outbox <D11 id>" when `outbox_id`;
  - "+<minutes> min requested" for `kind == "time_advance"`.

`notes` is plain text. The outbox id is its own member, `EffectLine.outbox_id: str | None`,
from which templates render the "dispatched · outbox" note through the `outbox_id` filter and
`outbox_url`; `EffectLine.more` holds ("Effect id", full id) for an external owner. **Unknown
State types.** A caller with no States read (#245's play strip, #254, #255, #249, #258) calls
`effect_line(effect, names, units=None)`; each value's type is then inferred from the JSON
value alone (bool → boolean, int/float → number, str → text, anything else compact JSON), with
no unit. A page never reads States just to word a change.

The Engine sends status and contingency as plain strings (no schema enum), so they are never
`open_enum`, and any value renders humanised. Reservations (founder decision 3) are these
status/contingency columns, not a page. Outbox row statuses (`intended`, `claimed`,
`confirmed`, `failed`, `unknown`, `cancelled`, `imported_unsettled`, `compensated`), listing
reasons, `reverification`, external `source` (`live`, `last_known`, `unknown`), scheduled
`label`/`status` and clock `mode` are all humanised (C3) with the raw value in `title`.

**D11 — Outbox ids and the missing outbox list (owner #235; `console/effects.py`, template
filter `outbox_id` in `console/templatetags/world_tags.py`).** An outbox id (and an
`effect_id`) is the provider's idempotency key, so it is shown in full, never shortened by
convention 2: `<code class="outbox-id">01JM6BX…full…</code>`. Where the page isn't the
reconciliation list, it links to `inspect:reconciliation` plus `#outbox-<id>` through
`outbox_url(story_id, outbox_id)` (D19: `None`, so plain `<code>`, until #249 registers the
name), and the list gives each row `id="outbox-<id>"`. The Engine has no outbox list read
(founder decision 4). Every page that shows outbox rows outside the reconciliation list carries
the one notice `OUTBOX_LIST_NOTICE` = "Outbox rows are shown where the Engine reports them;
there is no list of every row yet (<link>)". The link is `OUTBOX_LIST_REQUEST` (the Engine
request the planner files, "Engine request (outbox list, nathanpond/Epistrel#679)") to its
GitHub URL, in the same tab. Pages render it only through `outbox_list_notice_html()` (#245,
#254, #255, #258, and any later page showing outbox rows), never by filling the constant
themselves. *E17 (#295, M7):* the request was delivered by Engine M7 (`list_outbox`), so
`outbox_list_notice_html(story_id=…)` now renders `OUTBOX_SECTION_NOTICE` = "Every outbox row is
listed in <a>Effects \u203a Outbox</a>", linking the story's `inspect:outbox` (#325); while
that name is unregistered it renders the old notice (D19). Every caller passes its story id.

**D12 — Reconciliation count link (owner #235; `console/effects.py`).**
`reconciliation_link(story_id, count)` returns `None` for `None` or 0, or while
`inspect:reconciliation` is unregistered (D19), else ("1 effect to reconcile" / "N effects to
reconcile", the `inspect:reconciliation` URL). The text alone is `reconciliation_text(count)`
(`None` for `None` or 0), which a page shows unlinked when the link is `None` but the count
isn't 0 (D19's "a count without a link"); no page words the count itself. The play strip
(#245), the regenerate and correct results (#245) and the rewind, undo-rewind, swap and fork
reports (#258) use it.

**D13 — Incomplete and staged turns (owner #235; `console/effects.py`).** A
`TurnSummary.incomplete` turn shows `<span class="badge incomplete" title="A staged turn still
waiting on its external outcome">Incomplete</span>` beside its turn number (#245 play, #254
History), linked to the turn's Engine operation page through D19's `engine_operation_url` when
the operation id is known. An operation with `staged` shows "Staged turn" (#250, #257). A
stage's `revision` reads "revision <n>", or "—". The text `INCOMPLETE_TITLE` and the label
`STAGED_LABEL` live here.

**D19 — Links to a route a later story registers (owner #235; `console/effects.py`,
`console/world.py`).** A helper that reverses a URL name another story registers returns `None`
while the name is unregistered (`NoReverseMatch`), and its templates render plain text then (an
id in `<code>`, a name unlinked, a count without a link). So a story never needs a Blocked-by
edge just to link to a page another story builds. It applies to:
- `console.effects.outbox_url` (`inspect:reconciliation`, #249) and `reconciliation_link` (the
  same name, D12);
- `console.effects.engine_operation_url(operation_id) -> str | None` (`operations:engine`,
  #250), which #245's and #254's Incomplete badges use;
- `console.effects.provider_url(name) -> str | None` (`engine:provider`, #240): `None` for a
  blank or non-string name too. It lives here, not in #240's `console/providers.py`, so the
  pages that name a provider (#247, #248, #249, #256, #264, #259, and #240's own list) import
  it without depending on #240. #240's detail page still refuses a malformed name itself (its
  `is_provider_name`);
- `console.world.world_refusal`'s `provider_unbound` link (`stories:providers`, #247), and any
  template that links a section of another story (`inspect:clock`, `inspect:clock-settings`,
  `engine:providers`) only when its name resolves (#64's nav rule).

Tests pin the unlinked rendering with a urlconf lacking the name (as
`tests/inspect/urls_without_retrieval.py` does). The linked rendering, before the owning story
lands, uses **one** shared stub urlconf, `tests/urls_with_engine_operation.py` (owner #235):
the real patterns plus stub views named `operations:engine`, `inspect:reconciliation`,
`engine:provider` and `stories:providers`. #245, #254, #240, #249, #250 and any other story
testing a D19 link reuse it (adding a stub name there if it needs one) and never add their own.
A story that *imports a module* another story creates still depends on that story; D19 removes
only link-only edges.

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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine.models import StateEffect
from console.engine_errors import EMPTY
from console.ids import is_ulid
from console.wording import character_label, humanised
from console.world import change_text, signed_delta_text, state_value_text

# --- D10: State changes ------------------------------------------------------------------------

UNCONDITIONAL = "unconditional"
EXTERNAL = "external"
REQUESTED_NOTE = "requested of the consumer"
DISPATCHED_NOTE = "dispatched · outbox"  # the template follows it with the D11 id
TRANSFER_NOTES = {"transfer_in": "transfer in", "transfer_out": "transfer out"}
TIME_ADVANCE = "time_advance"
EFFECT_ID_LABEL = "Effect id"

# --- D11: outbox ids --------------------------------------------------------------------------

# The Engine request for an outbox list: delivered by Engine M7 (``list_outbox``); kept for
# history and for the notice shown while ``inspect:outbox`` is unregistered (E17, #295).
OUTBOX_LIST_REQUEST = "https://github.com/nathanpond/Epistrel/issues/679"
OUTBOX_LIST_REQUEST_LABEL = "Engine request (outbox list)"
OUTBOX_LIST_NOTICE = (
    "Outbox rows are shown where the Engine reports them; there is no list of every row yet "
    "({link})"
)

# E17 (#295): the notice once the Effects tab's Outbox section (#325) lists every row.
OUTBOX_SECTION_LABEL = "Effects \u203a Outbox"
OUTBOX_SECTION_NOTICE = "Every outbox row is listed in {link}"

# --- D13: incomplete and staged turns ------------------------------------------------------------

INCOMPLETE_TEXT = "Incomplete"
INCOMPLETE_TITLE = "A staged turn still waiting on its external outcome"
STAGED_LABEL = "Staged turn"


@dataclass(frozen=True)
class EffectLine:
    """One State change as D10 words it. ``notes`` is plain text, in D10's order; when the
    change was dispatched, :data:`DISPATCHED_NOTE` stands in its place and ``outbox_id`` holds the
    full id the template renders after it (the ``outbox_id`` filter, linked by
    :func:`outbox_url`). ``more`` holds ("Effect id", id) for an external owner."""

    state: str
    scope: str
    change: str
    status: str
    contingency: str
    reason: str
    notes: tuple[str, ...]
    outbox_id: str | None
    more: tuple[tuple[str, str], ...]


def effect_line(
    effect: StateEffect, names: Mapping[str, str], units: Mapping[str, str] | None = None
) -> EffectLine:
    """D10's columns for one ``StateEffect``. Each value's type is inferred from its JSON value
    (no page reads States to word a change); ``units`` (by State name) supplies the unit."""
    unit = (units or {}).get(effect.state)
    change = change_text(effect.old_value, effect.new_value, None, unit)
    if effect.delta is not None:
        signed = signed_delta_text(effect.delta, unit)
        has_values = effect.old_value is not None or effect.new_value is not None
        change = f"{signed} ({change})" if has_values else signed
    notes: list[str] = []
    if effect.capped:
        notes.append(f"capped from {state_value_text(effect.declared, None, unit)}")
    if effect.kind in TRANSFER_NOTES:
        notes.append(TRANSFER_NOTES[effect.kind])
    if effect.counterparty:
        notes.append(f"other side {humanised(effect.counterparty)}")
    if effect.owner == EXTERNAL:
        notes.append(REQUESTED_NOTE)
    outbox = effect.outbox_id if isinstance(effect.outbox_id, str) and effect.outbox_id else None
    if outbox is not None:
        notes.append(DISPATCHED_NOTE)
    if effect.kind == TIME_ADVANCE:
        minutes = EMPTY if effect.minutes is None else str(effect.minutes)
        notes.append(f"+{minutes} min requested")
    more: tuple[tuple[str, str], ...] = ()
    if effect.owner == EXTERNAL and effect.effect_id:
        more = ((EFFECT_ID_LABEL, effect.effect_id),)
    status = humanised(effect.status) if effect.status else EMPTY
    contingency = (
        "" if not effect.contingency or effect.contingency == UNCONDITIONAL
        else humanised(effect.contingency)
    )  # fmt: skip
    return EffectLine(
        state=effect.state,
        scope=character_label(effect.scope_key, names),
        change=change,
        status=status,
        contingency=contingency,
        reason=effect.reason,
        notes=tuple(notes),
        outbox_id=outbox,
        more=more,
    )


# --- D11 / E17: the outbox list ------------------------------------------------------------------


def outbox_list_notice_html(*, story_id: str | None = None) -> SafeString:
    """E17 (#295): :data:`OUTBOX_SECTION_NOTICE`, linking the story's ``inspect:outbox``, when
    ``story_id`` is given and that name resolves; else D11's old notice, linking
    :data:`OUTBOX_LIST_REQUEST` in the same tab (D19). The one renderer of both."""
    section = _reverse("inspect:outbox", story_id) if story_id else None
    if section is not None:
        link = format_html('<a href="{}">{}</a>', section, OUTBOX_SECTION_LABEL)
        before, _, after = OUTBOX_SECTION_NOTICE.partition("{link}")
        return format_html("{}{}{}", before, link, after)
    link = format_html('<a href="{}">{}</a>', OUTBOX_LIST_REQUEST, OUTBOX_LIST_REQUEST_LABEL)
    before, _, after = OUTBOX_LIST_NOTICE.partition("{link}")
    return format_html("{}{}{}", before, link, after)


# --- D12: the reconciliation count ---------------------------------------------------------------


def reconciliation_text(count: Any) -> str | None:
    """ "1 effect to reconcile" / "N effects to reconcile"; ``None`` for ``None``, 0 or a
    non-int."""
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        return None
    return "1 effect to reconcile" if count == 1 else f"{count} effects to reconcile"


# --- D19: links to routes later stories register --------------------------------------------------


def _reverse(name: str, *args: str) -> str | None:
    try:
        return reverse(name, args=list(args))
    except NoReverseMatch:
        return None


def outbox_url(story_id: str, outbox_id: Any) -> str | None:
    """The reconciliation list at ``#outbox-<id>`` (``inspect:reconciliation``, #249); ``None``
    while unregistered, or for a blank or non-string id."""
    if not isinstance(outbox_id, str) or not outbox_id:
        return None
    url = _reverse("inspect:reconciliation", story_id)
    return None if url is None else f"{url}#outbox-{outbox_id}"


def reconciliation_link(story_id: str, count: Any) -> tuple[str, str] | None:
    """D12: (:func:`reconciliation_text`, the ``inspect:reconciliation`` URL); ``None`` for no
    count, or while the name is unregistered (show the text unlinked then)."""
    text = reconciliation_text(count)
    if text is None:
        return None
    url = _reverse("inspect:reconciliation", story_id)
    return None if url is None else (text, url)


def engine_operation_url(operation_id: Any) -> str | None:
    """The Engine operation page (``operations:engine``, #250); ``None`` while unregistered or
    for an id that isn't a ULID."""
    if not isinstance(operation_id, str) or not is_ulid(operation_id):
        return None
    return _reverse("operations:engine", operation_id)


def provider_url(name: Any) -> str | None:
    """A provider's page (``engine:provider``, #240); ``None`` while unregistered or for a blank
    or non-string name."""
    if not isinstance(name, str) or not name.strip():
        return None
    return _reverse("engine:provider", name)


# --- D13: staged turns ----------------------------------------------------------------------------


def stage_revision_text(revision: Any) -> str:
    """ "revision 12" (no separators), "—" for a bool or non-int."""
    if not isinstance(revision, int) or isinstance(revision, bool):
        return EMPTY
    return f"revision {revision}"
