"""The forms every M7 page that opens or answers a rewrite session shares (#294).

**E1 — the import rule.** A page story imports these forms (mixing :class:`RewriteModesForm`
into its own) and never defines a preview or resolution-mode field, or an answers form, of its
own. ``console.rewrites`` holds the session view these forms read and
:func:`console.rewrites.resolution_mode_text`, which reads a stored mode back to these labels.

**E4 — Rewrite modes and answers.**

- :class:`RewriteModesForm` (mixed into every form that opens a session: #297's New rewrite,
  #305's line edit, #300's Direct, #301's directed regenerate, #303's undo/redo, #306's merge)
  has two fields:

  - ``apply_directly`` — a ``BooleanField``, unticked by default, under an "Advanced"
    ``<details>`` (``templates/console/rewrites/_modes.html``), labelled "Apply directly (skip
    the preview)";
  - ``resolution`` — a ``ChoiceField`` over :data:`RESOLUTION_CHOICES`: ``synchronous`` "Ask me
    about every conflict" (initial), ``best_effort`` "Decide for me (least change)",
    ``threshold_high`` "Ask only about high-severity conflicts", ``threshold_low`` "Ask about
    high- and low-severity conflicts".

  :meth:`RewriteModesForm.modes` → ``{"preview": "apply_directly" | "preview_first",
  "resolution_mode": "synchronous" | "best_effort" | {"threshold": "high" | "low"}}``, merged
  into the request body. An Engine ``body.preview`` loc maps to ``apply_directly`` and
  ``body.resolution_mode`` to ``resolution`` (E21).
- ``resolution_mode_text(value)`` (in ``console.rewrites``) reads the Engine's stored
  ``resolution_mode`` back to the same labels; an unknown value is compact JSON.
- :class:`AnswersForm` ``(questions, data=None)`` has one group per *unanswered* question
  (``questions`` is ``session_view(...).questions``, so the form and the partial share the
  recommended option and the answered state):

  - ``choice_<qid>`` (radio, initial = the recommended option);
  - ``text_<qid>`` (``CharField``, max 2,000, the Engine's ``ANSWER_TEXT_MAX``).

  The submit buttons are "Submit answers" and "Accept every recommendation" (``accept_all``).
  :meth:`AnswersForm.answers` → the Engine's ``answers`` list:

  - with ``accept_all``, ``{"question_id", "accept_recommended": true}`` for each question that
    has a recommendation (the others go by their radio and text, as for "Submit answers");
  - with text given, ``{"question_id", "text"}`` (text wins over the radio; the help says so);
  - else ``{"question_id", "option_id"}``.

  A question left on its pre-selected recommendation sends ``option_id`` (the recommended one),
  not ``accept_recommended``; only "Accept every recommendation" sends ``accept_recommended``.
  An optional question left untouched (its choice the recommended initial, its text blank) is
  omitted. A required question with no recommendation and no text is Django's required-choice
  error. A 422 ``invalid_answer`` puts ``details.question_id``'s message on that group
  (:meth:`AnswersForm.apply_engine_error`), else above the form.

**#297 — the Rewrite page's forms.** :class:`NewRewriteForm` is the instruction (a textarea,
required, stripped, at most :data:`INSTRUCTION_MAX` code points with no HTML ``maxlength``, C10)
plus E4's modes; :meth:`NewRewriteForm.body` is ``{"instruction", **modes()}``.
:class:`RewriteFilterForm` is E22's state filter: one checkbox per ``STATE_TEXT`` state in its
order, none ticked meaning every state; :meth:`RewriteFilterForm.states` gives the ticked ones
de-duplicated in that order.

**#305 — the line edit.** :class:`LineEditForm` is ``new_text`` (kept verbatim: only
``\r\n``/``\r`` become ``\n``; whitespace-only is blank; at most :data:`LINE_MAX` characters;
unchanged from the hidden ``original`` is :data:`LINE_UNCHANGED`, checked only when the page read
the line) plus E4's modes and the hidden target. :func:`line_target` parses the target from a
query or the POST, :class:`LineTarget` carries it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django import forms

from console.engine.client import EngineClientError, EngineError
from console.ids import is_ulid

if TYPE_CHECKING:
    from console.rewrites import QuestionView

PREVIEW_FIRST = "preview_first"
APPLY_DIRECTLY = "apply_directly"
SYNCHRONOUS = "synchronous"
RESOLUTION_CHOICES = (
    ("synchronous", "Ask me about every conflict"),
    ("best_effort", "Decide for me (least change)"),
    ("threshold_high", "Ask only about high-severity conflicts"),
    ("threshold_low", "Ask about high- and low-severity conflicts"),
)
THRESHOLDS = {"threshold_high": "high", "threshold_low": "low"}
APPLY_DIRECTLY_LABEL = "Apply directly (skip the preview)"
RESOLUTION_LABEL = "Conflicts"
ANSWER_TEXT_MAX = 2000  # the Engine's ``ANSWER_TEXT_MAX``
ANSWER_TEXT_HELP = "Text you write here is sent instead of the option picked above"
ACCEPT_ALL = "accept_all"
INVALID_ANSWER = "invalid_answer"


class RewriteModesForm(forms.Form):
    """E4's two mode fields; mix it into a form that opens a rewrite session."""

    apply_directly = forms.BooleanField(required=False, label=APPLY_DIRECTLY_LABEL)
    resolution = forms.ChoiceField(
        choices=RESOLUTION_CHOICES, initial=SYNCHRONOUS, label=RESOLUTION_LABEL
    )

    def modes(self) -> dict[str, Any]:
        """The ``preview`` and ``resolution_mode`` members of the request body (a valid form)."""
        resolution = self.cleaned_data["resolution"]
        mode: Any = (
            {"threshold": THRESHOLDS[resolution]} if resolution in THRESHOLDS else resolution
        )
        preview = APPLY_DIRECTLY if self.cleaned_data.get("apply_directly") else PREVIEW_FIRST
        return {"preview": preview, "resolution_mode": mode}


@dataclass(frozen=True)
class QuestionRow:
    """One question as ``_questions.html`` lays it out: the view, its two bound fields (``None``
    for an answered question) and the option id shown checked."""

    question: QuestionView
    choice: forms.BoundField | None
    text: forms.BoundField | None
    selected: str | None


def choice_name(question_id: str) -> str:
    return f"choice_{question_id}"


def text_name(question_id: str) -> str:
    return f"text_{question_id}"


class AnswersForm(forms.Form):
    """E4's answers form over ``session_view(...).questions``: a ``choice_<qid>`` radio and a
    ``text_<qid>`` box per unanswered question."""

    def __init__(
        self,
        questions: Sequence[QuestionView],
        data: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.questions = tuple(questions)
        for question in self.unanswered:
            self.fields[choice_name(question.id)] = forms.ChoiceField(
                choices=[(option.id, option.label) for option in question.options],
                required=False,
                initial=question.recommended_id,
                widget=forms.RadioSelect,
                label=question.prompt,
            )
            self.fields[text_name(question.id)] = forms.CharField(
                max_length=ANSWER_TEXT_MAX,
                required=False,
                label="Or answer in your own words",
                help_text=ANSWER_TEXT_HELP,
                widget=forms.Textarea(attrs={"rows": 2}),
            )

    @property
    def unanswered(self) -> tuple[QuestionView, ...]:
        return tuple(q for q in self.questions if q.answered is None)

    @property
    def accept_all(self) -> bool:
        return self.is_bound and bool(self.data.get(ACCEPT_ALL))

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for question in self.unanswered:
            if not question.required:
                continue
            choice, text = choice_name(question.id), text_name(question.id)
            if cleaned.get(text) or cleaned.get(choice):
                continue
            if self.accept_all and question.recommended_id:
                continue
            if choice not in self.errors:
                self.add_error(choice, self.fields[choice].error_messages["required"])
        return cleaned

    def answers(self) -> list[dict[str, Any]]:
        """The Engine's ``answers`` list (a valid form), in the Engine's question order."""
        sent: list[dict[str, Any]] = []
        for question in self.unanswered:
            choice = self.cleaned_data.get(choice_name(question.id)) or ""
            text = self.cleaned_data.get(text_name(question.id)) or ""
            if self.accept_all and question.recommended_id:
                sent.append({"question_id": question.id, "accept_recommended": True})
            elif text:
                sent.append({"question_id": question.id, "text": text})
            elif not question.required and choice in ("", question.recommended_id):
                continue  # an untouched optional question is omitted
            elif choice:
                sent.append({"question_id": question.id, "option_id": choice})
        return sent

    def text_question_ids(self) -> list[str]:
        """The ids of the questions :meth:`answers` sends as free text, in order: the Engine's
        ``body.answers[n].text`` locator counts only these (E5's guardrail case)."""
        return [a["question_id"] for a in self.answers() if "text" in a]

    def apply_engine_error(self, exc: EngineClientError) -> bool:
        """A 422 ``invalid_answer``: its message on ``details.question_id``'s group, else above
        the form. ``False`` (nothing placed) for any other error."""
        if not isinstance(exc, EngineError) or exc.code != INVALID_ANSWER:
            return False
        question_id = exc.details.get("question_id") if isinstance(exc.details, Mapping) else None
        name = choice_name(question_id) if isinstance(question_id, str) else None
        self.add_error(name if name in self.fields else None, exc.message)
        return True

    @property
    def question_rows(self) -> tuple[QuestionRow, ...]:
        """Every question, in order, with its fields and the option shown checked (the posted
        choice on a bound form; on an unbound one its ``initial``, which defaults to the
        recommended option: #298's refusal re-render copies the posted choices into it)."""
        rows = []
        for question in self.questions:
            if question.answered is not None:
                rows.append(QuestionRow(question, None, None, None))
                continue
            choice = self[choice_name(question.id)]
            value = choice.value()
            selected = str(value) if value else None
            rows.append(QuestionRow(question, choice, self[text_name(question.id)], selected))
        return tuple(rows)


# --- #297: the Rewrite page's forms -----------------------------------------------------------

INSTRUCTION_MAX = 4000  # the Engine's ``instruction`` max_length (code points, stripped)
INSTRUCTION_REQUIRED = "Say what was really true about the story's past"
INSTRUCTION_TOO_LONG = "At most 4,000 characters"
INSTRUCTION_HELP = (
    "Describe the past as it really was, e.g. “Bobby actually went to UCLA.” Nothing changes "
    "until you apply the plan"
)
STATE_PARAM = "state"


class NewRewriteForm(RewriteModesForm):
    """#297's New rewrite form: the instruction (required, stripped, at most
    :data:`INSTRUCTION_MAX` characters, no HTML ``maxlength`` per C10) plus E4's modes."""

    instruction = forms.CharField(
        label="Instruction",
        help_text=INSTRUCTION_HELP,
        widget=forms.Textarea(attrs={"rows": 4}),
        error_messages={"required": INSTRUCTION_REQUIRED},
    )

    field_order = ("instruction", "resolution", "apply_directly")

    def clean_instruction(self) -> str:
        text: str = self.cleaned_data["instruction"]
        if len(text) > INSTRUCTION_MAX:
            raise forms.ValidationError(INSTRUCTION_TOO_LONG)
        return text

    def body(self) -> dict[str, Any]:
        """``POST …/rewrites``'s body (a valid form): the instruction and E4's modes."""
        return {"instruction": self.cleaned_data["instruction"], **self.modes()}


def _state_choices() -> list[tuple[str, str]]:
    from console.rewrites import STATE_TEXT  # console.rewrites imports this module

    return list(STATE_TEXT.items())


class RewriteFilterForm(forms.Form):
    """#297's state filter (E22): one checkbox per E3 state, in ``STATE_TEXT``'s order. None
    ticked means every state (no ``state`` sent); a value outside the choices is Django's
    ``invalid_choice`` error, and the page makes no list read."""

    state = forms.MultipleChoiceField(
        choices=_state_choices,
        required=False,
        label="State",
        widget=forms.CheckboxSelectMultiple,
    )

    def states(self) -> tuple[str, ...]:
        """The ticked states (a valid form), de-duplicated, in the choices' order."""
        ticked = set(self.cleaned_data.get("state") or ())
        return tuple(value for value, _ in _state_choices() if value in ticked)


# --- #305: the line-edit form -------------------------------------------------------------------

LINE_MAX = 8000  # the Engine's ``line_edit.new_text`` max_length (pydantic, ``len()``)
LINE_REQUIRED = "Write the line's new text"
LINE_TOO_LONG = "At most 8,000 characters"
LINE_UNCHANGED = "That's the line as recorded — change it first"
LINE_LABEL = "New text"
PLAN_EDIT = "Plan the edit"


def normalised_line(text: str) -> str:
    """``\\r\\n`` and ``\\r`` read as ``\\n`` (what a browser posts for a textarea's newlines);
    nothing else in the line changes."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class LineTarget:
    """The line a line edit names: by ``event_id`` (the history item's ``message_id``), else by
    ``turn`` and an optional ``beat``. In id mode ``turn`` rides along only to read the line's
    current text (``None`` when it was missing or malformed)."""

    event_id: str | None
    turn: int | None
    beat: int | None = None


def _whole(value: object, minimum: int) -> tuple[bool, int | None]:
    """A posted whole number ≥ ``minimum``: ``(True, n)``; ``(True, None)`` when blank;
    ``(False, None)`` when malformed."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return True, None
    if not text.isascii() or not text.isdigit() or int(text) < minimum:
        return False, None
    return True, int(text)


def line_target(params: Mapping[str, Any]) -> LineTarget | None:
    """The target in ``params`` (a GET query or the POST's hidden fields), or ``None`` when it
    names no line: ``event_id`` (trimmed, upper-cased, C13) must be a ULID, and a malformed one
    refuses even beside a valid ``turn``; without it, ``turn`` (≥ 1) is required and ``beat``
    (≥ 0) optional, either malformed refusing (``beat`` alone too). With a good ``event_id``, a
    bad ``turn`` is dropped (id mode, no read)."""
    raw_id = str(params.get("event_id") or "").strip().upper()
    _, turn = _whole(params.get("turn"), 1)
    if raw_id:
        return LineTarget(raw_id, turn) if is_ulid(raw_id) else None
    beat_ok, beat = _whole(params.get("beat"), 0)
    if turn is None or not beat_ok:
        return None
    return LineTarget(None, turn, beat)


class LineEditForm(RewriteModesForm):
    """#305's line edit: ``new_text`` (a textarea, required, kept verbatim, at most
    :data:`LINE_MAX` characters after :func:`normalised_line`, no HTML ``maxlength``, C10), the
    hidden target and ``original`` (the text the page read, compared only when it was read), and
    E4's modes. :meth:`body` is ``{"line_edit": {event_id | turn[, beat], new_text}, **modes()}``
    for the ``target`` the view parsed (never both an id and a turn, never an instruction)."""

    new_text = forms.CharField(
        label=LINE_LABEL,
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    event_id = forms.CharField(required=False, widget=forms.HiddenInput)
    turn = forms.CharField(required=False, widget=forms.HiddenInput)
    beat = forms.CharField(required=False, widget=forms.HiddenInput)
    next = forms.CharField(required=False, widget=forms.HiddenInput)
    original = forms.CharField(required=False, strip=False, widget=forms.HiddenInput)

    field_order = ("new_text", "resolution", "apply_directly")

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        target: LineTarget | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.target = target

    def clean_new_text(self) -> str:
        text = normalised_line(self.cleaned_data.get("new_text") or "")
        if not text.strip():
            raise forms.ValidationError(LINE_REQUIRED)
        if len(text) > LINE_MAX:
            raise forms.ValidationError(LINE_TOO_LONG)
        original = normalised_line(self.data.get("original") or "") if self.data else ""
        if original and text == original:
            raise forms.ValidationError(LINE_UNCHANGED)
        return text

    def body(self) -> dict[str, Any]:
        """``POST …/rewrites``'s body (a valid form with a target)."""
        target = self.target
        if target is None:
            raise ValueError("a line edit's body needs its target")
        line: dict[str, Any]
        if target.event_id:
            line = {"event_id": target.event_id}
        else:
            line = {"turn": target.turn}
            if target.beat is not None:
                line["beat"] = target.beat
        line["new_text"] = self.cleaned_data["new_text"]
        return {"line_edit": line, **self.modes()}
