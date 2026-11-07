"""The shared wording of every M6 World page and slot write (#235): State values, clock values,
positions and boundaries, slot-write results and refusals.

The D-list text below is the M6 cross-story conventions' own (M6 milestone description), verbatim.

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

**D8 — Inter-turn slot writes: mechanics and result pages, plus the re-render, "nothing to
change" and confirm-page rules every M6 write form shares (owner #235; `console/world.py`,
`console/confirm.py`).** These are the timeline-moving writes: set a State, declare a State,
approve a proposal, advance the clock, create, reschedule or cancel a scheduled event, settle
now and compensate.
- **Request.** A form POST with the page's fresh idempotency key (#135 convention 8), sending
  `X-Epistrel-Expected-Revision`. Its value is the `timeline_revision` of the read the form was
  rendered from, carried in the hidden field `expected_revision` (`EXPECTED_REVISION_FIELD`).
  When that read carries no `timeline_revision` (the clock read `ClockBody`, the reconciliation
  read, the proposals read, or a page with only the shell's reads), the value is the shell's
  story read's `Story.timeline_revision`; the story is read first, so a turn landing in between
  makes the write stale (409 `revision_conflict`), never silently ahead. A missing or
  non-integer value sends no header. The configuration writes don't send it: `PATCH
  …/states/{name}/definition`, `PATCH …/clock` and `PUT …/binding`.
- **Timeout.** Slot writes wait `turn_timeout(None)`: the Engine queues them behind a running
  turn under `turn_concurrency: queue`. Configuration writes wait `MUTATION_TIMEOUT_S`.
- **Result.** Each write gets a result page, rendered on the POST response and again via
  `?report=<pk>` from the #44 row (convention 23's pattern, `inspect_beliefs.written`). It
  shows:
  - what changed (D5/D6);
  - "Slot: <C1 position>", linked. An answer with no slot or position (`ScheduledEventWritten`)
    leaves the line out; a `null` slot (`settled_now` `queued`) reads "Slot: —";
  - "Operation: <engine_id>", linking the #44 row when there is one;
  - "Story revision now <n>";
  - a "Back to <section>" link.

  `console.world.slot_result(story_id, answer, row, back_url) -> SlotResult` builds these lines
  from any #232 slot-write answer, with or without a slot. `SlotResult.engine_position` carries
  the Engine's `N⁺k` string for D7's More row ("Engine position", value). The #44 row is
  `ctx.client.last_operation_id` → `EngineOperation` (as `inspect_beliefs.direct` does); when
  there is none the page renders from the raw answer with no report link. This applies to every
  slot-write and configuration-write result.
- **Configuration writes** show `config_revision_text(n)` ("Configuration revision now <n>",
  `CONFIG_REVISION_TEXT`) instead and, as POST-redirect-GET, flash "<Humanised member>: saved"
  (C3, convention 23).
- **Nothing to change (configuration writes).** When the Engine accepts a configuration write
  that changed nothing, the answer's `config_revision` equals the one the page rendered from:
  the shell's effective-config read's `config_revision` for a story's configuration (#265's
  definition PATCH, #243's clock PATCH), the signed snapshot's for a deployment write (#246's
  provider replace, #241's settings). Then the page flashes `ENGINE_NOTHING_CHANGED` "The
  Engine found nothing to change" (info) in place of the "saved" lines, and still redirects.
  When the base revision is unknown, or the answer carries no `config_revision` (#247's binding
  PUT, whose answer has only `timeline_revision`), the usual flash shows; the Console's own
  "untouched sends nothing" check (C9) stays first everywhere.
  `console.world.nothing_changed(answer_revision, base_revision) -> bool` is the one
  comparison; #265, #243, #246, #241 and #247 cite this rule.
- **Re-rendering a refused or invalid form (every slot write and configuration write; the
  confirm page counts as the form).**
  - *Local invalid input* (the Console's own checks, a gate refusal, a bad snapshot): nothing
    was sent, so the page keeps the **posted** idempotency key and the **posted**
    `expected_revision`.
  - *An answered Engine refusal* (any 4xx, `revision_conflict` and `module_off` included): a
    **fresh** idempotency key, since a new attempt is a new write; and `expected_revision`
    refreshed from **that POST's own story read** (the shell's `Story.timeline_revision`, or
    the form's own read when it carries one, as #251's State read does). A `revision_conflict`
    additionally shows C5's "the story changed" text above the form, so the operator reviews
    what changed before resubmitting; the refreshed revision only makes the resubmit possible.
    When the POST's read failed, or the page makes no story read (a confirm page outside the
    Inspect shell: #262's cancel, #256's settle-now), the posted revision is kept and C5's
    `revision_conflict` text sends the operator back to the list to reload.
  - *A transport error, timeout or unreadable answer* (`bad_response`; the outcome is unknown):
    #43's panel above the form, and the **posted** key and revision kept, so a resubmit replays
    rather than writing twice.
  - *A 409 `module_off` on the write* (the local gate passed): a form-level refusal, D9's
    reason with the Config link (on a confirm page, through `ConfirmPage.error_url`), never a
    re-grey and never an extra read.

  Configuration, provider and binding writes send no expected revision, so only the key half
  applies to them; #263's correction keeps its existing `rev` handling and follows the key
  half. #251, #261, #265, #266, #243, #252, #253, #262, #263, #246, #241, #247, #256, #264 and
  #260 cite this rule and define none of their own.
- **Confirm pages with typed values.** A confirm page that carries a form's values in hidden
  fields carries the **raw typed text**, re-cleaned on the confirmed POST (a cleaned `True` or
  `12` from `ScalarValueField`/`DeltaField` doesn't round-trip as text). #264 and #260 follow
  it.
- **Shared confirm-page extensions (#235; `console/confirm.py`,
  `templates/console/_confirm.html`).** `ConfirmPage` gains three optional members, leaving
  every existing page unchanged: `blocked: str = ""` (when set, `<p class="warning">` and a
  disabled button in place of the live submit: #263's not-latest/archived/paused states, and
  any confirm page that must show why it can't proceed); `fact_urls: Mapping[str, str]` (a fact
  whose label is named renders its value in `<a>`: #256, #264); and `error_url`/`error_link` (a
  link after `error`, e.g. a `WorldRefusal.url` labelled "Config", "Providers" or
  "Reconciliation": #262's cancel, #256, #264).
- **Gates.** A gated POST checks D1 through `gate_refusal` first and makes no Engine write
  (C6). A write whose form can carry no non-blank gated field (#262's reschedule unticking
  every shared owner; a confirm page) checks `module_gate` directly instead.

**D9 — Refusal wording for M6 writes (owner #235; `console/world.py` `world_refusal(exc,
story_id, verb_phrase, effective, *, field_map=None, value_type=None, unit=None) ->
WorldRefusal | None`, where `WorldRefusal(text, url="", field=None)` is a frozen dataclass;
`value_type`/`unit`, keyword-only, format `attempted`/`current` through D5 when the caller
knows the State (#251 and #265 pass the read State's, #260 those of `PlayStory.states` when it
lists the State); without them the type is inferred from the value, no unit (#264, which reads
no States).** Its own codes are matched first; anything else falls through to C5's
`write_refusal` (archived, paused, revision conflict, erased subject), wrapped with
`field=None`. The text is exact. `field` names the Engine request member the error is about
(forms name their fields after the Engine's members), so a re-rendered form puts the text under
that field, or above the form when `field` is `None`. The member comes from `details.field` (or
the code's own member below) with the Engine's `body.` prefix stripped (`body.shared_owners` →
`shared_owners`, `body.effect.state` → `effect.state`), then mapped through the caller's
`field_map` when it names it: the mapping is per form, never global (#253 and #262 pass `{"to":
"planned"}`, since their time field is `planned`). A member the map doesn't name is kept as is.
A code below that names no member is placed by `details.field` (stripped and mapped) when
present, else above the form. The same stripping applies to the Engine's 422 request-validation
paths: `apply_engine_errors` strips `body.` before its alias lookup, so every alias map is
keyed `effect.state`, never `body.effect.state`.
- **`story_busy` (423, from slot writes).** `busy_notice(details)` plus " · retry after
  <Retry-After> s" when `exc.retry_after` is set. `lock_timeout` reads "Story busy".
- **`module_off`.** `unavailable_for(details.module, effective).reason`, with a link to the
  story's Config page (convention 24).
- **`clock_backward`.** "That would move the clock backward (it is at <offset_text(current)>,
  you asked for <offset_text(attempted)>) — earlier story-time is reached by rewinding". The
  Engine's details name no member, so it is placed by `details.field` when present, else above
  the form; a non-int `current`/`attempted` reads "—".
- **`clock_unanchored`.** "The clock's anchor doesn't fix the time of day, so "<to>" can't be
  placed — enter minutes from the story's start", on the time field.
- **`clock_external`.** "This clock follows its provider's value (external mode) — it can't be
  advanced here".
- **`provider_unbound`.** "This story has no binding to <providers, comma-joined> — bind it
  first", with `url` set to `stories:providers` (the story's bindings page, which #247
  registers; "" while it doesn't resolve).
- **`state_unpermitted`.**
  - On a set or a declaration: "This State's authorization doesn't admit the consumer as a
    source".
  - On compensate: "Only a State an external provider owns can be compensated".
- **`state_readonly_external`.** `READONLY_EXTERNAL_TEXT` = "This State's value belongs to its
  provider — it can't be set here" (exported, so #251's external-owner pre-check shows the same
  sentence with no call).
- **`state_derived`.** "This State is derived from other States — it can't be set directly".
- **`state_constraint_violation`.** "That breaks the State's `<constraint>` constraint (asked
  <attempted>, now <current>)", values through D5. For a definition change (`verb_phrase ==
  "change a State's definition"`, #265), where the Engine sends `attempted == current`: "The
  current value <current> breaks the new `<constraint>` constraint".
- **`state_name_taken`.** "A State named “<state>” already exists", on `name`.
- **`validation_error`, by `reason`.**
  - `reserved_state`: "`world_clock` is the world clock — use the Clock section".
  - `immutable_field`: "<Humanised field> can't change once a State is declared", on that
    field.
  - `event_settled`: "This event is already <humanised status> — it can't be changed".
  - `unknown_character` (on `scope_key`, `participants` or `shared_owners`): C4's
    `UNKNOWN_CHARACTER`, on that member.
  - `unknown_place` (on `place`): `PLACE_UNKNOWN` "That isn't a place in this story", on
    `place`.
  - Any other: on `details.field` when the form has it, else above the form.
- **`viewpoint_unresolved` (on `shared_owners`/`participants`).** C4's `UNKNOWN_CHARACTER`.
- **`not_found`, by `reason`.**
  - `no_listing`: "This effect is no longer listed for reconciliation — reload the list".
  - `no_pending_proposal`: "This proposal is no longer pending — reload the list".
  - No `reason`, with `details.state`: `STATE_NOT_FOUND` "This story has no State “<state>”",
    on `state` (#264's compensate and #260's ingest rows; a page that read the State first,
    such as #251's, renders convention 10's not-found page for a 404 instead).
- **`settle_refused`.** "Settling was refused: <reasons joined "; ">". Each reason code maps
  through the exported `settle_reason_text(code)`, which #249's re-verification column and
  #256's result reuse:
  - `precondition:availability` → "the item is no longer available";
  - `precondition:ownership` → "the seller no longer owns it";
  - `bounds:<c>` → "it would break the `<c>` bound";
  - `settlement_conflict` → "it conflicts with another settlement";
  - `not_confirmed` → "the effect isn't confirmed";
  - an unknown code → shown raw in `<code>`.
- **`provider_read_only`.** "<provider> is read-only — it can't take a compensating effect".
- **Anything else.** `None`: the caller renders #43's `panel_for`. That includes
  `idempotency_conflict`.

Codes outside slot writes (`invalid_manifest`, `provider_in_use`, `binding_incomplete`,
`not_latest_turn`, `already_corrected`) are each worded by their own story (#246, #247, #263),
C5 first.

**D18 — Engine gaps, said once (owner #235; convention 24's rule).**
- **Outbox list (nathanpond/Epistrel#679, closed).** Delivered by Engine M7 (`list_outbox`):
  `outbox_list_notice_html(story_id=…)` links the Effects tab's Outbox section (E17, #295; D11's
  old notice while `inspect:outbox` is unregistered).
- **Inter-turn slots in History, and no State change log.** The history read doesn't list
  inter-turn slot operations: `HistoryItemModel`/`TurnSummaryModel` carry no `slot_k`, and
  items come from the transcript. No Engine read lists a story's slots or its State changes
  across turns and slots. #209's slot field carries the exported
  `console.world.SLOTS_NOT_LISTED` = "History doesn't list inter-turn slots; a State's "since"
  position or a write's result names one"; #255's trail lists turn changes only and says so.
- **`viewpoint` on the States and scheduled-event reads.** Declared since Engine M7
  (nathanpond/Epistrel#682): `?viewpoint=` on `GET …/states`, `GET …/states/{name}` and `GET
  …/scheduled-events` is in the Engine's OpenAPI. No page notice.
- **State definition read (nathanpond/Epistrel#680, closed).** Read by `get_state_definition`;
  #329 uses its `config_revision` as D8's base (E17, #295). Until #329 lands, #265's page still
  cites `STATE_DEFINITION_REQUEST`.
- **Operations by label (nathanpond/Epistrel#681, closed).** Story operations are listed by
  `list_operations` (#324) (E17, #295).
- **No State-proposal analysis status read.** The 202's `job_id` has no read, so #266 flashes
  "queued" and asks for a reload rather than polling.
- **`StateBody` has no `provider_id`.** An external State's owner reads "External" with no
  provider (#242); the provider shows in #247 and #248.
- **`ExternalReadItemBody` names no State.** It carries `state_id` only, and no read maps ids
  to names, so #248 labels a read by provider and value name.
- **`POST /stories`'s 404.** Declared since Engine M7 (#682): a `provider_bindings` entry
  naming an unregistered provider answers 404 `not_found` (`details.provider`,
  `ProviderNotFound`). #259 handles it by `details.provider`.
- **History has no `whisper_to`.** The history read doesn't return a turn's whisper target, so
  #263's correction can't carry a whisper over (its help says so).
- **Clock anchor components are the anchoring offset's, not now's.** `ClockBody.anchor`'s
  year…minute are `absolute_at(anchor.offset)`; only `ClockBody.display` is now. #243 heads the
  table "As established" and never shifts components to now (D6: no date computing).
- **Stale ingest route prose.** `POST /stories/{id}/ingest`'s description still says `outcome`
  events "arrive later in M6 (422 `unsupported_event_kind`)"; the code records them
  (`SUPPORTED_KINDS` includes `outcome`). Console code follows the code.
- **The Ingest tab on archived and paused stories.** The Engine accepts an outcome-only ingest
  batch on a paused story (a pause never refuses outcomes), but the Console keeps the Ingest
  tab disabled on archived and paused stories for every batch (#260), so an outcome can't be
  sent from the Console there. Recorded as a known Console limit, not an Engine gap.

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

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.cast import OMNISCIENT, character_of
from console.engine.client import EngineClientError, EngineError
from console.engine.models import NarrativePosition
from console.engine_errors import EMPTY
from console.held import retry_seconds
from console.inspect import module_off_unavailable, unavailable_for
from console.positions import SUPERSCRIPT, knowledge_position_text, knowledge_position_url
from console.wording import (
    UNKNOWN_CHARACTER,
    busy_notice,
    character_label,
    humanised,
    write_refusal,
)

MINUS = "\u2212"  # the minus sign (U+2212) of a negative delta
UNDEFINED = "undefined"
NUMBER, BOOLEAN, ENUM, TEXT = "number", "boolean", "enum", "text"

# --- D4: the viewpoint line ------------------------------------------------------------------

STATES_NOTE = "hidden States are left out"
OMNISCIENT_LABEL = "Omniscient"

# --- D8: slot writes --------------------------------------------------------------------------

EXPECTED_REVISION_FIELD = "expected_revision"
STORY_REVISION_TEXT = "Story revision now {n}"
CONFIG_REVISION_TEXT = "Configuration revision now {n}"
ENGINE_NOTHING_CHANGED = "The Engine found nothing to change"

# --- D9: exported refusal sentences ------------------------------------------------------------

READONLY_EXTERNAL_TEXT = "This State's value belongs to its provider — it can't be set here"
RESERVED_STATE_TEXT = "`world_clock` is the world clock — use the Clock section"
# Exported so #252's advance page shows the same sentence for an external clock with no call.
CLOCK_EXTERNAL_TEXT = (
    "This clock follows its provider's value (external mode) — it can't be advanced here"
)
PLACE_UNKNOWN = "That isn't a place in this story"
STATE_NOT_FOUND = "This story has no State “{state}”"
# D9's ``no_pending_proposal`` sentence (exported: #266's approve page shows it with no call).
NO_PENDING_PROPOSAL_TEXT = "This proposal is no longer pending — reload the list"
COMPENSATE_VERB = "compensate an effect"
DEFINITION_VERB = "change a State's definition"

# --- D18: Engine gaps -------------------------------------------------------------------------

SLOTS_NOT_LISTED = (
    "History doesn't list inter-turn slots; a State's \"since\" position or a write's result "
    "names one"
)
STATE_DEFINITION_REQUEST = "https://github.com/nathanpond/Epistrel/issues/680"

_BOUNDARY = re.compile(r"^([0-9]+)(?:\+([0-9]+))?$")
_FIVE_PARTS = ("turn", "variant", "slot_k", "beat")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _plain_int(value: object) -> str:
    """An int printed plain ("1200"), anything else "—"."""
    return str(value) if _is_int(value) else EMPTY


# --- D5: State values -------------------------------------------------------------------------


def inferred_type(value: Any) -> str:
    """The D5 type a JSON value reads as when no State says (bool → boolean, int/float →
    number, str → text, anything else compact JSON)."""
    if isinstance(value, bool):
        return BOOLEAN
    if isinstance(value, int | float):
        return NUMBER
    if isinstance(value, str):
        return TEXT
    return "json"


def state_value_text(value: Any, type: str | None, unit: str | None = None) -> str:
    """D5: ``None`` → "undefined"; a number → ``f"{v:,}"`` plus " <unit>"; a boolean →
    "true"/"false"; ``enum``/``text`` verbatim; anything else (or a value that doesn't fit its
    type) compact JSON. ``type`` ``None`` infers it from the value."""
    if value is None:
        return UNDEFINED
    kind = inferred_type(value) if type is None else type
    if kind == NUMBER and _is_number(value):
        return f"{value:,} {unit}" if unit else f"{value:,}"
    if kind == BOOLEAN and isinstance(value, bool):
        return "true" if value else "false"
    if kind in (ENUM, TEXT) and isinstance(value, str):
        return value
    return _json(value)


def state_values(
    values: Any,
    names: Mapping[str, str],
    cast_order: Iterable[str],
    *,
    type: str | None = None,
    unit: str | None = None,
) -> list[tuple[str, str]]:
    """D5: a per-character State's ``[(label, text)]``: the ids present in ``values`` in cast
    order, then the other ids in the map's own order, labelled by C4. ``values`` ``None`` gives
    one row ("—", "undefined"); a non-object one ("—", compact JSON)."""
    if values is None:
        return [(EMPTY, UNDEFINED)]
    if not isinstance(values, Mapping):
        return [(EMPTY, _json(values))]
    order = [cid for cid in cast_order if cid in values]
    seen = set(order)
    order += [cid for cid in values if cid not in seen]
    return [
        (character_label(cid, names), state_value_text(values[cid], type, unit)) for cid in order
    ]


def signed_delta_text(delta: Any, unit: str | None = None) -> str:
    """ "+5 gold", "\u22123 gold" (U+2212), "+0", "+1,200.5"; a non-number as compact JSON."""
    if not _is_number(delta):
        return _json(delta)
    sign = MINUS if delta < 0 else "+"
    text = f"{sign}{abs(delta):,}"
    return f"{text} {unit}" if unit else text


def change_text(old: Any, new: Any, type: str | None = None, unit: str | None = None) -> str:
    """ "<old> → <new>", each through D5 (its type inferred when ``type`` is ``None``)."""
    return f"{state_value_text(old, type, unit)} → {state_value_text(new, type, unit)}"


# --- D6: clock values and offsets ---------------------------------------------------------------


class ClockReading(Protocol):
    """A clock value: #231's ``ClockValue`` and #232's ``WorldClock`` both fit."""

    @property
    def offset(self) -> int: ...
    @property
    def display(self) -> str: ...


def offset_text(minutes: Any) -> str:
    """ "+HHh MMm", byte for byte the Engine's ``world_clock.display`` (hours not wrapped);
    "—" for a non-number."""
    if not _is_number(minutes):
        return EMPTY
    hours, rest = divmod(int(minutes), 60)
    return f"+{hours:02d}h {rest:02d}m"


def _display(value: Any) -> str:
    display = getattr(value, "display", None)
    return display if isinstance(display, str) else EMPTY


def clock_change_text(before: ClockReading | None, after: ClockReading | None) -> str:
    """ "<before.display> → <after.display>", or "<after.display> (unchanged)" when both
    offsets are equal ints; a missing side reads "—"."""
    b, a = getattr(before, "offset", None), getattr(after, "offset", None)
    if before is not None and after is not None and _is_int(b) and _is_int(a) and a == b:
        return f"{_display(after)} (unchanged)"
    return f"{_display(before) if before is not None else EMPTY} → " + (
        _display(after) if after is not None else EMPTY
    )


def clock_value_html(value: ClockReading | None) -> SafeString:
    """``<span class="clock" title="offset 130 min">DISPLAY</span>``; "—" without a display,
    and no ``title`` without an int offset."""
    display = getattr(value, "display", None) if value is not None else None
    if not isinstance(display, str):
        return SafeString(EMPTY)
    offset = getattr(value, "offset", None)
    if _is_int(offset):
        return format_html('<span class="clock" title="offset {} min">{}</span>', offset, display)
    return format_html('<span class="clock">{}</span>', display)


# --- D7: positions, slots and boundaries ---------------------------------------------------------


def five_part(mapping: Any) -> NarrativePosition | None:
    """A ``{turn, variant, slot_k, beat, step}`` map as a position; ``step`` alone may be
    missing (0: an anchor's four-part ``anchored_at``). ``None`` for a non-dict, a missing
    part or a non-int part (a bool is not an int); extra keys are ignored."""
    if not isinstance(mapping, Mapping):
        return None
    parts = [mapping.get(key) for key in _FIVE_PARTS] + [mapping.get("step", 0)]
    ints = [part for part in parts if isinstance(part, int) and not isinstance(part, bool)]
    if len(ints) != len(parts):
        return None
    turn, variant, slot_k, beat, step = ints
    return NarrativePosition(turn=turn, variant=variant, slot_k=slot_k, beat=beat, step=step)


def slot_turn_text(turn: int, k: int | None) -> str:
    """ "turn 12⁺¹" (C1's superscripts); "turn 12" for ``k`` 0 or ``None``."""
    text = f"turn {turn}"
    return text + f"+{k}".translate(SUPERSCRIPT) if k else text


def boundary_text(as_of: str | None) -> str:
    """ "as the story stands" (``None``), "through turn 12", "through turn 12⁺¹"; a malformed
    boundary "through <raw>"."""
    if as_of is None:
        return "as the story stands"
    match = _BOUNDARY.match(as_of)
    if match is None:
        return f"through {as_of}"
    k = match.group(2)
    return f"through {slot_turn_text(int(match.group(1)), int(k) if k else None)}"


# --- D4: the viewpoint line ------------------------------------------------------------------


def viewpoint_line(
    viewpoint: str | None,
    names: Mapping[str, str],
    *,
    note: str = STATES_NOTE,
    omniscient_note: str = "",
) -> str:
    """ "Viewpoint: Omniscient" (" — <omniscient_note>" when given), or "Viewpoint: Alice —
    <note>" with ``{label}`` in ``note`` replaced by the C4 label (a raw viewpoint as given)."""
    if not viewpoint or viewpoint == OMNISCIENT:
        line = f"Viewpoint: {OMNISCIENT_LABEL}"
        return f"{line} — {omniscient_note}" if omniscient_note else line
    character = character_of(viewpoint)
    label = character_label(character, names) if character is not None else viewpoint
    filled = note.replace("{label}", label)
    return f"Viewpoint: {label} — {filled}" if filled else f"Viewpoint: {label}"


# --- D8: slot-write mechanics and results --------------------------------------------------------


def expected_revision(data: Mapping[str, Any]) -> int | None:
    """The posted ``expected_revision`` (ASCII digits, whitespace stripped, leading zeros
    allowed), else ``None`` (no header is sent)."""
    value = data.get(EXPECTED_REVISION_FIELD)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return int(value) if value.isascii() and value.isdigit() else None


def config_revision_text(n: Any) -> str:
    """ "Configuration revision now 13" ("—" for a non-int)."""
    return CONFIG_REVISION_TEXT.replace("{n}", _plain_int(n))


def nothing_changed(answer_revision: Any, base_revision: Any) -> bool:
    """D8: the Engine accepted a configuration write that changed nothing (both ints, equal)."""
    return _is_int(answer_revision) and _is_int(base_revision) and answer_revision == base_revision


class OperationRow(Protocol):
    """The #44 row a result page links (``EngineOperation``)."""

    @property
    def pk(self) -> Any: ...
    @property
    def engine_operation_id(self) -> str: ...


@dataclass(frozen=True)
class SlotResult:
    """A slot write's result-page lines (D8). ``slot_text`` ``None``: the answer has no slot (no
    "Slot:" line); "—" for a ``null`` or malformed one. ``engine_position`` is the Engine's
    ``N⁺k`` string for the More row ("Engine position", value)."""

    slot_text: str | None
    slot_url: str | None
    operation_id: str | None
    operation_pk: int | None
    revision_text: str
    engine_position: str | None
    back_url: str


def slot_result(story_id: str, answer: Any, row: OperationRow | None, back_url: str) -> SlotResult:
    """D8's result lines from any #232 slot-write answer (``operation_id``,
    ``timeline_revision``, and a ``slot`` or a map ``position`` when it has one)."""
    slot_text: str | None = None
    slot_url: str | None = None
    position_attr = getattr(answer, "position", None)
    has_slot = hasattr(answer, "slot") or isinstance(position_attr, Mapping)
    if has_slot:
        raw = answer.slot if hasattr(answer, "slot") else position_attr
        position = five_part(raw)
        if position is None:
            slot_text = EMPTY
        else:
            slot_text = knowledge_position_text(position)
            slot_url = knowledge_position_url(story_id, position)
    engine_position = position_attr if isinstance(position_attr, str) and position_attr else None
    answered = getattr(answer, "operation_id", None)
    operation_id: str | None = answered if isinstance(answered, str) and answered.strip() else None
    if operation_id is None and row is not None:
        operation_id = row.engine_operation_id or None
    pk = row.pk if row is not None else None
    return SlotResult(
        slot_text=slot_text,
        slot_url=slot_url,
        operation_id=operation_id,
        operation_pk=pk if _is_int(pk) else None,
        revision_text=STORY_REVISION_TEXT.replace(
            "{n}", _plain_int(getattr(answer, "timeline_revision", None))
        ),
        engine_position=engine_position,
        back_url=back_url,
    )


# --- D9: refusals -------------------------------------------------------------------------------


@dataclass(frozen=True)
class WorldRefusal:
    """A refused M6 write's text, its link ("" when none) and the Engine request member it is
    about (``body.`` stripped, renamed by the form's ``field_map``; ``None``: above the form)."""

    text: str
    url: str = ""
    field: str | None = None


_SETTLE_REASONS = {
    "precondition:availability": "the item is no longer available",
    "precondition:ownership": "the seller no longer owns it",
    "settlement_conflict": "it conflicts with another settlement",
    "not_confirmed": "the effect isn't confirmed",
}


def _code_span(text: str) -> str:
    return "`" + text.replace("`", "") + "`"


def settle_reason_text(code: Any) -> str:
    """One ``settle_refused`` reason: "the seller no longer owns it", "it would break the `max`
    bound"; an unknown code raw as a backtick span; a non-string as compact JSON in one."""
    if not isinstance(code, str):
        return _code_span(_json(code))
    if code in _SETTLE_REASONS:
        return _SETTLE_REASONS[code]
    if code.startswith("bounds:") and code[len("bounds:") :]:
        return f"it would break the {_code_span(code[len('bounds:') :])} bound"
    return _code_span(code)


def event_settled_text(status: str | None) -> str:
    """D9's ``event_settled`` sentence: "This event is already cancelled — it can't be changed"
    ("settled" when the status is unknown); #262's reschedule GET says it for a listed cancelled
    or completed event too."""
    shown = humanised(status).lower() if status else "settled"
    return f"This event is already {shown} — it can't be changed"


def state_not_found_text(state: str) -> str:
    """ "This story has no State “silver”"."""
    return STATE_NOT_FOUND.replace("{state}", state)


def _string(details: Mapping[str, Any], key: str) -> str | None:
    value = details.get(key)
    return value if isinstance(value, str) and value else None


def _url(name: str, story_id: str) -> str:
    try:
        return reverse(name, args=[story_id])
    except NoReverseMatch:
        return ""


def world_refusal(
    exc: EngineClientError,
    story_id: str,
    verb_phrase: str,
    effective: Mapping[str, Any] | None,
    *,
    field_map: Mapping[str, str] | None = None,
    value_type: str | None = None,
    unit: str | None = None,
) -> WorldRefusal | None:
    """D9's exact text for a refused M6 write, matched on ``code``; other codes fall through to
    C5's ``write_refusal``, and ``None`` (``idempotency_conflict``, an unknown code, a transport
    error, a ``validation_error`` carrying a ``details.errors`` list, which callers place with
    ``apply_engine_errors``) sends the caller to ``panel_for``."""
    if not isinstance(exc, EngineError):
        return None
    details = exc.details if isinstance(exc.details, Mapping) else {}

    def member(name: str | None) -> str | None:
        if not name:
            return None
        name = name.removeprefix("body.")
        return (field_map or {}).get(name, name)

    placed = member(_string(details, "field"))

    def value(key: str) -> str:
        if key not in details:
            return EMPTY
        return state_value_text(details[key], value_type, unit)

    code = exc.code
    if code == "story_busy":
        text = busy_notice(details)
        seconds = retry_seconds(exc.retry_after)
        if seconds is not None:
            text += f" · retry after {seconds} s"
        return WorldRefusal(text)
    if code == "module_off":
        module = _string(details, "module")
        reason = (
            unavailable_for(module, effective).reason
            if module is not None
            else module_off_unavailable(exc, None, effective).reason
        )
        return WorldRefusal(reason, _url("stories:config", story_id))
    if code == "clock_backward":
        return WorldRefusal(
            "That would move the clock backward (it is at "
            f"{offset_text(details.get('current'))}, you asked for "
            f"{offset_text(details.get('attempted'))}) — earlier story-time is reached by "
            "rewinding",
            field=placed,
        )
    if code == "clock_unanchored":
        to = _string(details, "to")
        what = f'"{to}"' if to is not None else "that time"
        return WorldRefusal(
            f"The clock's anchor doesn't fix the time of day, so {what} can't be placed — "
            "enter minutes from the story's start",
            field=placed or member("to"),
        )
    if code == "clock_external":
        return WorldRefusal(CLOCK_EXTERNAL_TEXT, field=placed)
    if code == "provider_unbound":
        providers = details.get("providers")
        named = (
            [p for p in providers if isinstance(p, str) and p]
            if isinstance(providers, list)
            else []
        )
        target = ", ".join(named) if named else "its providers"
        return WorldRefusal(
            f"This story has no binding to {target} — bind it first",
            _url("stories:providers", story_id),
            placed,
        )
    if code == "state_unpermitted":
        if verb_phrase == COMPENSATE_VERB:
            text = "Only a State an external provider owns can be compensated"
        else:
            text = "This State's authorization doesn't admit the consumer as a source"
        return WorldRefusal(text, field=placed)
    if code == "state_readonly_external":
        return WorldRefusal(READONLY_EXTERNAL_TEXT, field=placed)
    if code == "state_derived":
        return WorldRefusal(
            "This State is derived from other States — it can't be set directly", field=placed
        )
    if code == "state_constraint_violation":
        constraint = _string(details, "constraint")
        if verb_phrase == DEFINITION_VERB:
            breaks = (
                f"the new {_code_span(constraint)} constraint"
                if constraint is not None
                else "one of the new constraints"
            )
            text = f"The current value {value('current')} breaks {breaks}"
        else:
            breaks = (
                f"the State's {_code_span(constraint)} constraint"
                if constraint is not None
                else "one of the State's constraints"
            )
            text = f"That breaks {breaks} (asked {value('attempted')}, now {value('current')})"
        return WorldRefusal(text, field=placed)
    if code == "state_name_taken":
        state = _string(details, "state")
        text = (
            f"A State named “{state}” already exists"
            if state is not None
            else "A State with that name already exists"
        )
        return WorldRefusal(text, field=member("name"))
    if code == "validation_error":
        return _validation_refusal(exc, details, placed, member("place"))
    if code == "viewpoint_unresolved":
        return WorldRefusal(UNKNOWN_CHARACTER, field=placed)
    if code == "not_found":
        listed = details.get("reason")
        if listed == "no_listing":
            return WorldRefusal(
                "This effect is no longer listed for reconciliation — reload the list",
                field=placed,
            )
        if listed == "no_pending_proposal":
            return WorldRefusal(NO_PENDING_PROPOSAL_TEXT, field=placed)
        state = _string(details, "state")
        if listed is None and state is not None:
            return WorldRefusal(state_not_found_text(state), field=member("state"))
        return None
    if code == "settle_refused":
        reasons = details.get("reasons")
        texts = [settle_reason_text(r) for r in reasons] if isinstance(reasons, list) else []
        text = "Settling was refused: " + "; ".join(texts) if texts else "Settling was refused"
        return WorldRefusal(text, field=placed)
    if code == "provider_read_only":
        provider = _string(details, "provider") or "This provider"
        return WorldRefusal(
            f"{provider} is read-only — it can't take a compensating effect", field=placed
        )
    refusal = write_refusal(exc, story_id, verb_phrase)
    return None if refusal is None else WorldRefusal(refusal.text, refusal.url)


def _validation_refusal(
    exc: EngineError, details: Mapping[str, Any], placed: str | None, place: str | None
) -> WorldRefusal | None:
    """D9's ``validation_error`` rows, by ``reason``."""
    reason = details.get("reason")
    if reason == "reserved_state":
        return WorldRefusal(RESERVED_STATE_TEXT, field=placed)
    if reason == "immutable_field":
        if placed is None:
            return WorldRefusal("This field can't change once a State is declared")
        return WorldRefusal(
            f"{humanised(placed)} can't change once a State is declared", field=placed
        )
    if reason == "event_settled":
        return WorldRefusal(event_settled_text(_string(details, "status")), field=placed)
    if reason == "unknown_character":
        return WorldRefusal(UNKNOWN_CHARACTER, field=placed)
    if reason == "unknown_place":
        return WorldRefusal(PLACE_UNKNOWN, field=placed or place)
    if isinstance(details.get("errors"), list):
        return None
    return WorldRefusal(exc.message, field=placed)
