"""The Engine panel's certification admin forms (#315; Engine #233/#235, #234; D14, E21).

:class:`CertificationImportForm` backs "Import a certification record"
(``engine:certification-import``). A hand run of ``epistrel evals`` writes the record as a JSON
file; it is given either as that file or pasted, and exactly one of the two is required (both or
neither → :data:`ONE_OF_THE_TWO`, a non-field error):

- ``record_file``: a file whose name ends in ``.json`` (any case; the browser's content type is
  ignored), at most :data:`MAX_RECORD_BYTES`, checked before it is read, then read as
  ``utf-8-sig``. Its errors, in order: :data:`FILE_NOT_JSON_NAME`, :data:`FILE_TOO_BIG`,
  :data:`FILE_EMPTY` (a blank file too), :data:`FILE_NOT_UTF8`, then ``JsonBoxField``'s own
  invalid-JSON / wrong-type messages, all on ``record_file``.
- ``record``: a ``JsonBoxField(dict)`` textarea (D14's strict rules: no duplicate keys, no
  NaN/Infinity, an object at the top), capped at the same size counted in UTF-8 bytes
  (:data:`PASTED_TOO_BIG`). A whitespace-only textarea counts as not given.

File and textarea go through the one parse helper, :func:`parse_record_text`. Once a file
decodes, its text is placed in the ``record`` textarea of the bound data and the textarea's help
reads "Loaded from <filename>" (:data:`LOADED_FROM`): a browser can't refill a file input, so a
re-render keeps the record editable there.

**Local checks** (before any call, only what the Console can see), on ``record``: the members
:data:`REQUIRED_MEMBERS` (the schema's required ones plus ``report_md``, which the import route
requires) → :data:`NOT_A_RECORD` naming the missing ones in that order; then ``schema_version``
must be the int 1 exactly (not a bool, ``1.0`` or ``"1"``) → :data:`SCHEMA_VERSION_ONLY_1`. The
report-hash rule is the Engine's alone (its normalisation), so the Console doesn't redo it.

:attr:`CertificationImportForm.preview` is the record's id, fingerprint and ``run_at`` whenever
the object parsed (local-check failures included), missing members as "—".

:class:`InvalidateFields` is the invalidate confirm page's inputs: ``reason`` (a required radio,
``drift`` / ``manual``, no default) and ``note`` (a required textarea, stripped, at most
:data:`NOTE_MAX`, the Engine's ``InvalidateRequest.note`` limit).
"""

from datetime import datetime
from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.forms.fields import JsonBoxField
from console.impact import short_fingerprint
from console.raw import EMPTY
from console.templatetags.console_format import engine_time
from console.templatetags.inspect_tags import engine_id

MAX_RECORD_BYTES = 2 * 1024 * 1024  # 2 MiB; with form overhead under DATA_UPLOAD_MAX_MEMORY_SIZE
REQUIRED_MEMBERS = (
    "schema_version",
    "certification_id",
    "fingerprint",
    "seed_count",
    "report_sha256",
    "run_at",
    "report_md",
)
NOTE_MAX = 1000

ONE_OF_THE_TWO = "Paste the record or choose its file — one of the two"
FILE_NOT_JSON_NAME = "Choose a .json file"
FILE_TOO_BIG = "The file is over 2 MiB"
FILE_EMPTY = "The file is empty"
FILE_NOT_UTF8 = "The file isn't UTF-8 text"
PASTED_TOO_BIG = "The record is over 2 MiB"
NOT_A_RECORD = "This isn't a certification record (missing: {members})"
SCHEMA_VERSION_ONLY_1 = "Only record schema version 1 can be imported"
LOADED_FROM = "Loaded from {name}"
RECORD_HELP = "The record as JSON, report_md included"
RECORD_FILE_HELP = "The .json file a hand run of epistrel evals wrote (at most 2 MiB)"

REASON_CHOICES = (
    ("drift", "The model changed under the same name and version"),
    ("manual", "Withdrawn by an operator"),
)
NOTE_HELP = "Kept for the audit trail"

_BOX = JsonBoxField(dict)


def parse_record_text(text: str) -> dict[str, Any] | None:
    """``text`` as a JSON object under ``JsonBoxField``'s strict rules (D14); ``None`` when it
    is blank. Raises ``ValidationError`` with the field's own messages."""
    parsed = _BOX.to_python(text)
    return parsed if isinstance(parsed, dict) else None


class RecordBoxField(JsonBoxField):
    """The pasted record: :data:`PASTED_TOO_BIG` over :data:`MAX_RECORD_BYTES` (UTF-8 bytes),
    checked before parsing."""

    def to_python(self, value: Any) -> Any:
        if isinstance(value, str) and len(value.encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValidationError(PASTED_TOO_BIG, code="too_big")
        return super().to_python(value)


def _member(record: dict[str, Any], name: str) -> str | None:
    value = record.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _run_html(value: str | None) -> SafeString | str:
    if value is None:
        return EMPTY
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return format_html("<code>{}</code>", value)
    return engine_time(value)


def preview_html(record: dict[str, Any]) -> SafeString:
    """ "Certification <short id> · fingerprint <short> · run <engine_time>"; a missing member
    reads "—", a ``run_at`` that doesn't parse is shown raw in ``<code>``."""
    cid = _member(record, "certification_id")
    fingerprint = _member(record, "fingerprint")
    shown_fp = (
        format_html(
            '<code class="hash" title="{}">{}</code>', fingerprint, short_fingerprint(fingerprint)
        )
        if fingerprint
        else EMPTY
    )
    return format_html(
        "Certification {} · fingerprint {} · run {}",
        engine_id(cid) if cid else EMPTY,
        shown_fp,
        _run_html(_member(record, "run_at")),
    )


def missing_members(record: dict[str, Any]) -> list[str]:
    return [name for name in REQUIRED_MEMBERS if name not in record]


def schema_version_ok(value: object) -> bool:
    return type(value) is int and value == 1


class CertificationImportForm(forms.Form):
    """The import page's form (module docstring)."""

    record_file = forms.FileField(
        required=False,
        label="Record file",
        help_text=RECORD_FILE_HELP,
        error_messages={"empty": FILE_EMPTY},
        widget=forms.ClearableFileInput(attrs={"accept": ".json,application/json"}),
    )
    record = RecordBoxField(
        dict,
        required=False,
        label="Or paste the record",
        help_text=RECORD_HELP,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.loaded_from = ""
        self.parsed: dict[str, Any] | None = None

    def clean(self) -> dict[str, Any] | None:
        cleaned = super().clean() or {}
        upload = cleaned.get("record_file")
        pasted = (self.data.get("record") or "").strip()
        if bool(upload) == bool(pasted):
            raise ValidationError(ONE_OF_THE_TWO, code="one_of_the_two")
        record = self._read(upload) if isinstance(upload, UploadedFile) else cleaned.get("record")
        if not isinstance(record, dict):
            return cleaned
        self.parsed = record
        missing = missing_members(record)
        if missing:
            listed = format_html(", ".join("<code>{}</code>" for _ in missing), *missing)
            self.add_error("record", format_html(NOT_A_RECORD, members=listed))
        elif not schema_version_ok(record["schema_version"]):
            self.add_error("record", SCHEMA_VERSION_ONLY_1)
        cleaned["record"] = record
        return cleaned

    def _read(self, upload: UploadedFile[bytes]) -> dict[str, Any] | None:
        """The file's record, or ``None`` with its error on ``record_file``. Once the file
        decodes, its text goes in the textarea (module docstring)."""
        name = upload.name or ""
        if not name.lower().endswith(".json"):
            self.add_error("record_file", FILE_NOT_JSON_NAME)
            return None
        size = upload.size or 0
        if size > MAX_RECORD_BYTES:
            self.add_error("record_file", FILE_TOO_BIG)
            return None
        if size == 0:
            self.add_error("record_file", FILE_EMPTY)
            return None
        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            self.add_error("record_file", FILE_NOT_UTF8)
            return None
        self._load(text, name)
        if not text.strip():
            self.add_error("record_file", FILE_EMPTY)
            return None
        try:
            return parse_record_text(text)
        except ValidationError as exc:
            self.add_error("record_file", exc)
            return None

    def _load(self, text: str, name: str) -> None:
        self.data = {**self.data, "record": text}
        self.loaded_from = name
        help_text = LOADED_FROM.format(name=name)
        self.fields["record"].help_text = help_text
        self["record"].help_text = help_text  # a BoundField copies its field's help at creation

    @property
    def record_text(self) -> str:
        """The textarea's text as the page carries it (the pasted text, or the file's)."""
        value = self.data.get("record") if self.is_bound else ""
        return value if isinstance(value, str) else ""

    @property
    def preview(self) -> SafeString | None:
        return preview_html(self.parsed) if self.parsed is not None else None


class InvalidateFields(forms.Form):
    """The invalidate confirm page's ``reason`` and ``note`` (module docstring)."""

    reason = forms.ChoiceField(
        choices=REASON_CHOICES, widget=forms.RadioSelect, label="Reason", required=True
    )
    note = forms.CharField(
        max_length=NOTE_MAX,
        strip=True,
        required=True,
        label="Note",
        help_text=NOTE_HELP,
        widget=forms.Textarea(attrs={"rows": 4, "cols": 60}),
    )
