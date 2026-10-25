"""Media forms (#70): the upload form and the library's GET filters.

Upload (``/media/upload/``) checks everything it can before ``POST /media`` (a refusal here makes
no upload call):

- ``kind`` is a required radio (image / video); ``?kind=`` may preselect it. The Engine's 415 has
  the final say on the file's type.
- ``content_rating`` comes only from the rating select (#64 convention 14), blank first, no
  preselection. When the ratings could not be read the field is left out and the form cannot be
  submitted.
- Depicted subjects: the picker (``subjects``: ``kind:id`` values from
  :func:`console.engine.choices.subject_choices`) plus typed lines (``subjects_typed``: a bare
  ULID or ``character:`` is a story character; ``catalog_character:`` and ``persona:`` are also
  accepted; ids trimmed and upper-cased). Picked come first in option order, then typed in line
  order, duplicates dropped; at most 50. None at all needs "Depicts nobody" ticked (then ``[]``
  is sent); ticked with subjects is refused.
- The picker accepts any well-formed ``kind:id`` (the POST does not re-read the lists); a value
  missing from the re-rendered list is kept as "<kind> …<last 6> (missing)".
- Poster (a video only): a file or a live image's id, not both. On an image either is an error.
- Alt text (≤ 500) and caption (≤ 2,000) are trimmed; blank ones are not sent. ``owner`` is a
  :class:`~console.forms.principal.PrincipalField`, pre-filled with the acting principal and
  always sent.
"""

import os
from typing import Any, cast

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.http import QueryDict

from console.engine.choices import RatingChoices, SubjectChoices
from console.engine.client import MediaUploadRequest, UploadPart
from console.engine.models import SubjectKind, SubjectRef
from console.forms.catalog import CURSOR_MAX, RELATION_NAME_PATTERN
from console.forms.principal import PrincipalField
from console.ids import is_ulid

KINDS = [("image", "Image"), ("video", "Video")]
SUBJECT_KINDS = ("character", "catalog_character", "persona")
SUBJECTS_MAX = 50
ALT_TEXT_MAX = 500
CAPTION_MAX = 2000
OCTET_STREAM = "application/octet-stream"
MISSING = " (missing)"

NO_SUBJECTS = "Declare who this depicts, or tick “Depicts nobody”"
NOBODY_AND_SUBJECTS = "Untick 'Depicts nobody' or remove the subjects"
TOO_MANY_SUBJECTS = f"At most {SUBJECTS_MAX} subjects"
POSTER_ON_IMAGE = "Posters apply to videos only"
POSTER_BOTH = "Choose a poster file or a poster id, not both"
POSTER_HELP = "Leave both empty and the Engine takes the first frame (needs ffmpeg on the Engine)"
POSTER_DATALIST = "poster-options"
POSTER_ID_INVALID = "Not a media id (26 characters of Crockford base32)"
SUBJECT_INVALID = "Not a subject id (a ULID, or catalog_character:, persona: or character: and one)"


def parse_subject(value: str) -> SubjectRef | None:
    """``kind:id`` (or, ``bare``, an id alone: a story character) as a :class:`SubjectRef`, the
    id trimmed and upper-cased; ``None`` when it is not one."""
    kind, sep, raw = value.strip().partition(":")
    if not sep:
        kind, raw = "character", kind
    kind = kind.strip()
    subject_id = raw.strip().upper()
    if kind not in SUBJECT_KINDS or not is_ulid(subject_id):
        return None
    return SubjectRef(cast(SubjectKind, kind), subject_id)


def subject_value(subject: SubjectRef) -> str:
    return f"{subject.kind}:{subject.id}"


def missing_label(value: str) -> str:
    """A picked value the re-rendered list no longer offers: "<kind> …<last 6> (missing)"."""
    kind, _, subject_id = value.partition(":")
    return f"{kind} …{subject_id[-6:]}{MISSING}"


class SubjectField(forms.MultipleChoiceField):
    """The picker: options are the listed subjects, but any well-formed ``kind:id`` is accepted
    (the POST does not re-read the lists, #70)."""

    def valid_value(self, value: Any) -> bool:
        return isinstance(value, str) and parse_subject(value) is not None and ":" in value


class MediaUploadForm(forms.Form):
    kind = forms.ChoiceField(label="Kind", choices=KINDS, widget=forms.RadioSelect)
    file = forms.FileField(
        label="File",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*,video/*"}),
    )
    content_rating = forms.ChoiceField(label="Content rating")
    subjects = SubjectField(
        label="Depicted subjects",
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 10}),
    )
    subjects_typed = forms.CharField(
        label="Other subject ids",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "One per line: a story character id, or catalog_character:<id>, persona:<id>, "
            "character:<id>"
        ),
    )
    depicts_nobody = forms.BooleanField(label="Depicts nobody", required=False)
    alt_text = forms.CharField(label="Alt text", required=False, max_length=ALT_TEXT_MAX)
    caption = forms.CharField(
        label="Caption",
        required=False,
        max_length=CAPTION_MAX,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": CAPTION_MAX}),
    )
    owner = PrincipalField(label="Owner")
    poster_file = forms.FileField(
        label="Poster file",
        required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
    )
    poster = forms.CharField(label="Poster media id", required=False, max_length=64)

    def __init__(
        self,
        data: Any = None,
        files: Any = None,
        *,
        ratings: RatingChoices,
        subjects: SubjectChoices,
        poster_options: list[tuple[str, str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(data, files, **kwargs)
        self.ratings = ratings
        self.subject_options = subjects
        # The poster id's ``<datalist>``: live images as ``(id, alt text)``; ``None`` (a failed
        # read) leaves the id input without one.
        self.poster_options = poster_options
        if poster_options:
            self.fields["poster"].widget.attrs["list"] = POSTER_DATALIST
        rating = self.fields["content_rating"]
        if ratings.ok and isinstance(rating, forms.ChoiceField):
            rating.choices = [("", "— choose —"), *ratings.choices]
        else:
            del self.fields["content_rating"]
        choices: list[Any] = list(subjects.choices)
        if self.is_bound:
            listed = set(subjects.values)
            name = self.add_prefix("subjects")
            posted = self.data.getlist(name) if isinstance(self.data, QueryDict) else []
            missing = [
                (value, missing_label(value))
                for value in dict.fromkeys(posted)
                if value not in listed and parse_subject(value) is not None and ":" in value
            ]
            if missing:
                choices.append(("Not listed", missing))
        picker = self.fields["subjects"]
        if isinstance(picker, SubjectField):
            picker.choices = choices

    @property
    def can_submit(self) -> bool:
        """Whether the rating select is there (the rating is required and comes only from it)."""
        return self.ratings.ok

    def clean_subjects(self) -> list[SubjectRef]:
        values: list[str] = self.cleaned_data.get("subjects") or []
        order = {value: n for n, value in enumerate(self.subject_options.values)}
        ranked = sorted(
            enumerate(values), key=lambda item: (order.get(item[1], len(order)), item[0])
        )
        picked = [parse_subject(value) for _, value in ranked]
        return [subject for subject in picked if subject is not None]

    def clean_subjects_typed(self) -> list[SubjectRef]:
        text: str = self.cleaned_data.get("subjects_typed") or ""
        found: list[SubjectRef] = []
        errors: list[str] = []
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            subject = parse_subject(line)
            if subject is None:
                errors.append(f"Line {n} ({line.strip()!r}): {SUBJECT_INVALID}")
            else:
                found.append(subject)
        if errors:
            raise forms.ValidationError(errors)
        return found

    def clean_poster(self) -> str:
        value: str = (self.cleaned_data.get("poster") or "").strip().upper()
        if value and not is_ulid(value):
            raise forms.ValidationError(POSTER_ID_INVALID)
        return value

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        data = self.cleaned_data
        if not self.ratings.ok:  # no select, so no rating: the upload cannot be sent
            self.add_error(None, self.ratings.unavailable_text)
        if "subjects" not in self.errors and "subjects_typed" not in self.errors:
            subjects = list(
                dict.fromkeys([*data.get("subjects", []), *data.get("subjects_typed", [])])
            )
            data["depicted_subjects"] = subjects
            if data.get("depicts_nobody") and subjects:
                self.add_error("depicts_nobody", NOBODY_AND_SUBJECTS)
            elif not data.get("depicts_nobody") and not subjects:
                self.add_error("subjects", NO_SUBJECTS)
            elif len(subjects) > SUBJECTS_MAX:
                self.add_error("subjects", f"{TOO_MANY_SUBJECTS} ({len(subjects)} given)")
        has_file = bool(data.get("poster_file"))
        has_id = bool(data.get("poster"))
        if data.get("kind") == "image" and (has_file or has_id):
            self.add_error("poster_file" if has_file else "poster", POSTER_ON_IMAGE)
        elif has_file and has_id:
            self.add_error("poster", POSTER_BOTH)
        return cleaned

    def metadata(self) -> MediaUploadRequest:
        """The ``metadata`` part for a valid form."""
        data = self.cleaned_data
        return MediaUploadRequest(
            kind=data["kind"],
            content_rating_id=data["content_rating"],
            depicted_subjects=list(data["depicted_subjects"]),
            owner=data["owner"],
            alt_text=data.get("alt_text") or None,
            caption=data.get("caption") or None,
            poster=data.get("poster") or None,
        )

    def file_part(self) -> UploadPart:
        return upload_part(self.cleaned_data["file"])

    def poster_part(self) -> UploadPart | None:
        poster = self.cleaned_data.get("poster_file")
        return upload_part(poster) if poster else None


def upload_part(uploaded: UploadedFile[bytes]) -> UploadPart:
    """An uploaded file as a request part: its base name, its declared type (else
    ``application/octet-stream``), its size, streamed from Django's own copy (memory or a
    temporary file Django deletes when the request ends)."""
    name = os.path.basename(uploaded.name or "") or "upload"
    content_type = uploaded.content_type or OCTET_STREAM
    uploaded.seek(0)
    return UploadPart(name, content_type, uploaded.size or 0, uploaded)


# --- library filters ------------------------------------------------------------------------------

KIND_FILTER = [("", "any"), ("image", "image"), ("video", "video")]


class MediaFilterForm(forms.Form):
    """The library's filters: kind, owner (``system`` allowed, never remembered), one relation
    (a name plus an optional principal) and "Show retired" (``?retired=1`` lists only retired
    assets, ``state=retired``)."""

    kind = forms.ChoiceField(required=False, label="Kind", choices=KIND_FILTER)
    owner = PrincipalField(allow_system=True, remember=False, required=False, label="Owner")
    relation = forms.CharField(required=False, label="Relation", max_length=64)
    relation_principal = PrincipalField(
        allow_system=True, remember=False, required=False, label="with principal"
    )
    retired = forms.BooleanField(required=False, label="Show retired")
    cursor = forms.CharField(required=False, max_length=CURSOR_MAX, widget=forms.HiddenInput)

    def clean_relation(self) -> str:
        value: str = (self.cleaned_data.get("relation") or "").strip()
        if value and not RELATION_NAME_PATTERN.match(value):
            raise forms.ValidationError("A relation name is 1 to 64 letters, digits, '_' or '-'.")
        return value

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean()
        data = self.cleaned_data
        if (
            data.get("relation_principal")
            and not data.get("relation")
            and "relation" not in self.errors
        ):
            self.add_error("relation", "Enter a relation name")
        return cleaned

    @property
    def retired_only(self) -> bool:
        return bool(self.is_bound and self.is_valid() and self.cleaned_data.get("retired"))

    @property
    def any_filter(self) -> bool:
        """Whether a kind, owner or relation filter is set ("Show retired" is not a filter: it
        picks the list)."""
        if not self.is_bound or not self.is_valid():
            return False
        data = self.cleaned_data
        return bool(data.get("kind") or data.get("owner") or data.get("relation"))

    def query(self) -> dict[str, Any]:
        """``list_media``'s keyword arguments for a valid form."""
        data = self.cleaned_data
        relation = data.get("relation") or ""
        return {
            "kind": data.get("kind") or None,
            "owner": data.get("owner") or None,
            "relations": [f"{relation}:{data.get('relation_principal') or ''}"] if relation else [],
            "state": "retired" if data.get("retired") else None,
            "cursor": data.get("cursor") or None,
        }
