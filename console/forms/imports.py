"""The bundle import's form and refusals (#320; FR-STORE-7, FR-ADMIN-4; E8, E13, E14, E21, E23).

**The form.** :class:`ImportForm` asks, in order, for the bundle ``file`` (a required
``FileField``, unless a kept spool stands in for it), the ``owner``, the ``name``, the
``content_rating_id``, the ``engine_profile_id`` and E14's ``upgrade_mode``. Its choices come
from three Engine reads (:func:`read_import_choices`): ``list_content_ratings()`` (non-retired,
in the Engine's order, by name), ``list_engine_profiles(preset=True)`` and
``list_engine_profiles()`` (presets first through E13's ``preset_order``/``preset_label``, then
the other profiles; deleted ones dropped, deduplicated by id keeping the preset entry, a
non-preset labelled by name with "(v<n>)" only when two share a name). A failed ratings read, or
either failed profiles read, leaves that field its blank choice alone with
:data:`RATINGS_UNREAD` / :data:`PROFILES_UNREAD` as its help (#50's settings form does the same),
so only ``""`` is valid for it. :meth:`ImportForm.metadata` builds the ``ImportMetadata`` part:
every blank optional member is left out (never ``""``), so the Engine's own defaults apply (the
requesting principal, the bundle's story name, the rating whose name matches the bundle's, the
bundle's own profile); ``upgrade_mode`` is always sent and ``ignore_warnings`` is ``false``.

**A kept spool.** After a refusal a metadata change can fix, the page carries the spool's signed
name in the hidden ``spool`` field and a checked ``keep_spool`` box ("Keep the uploaded file
<filename> (<size>)"); with both, ``file`` isn't required. A newly chosen file wins (the view
deletes the old spool). The box unticked, or a tampered field, with no new file → ``file``'s
"required"; a spool the sweep removed → :data:`~console.import_spool.IMPORT_SPOOL_EXPIRED` on
``file``.

**Refusals.** :func:`import_refusal` words the Engine's import refusals as a
:class:`FormRefusal` placed on a field (or above the form, ``field=""``); ``None`` means #43's
panel. :data:`FILE_REFUSALS` delete the spool; :data:`FIXABLE_REFUSALS` keep it.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from django import forms
from django.template.defaultfilters import filesizeformat
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from console.engine.client import EngineClient, EngineClientError, EngineError
from console.engine.models import ContentRatingSummary, EngineProfile
from console.engine_form_errors import engine_field_errors
from console.forms.upgrades import UpgradeModeField
from console.held import is_held
from console.import_spool import IMPORT_SPOOL_EXPIRED, Spool, spool_exists, unsign_spool
from console.upgrades import preset_label, preset_order
from console.wording import humanised

RESTORE_LINE = (
    "Importing a bundle always makes a new story; the story it came from is never changed"
)
FILE_HELP = "A .epistrel.tgz bundle from a story's Export"
OWNER_HELP = "Blank: you ({principal})"
NAME_HELP = "Blank: the bundle's story name"
RATING_DEFAULT = "The rating whose name matches the bundle's"
PROFILE_DEFAULT = "The bundle's own profile if this Engine has it, else the deployment default"
RATINGS_UNREAD = "The ratings could not be read"
PROFILES_UNREAD = "The profiles could not be read"
KEEP_TEXT = "Keep the uploaded file {filename} ({size})"
OWNER_DEFAULT_TEXT = "you ({principal})"
NAME_DEFAULT_TEXT = "the bundle's story name"
OWNER_MAX = 255
NAME_MAX = 200

FILE = "file"
SPOOL_FIELD = "spool"
KEEP_FIELD = "keep_spool"
METADATA_FIELDS = ("owner", "name", "content_rating_id", "engine_profile_id", "upgrade_mode")
OPTIONAL_MEMBERS = ("owner", "name", "content_rating_id", "engine_profile_id")

# --- refusal wording ------------------------------------------------------------------------------

BUNDLE_TOO_LARGE = "bundle_too_large"
CORRUPT_BUNDLE = "corrupt_bundle"
UNSUPPORTED_FORMAT = "unsupported_format_version"
RATING_REQUIRED = "rating_required"
VALIDATION_ERROR = "validation_error"
PROFILE_DELETED = "profile_deleted"
FILE_REFUSALS = frozenset({BUNDLE_TOO_LARGE, CORRUPT_BUNDLE, UNSUPPORTED_FORMAT})
FIXABLE_REFUSALS = frozenset({RATING_REQUIRED, VALIDATION_ERROR, PROFILE_DELETED})

NOTHING_CREATED = " — nothing was created"
TOO_LARGE_TEXT = "The bundle is larger than this Engine accepts ({limit}{value})" + NOTHING_CREATED
DEFAULT_LIMIT = "bundle_max_uncompressed_bytes"
BUNDLE_LIMITS = frozenset(
    {"bundle_max_uncompressed_bytes", "bundle_max_entries", "bundle_max_entry_bytes"}
)
SETTINGS_ANCHOR = "#settings-story-bundles"
CORRUPT_TEXT = (
    "The bundle is damaged or isn't an Epistrel bundle: {reason}{entry}" + NOTHING_CREATED
)
CORRUPT_REASON_TEXT: Mapping[str, str] = {
    "unreadable_archive": "the archive couldn't be read",
    "manifest_invalid": "its manifest is missing or invalid",
    "member_order": "its members are in the wrong order",
    "checksum_mismatch": "a member doesn't match its checksum",
    "schema_violation": "a member doesn't match its schema",
    "entry_outside_archive": "an entry points outside the archive",
    "unexpected_entry": "it holds an entry its manifest doesn't list",
    "payload_invalid": "a member's contents are invalid",
    "media_missing": "a media file it refers to is missing",
    "erasure_race": "an erasure ran at the same time — try again",
}
NO_REASON_TEXT = "the Engine gave no reason"
UNSUPPORTED_TEXT = "This bundle's format ({found}) isn't one this Engine reads ({supported})"
UNKNOWN_FORMAT = "unknown"
SUPPORTED_DEFAULT = "1.x"
RATING_NO_MATCH = "The bundle's rating “{name}” matches no rating here — choose one"
RATING_SEVERAL = "The bundle's rating “{name}” matches more than one rating here — choose one"
RATING_UNNAMED = "The bundle names no rating — choose one"
CLOSEST_TEXT = "Closest: {}"
PROFILE_DELETED_TEXT = "That engine profile has been deleted — choose another"
METADATA_PREFIXES = ("body.metadata.", "metadata.")


@dataclass(frozen=True)
class FormRefusal:
    """A refusal on the import form: its ``text`` (with markup when it links), the ``field`` it
    sits on (``""``: above the form), and an optional ``note`` beneath it."""

    text: str | SafeString
    field: str = ""
    note: str = ""


# --- the choices ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportChoices:
    """The rating and profile selects' ``(id, label)`` options (the blank choice included), and
    each read's failure text ("" when it was read)."""

    ratings: list[tuple[str, str]] = field(default_factory=lambda: [("", RATING_DEFAULT)])
    profiles: list[tuple[str, str]] = field(default_factory=lambda: [("", PROFILE_DEFAULT)])
    ratings_error: str = ""
    profiles_error: str = ""

    def rating_label(self, value: str) -> str:
        return dict(self.ratings).get(value, value)

    def profile_label(self, value: str) -> str:
        return dict(self.profiles).get(value, value)


def rating_options(ratings: Sequence[ContentRatingSummary]) -> list[tuple[str, str]]:
    """The blank choice, then each non-retired rating by name, in the Engine's order."""
    return [("", RATING_DEFAULT), *((r.id, r.name) for r in ratings if not r.retired)]


def profile_options(
    presets: Sequence[EngineProfile], listed: Sequence[EngineProfile]
) -> list[tuple[str, str]]:
    """The blank choice, then the presets (E13's order and label), then the other profiles:
    deleted ones dropped, one entry per id (the preset read's first), a non-preset labelled by
    name, plus "(v<n>)" when two non-presets share it."""
    found: dict[str, EngineProfile] = {}
    for profile in [*presets, *listed]:
        if profile.deleted_at is None and profile.id not in found:
            found[profile.id] = profile
    ordered = preset_order(found.values())
    names = Counter(p.name for p in ordered if p.preset is not True)
    options = [("", PROFILE_DEFAULT)]
    for profile in ordered:
        label = preset_label(profile)
        if profile.preset is not True and names[profile.name] > 1:
            label = f"{profile.name} (v{profile.current_version})"
        options.append((profile.id, label))
    return options


def read_import_choices(client: EngineClient) -> ImportChoices:
    """The three reads (ratings, presets, every profile), each failure degrading its field. A
    503 ``erasure_pending`` is raised for the frame's held fragment (D17)."""
    ratings: list[tuple[str, str]] = [("", RATING_DEFAULT)]
    ratings_error = ""
    try:
        ratings = rating_options(client.list_content_ratings())
    except EngineClientError as exc:
        if is_held(exc):
            raise
        ratings_error = RATINGS_UNREAD
    profiles: list[tuple[str, str]] = [("", PROFILE_DEFAULT)]
    profiles_error = ""
    try:
        presets, _ = client.list_engine_profiles(preset=True)
        listed, _ = client.list_engine_profiles()
    except EngineClientError as exc:
        if is_held(exc):
            raise
        profiles_error = PROFILES_UNREAD
    else:
        profiles = profile_options(presets, listed)
    return ImportChoices(ratings, profiles, ratings_error, profiles_error)


# --- the form -------------------------------------------------------------------------------------


def keep_text(held: Spool) -> str:
    return KEEP_TEXT.format(filename=held.filename, size=filesizeformat(held.size))


class ImportForm(forms.Form):
    """The import's fields (module docstring); ``choices`` from :func:`read_import_choices`,
    ``principal`` the requesting principal (the owner's help)."""

    file = forms.FileField(label="Bundle file", help_text=FILE_HELP)
    owner = forms.CharField(label="Owner", max_length=OWNER_MAX, required=False)
    name = forms.CharField(label="Name", max_length=NAME_MAX, required=False, help_text=NAME_HELP)
    content_rating_id = forms.ChoiceField(label="Content rating", required=False)
    engine_profile_id = forms.ChoiceField(label="Engine profile", required=False)
    upgrade_mode = UpgradeModeField(required=True)
    spool = forms.CharField(required=False, widget=forms.HiddenInput)
    keep_spool = forms.BooleanField(required=False, initial=True)

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
        *,
        choices: ImportChoices,
        principal: str,
    ) -> None:
        super().__init__(data, files)
        self.choices = choices
        self.principal = principal
        self.fields["owner"].help_text = OWNER_HELP.format(principal=principal)
        rating = self.fields["content_rating_id"]
        if isinstance(rating, forms.ChoiceField):
            rating.choices = choices.ratings
            rating.help_text = choices.ratings_error
        profile = self.fields["engine_profile_id"]
        if isinstance(profile, forms.ChoiceField):
            profile.choices = choices.profiles
            profile.help_text = choices.profiles_error
        self.kept: Spool | None = unsign_spool(data.get(SPOOL_FIELD)) if data else None
        if self.kept is not None and self._keep_ticked():
            self.fields[FILE].required = False

    def _keep_ticked(self) -> bool:
        if self.data is None:
            return False
        value = self.data.get(KEEP_FIELD)
        return value not in (None, "", "false", "0", "off")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if (
            not cleaned.get(FILE)
            and FILE not in self.errors
            and self.kept is not None
            and self._keep_ticked()
            and not spool_exists(self.kept.name)
        ):
            self.add_error(FILE, IMPORT_SPOOL_EXPIRED)
        return cleaned

    def held_spool(self) -> Spool | None:
        """The kept spool this POST resubmits (no new file chosen, the box ticked); call after
        ``is_valid()``."""
        if self.cleaned_data.get(FILE) or not self._keep_ticked():
            return None
        return self.kept

    def metadata(self) -> dict[str, Any]:
        """``ImportMetadata``: the blank optional members left out, ``upgrade_mode`` always,
        ``ignore_warnings`` false. Call after ``is_valid()``."""
        data = self.cleaned_data
        found: dict[str, Any] = {}
        for name in OPTIONAL_MEMBERS:
            value = data.get(name)
            if isinstance(value, str) and value.strip():
                found[name] = value.strip()
        found["upgrade_mode"] = data["upgrade_mode"]
        found["ignore_warnings"] = False
        return found


def owner_text(metadata: Mapping[str, Any], principal: str) -> str:
    """The confirm's owner fact: the one named, else the help's default."""
    owner = metadata.get("owner")
    return (
        owner
        if isinstance(owner, str) and owner
        else OWNER_DEFAULT_TEXT.format(principal=principal)
    )


def name_text(metadata: Mapping[str, Any]) -> str:
    name = metadata.get("name")
    return name if isinstance(name, str) and name else NAME_DEFAULT_TEXT


def bound_data(metadata: Mapping[str, Any]) -> dict[str, str]:
    """A signed ``ImportMetadata`` as the form's values (a refusal at the confirm shows the form
    again with them; the file must be chosen again)."""
    return {
        name: str(metadata.get(name) or "")
        for name in METADATA_FIELDS
        if metadata.get(name) is not None
    }


# --- refusals -------------------------------------------------------------------------------------


def _settings_url() -> str | None:
    try:
        return reverse("engine:settings") + SETTINGS_ANCHOR
    except NoReverseMatch:
        return None


def limit_value_text(limit: str, value: object) -> str:
    """``details.value`` by its limit: a size for a ``*_bytes`` limit, ``{:,}`` for
    ``bundle_max_entries``, as given otherwise."""
    if isinstance(value, int) and not isinstance(value, bool):
        if limit.endswith("_bytes"):
            return str(filesizeformat(value))
        if limit == "bundle_max_entries":
            return f"{value:,}"
    return str(value)


def too_large_text(details: Mapping[str, Any]) -> SafeString:
    found = details.get("limit")
    limit = found if isinstance(found, str) and found else DEFAULT_LIMIT
    value = details.get("value")
    shown_value = f" ({limit_value_text(limit, value)})" if value is not None else ""
    url = _settings_url() if limit in BUNDLE_LIMITS else None
    if url is not None:
        shown = format_html('<a class="settings-link" href="{}"><code>{}</code></a>', url, limit)
    else:
        shown = format_html("<code>{}</code>", limit)
    before, _, after = TOO_LARGE_TEXT.partition("{limit}")
    return format_html("{}{}{}", before, shown, after.format(value=shown_value))


def corrupt_reason_text(reason: object) -> str:
    if not isinstance(reason, str) or not reason:
        return NO_REASON_TEXT
    return CORRUPT_REASON_TEXT.get(reason, humanised(reason).lower())


def corrupt_text(details: Mapping[str, Any]) -> SafeString:
    entry = details.get("entry")
    shown_entry = (
        format_html(" (<code>{}</code>)", entry) if isinstance(entry, str) and entry else ""
    )
    before, _, after = CORRUPT_TEXT.partition("{reason}")
    tail = after.replace("{entry}", "")
    return format_html(
        "{}{}{}{}", before, corrupt_reason_text(details.get("reason")), shown_entry, tail
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def unsupported_text(details: Mapping[str, Any]) -> str:
    found = _text(details.get("bundle_version")) or _text(details.get("format")) or UNKNOWN_FORMAT
    supported = details.get("supported")
    if isinstance(supported, list) and supported:
        shown = ", ".join(str(s) for s in supported)
    else:
        shown = _text(supported) or SUPPORTED_DEFAULT
    return UNSUPPORTED_TEXT.format(found=found, supported=shown)


def rating_refusal(details: Mapping[str, Any]) -> FormRefusal:
    name = _text(details.get("bundle_rating_name"))
    candidates = details.get("candidates")
    listed = [str(c) for c in candidates] if isinstance(candidates, list) else []
    if name is None:
        text = RATING_UNNAMED
    elif listed:
        text = RATING_SEVERAL.format(name=name)
    else:
        text = RATING_NO_MATCH.format(name=name)
    note = CLOSEST_TEXT.format(", ".join(listed)) if listed else ""
    return FormRefusal(text, "content_rating_id", note)


def metadata_member(path: str) -> str:
    """``metadata.<member>`` / ``body.metadata.<member>`` → ``<member>`` (its first segment);
    anything else → ""."""
    for prefix in METADATA_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :].split(".", 1)[0].split("[", 1)[0]
    return ""


def validation_refusal(exc: EngineError) -> FormRefusal:
    failures = engine_field_errors(exc)
    first = failures[0] if failures else None
    message = (first.message if first is not None else "") or exc.message
    member = metadata_member(first.path) if first is not None else ""
    placed = member if member in METADATA_FIELDS else ""
    return FormRefusal(message + NOTHING_CREATED, placed)


def import_refusal(exc: EngineClientError) -> FormRefusal | None:
    """The import's refusals in plain words, on their field; ``None`` for anything else."""
    if not isinstance(exc, EngineError):
        return None
    details = exc.details
    if exc.code == BUNDLE_TOO_LARGE:
        return FormRefusal(too_large_text(details), FILE)
    if exc.code == CORRUPT_BUNDLE:
        return FormRefusal(corrupt_text(details), FILE)
    if exc.code == UNSUPPORTED_FORMAT:
        return FormRefusal(unsupported_text(details), FILE)
    if exc.code == RATING_REQUIRED:
        return rating_refusal(details)
    if exc.code == PROFILE_DELETED:
        return FormRefusal(PROFILE_DELETED_TEXT, "engine_profile_id")
    if exc.code == VALIDATION_ERROR:
        return validation_refusal(exc)
    return None
