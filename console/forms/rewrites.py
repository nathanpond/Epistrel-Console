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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django import forms

from console.engine.client import EngineClientError, EngineError

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
        choice on a bound form, else the recommended one)."""
        rows = []
        for question in self.questions:
            if question.answered is not None:
                rows.append(QuestionRow(question, None, None, None))
                continue
            choice = self[choice_name(question.id)]
            value = choice.value()
            selected = (str(value) if value else None) if self.is_bound else question.recommended_id
            rows.append(QuestionRow(question, choice, self[text_name(question.id)], selected))
        return tuple(rows)
