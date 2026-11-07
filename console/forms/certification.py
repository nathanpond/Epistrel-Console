"""The certification-run request form (#314, FR-PROF-17, NFR-QUAL-8).

:class:`CertifyForm` backs both "Request a certification run…" pages (``profiles:certify`` and
``stories:certify``, ``console/views/certify.py``):

- ``suites``: free text, split on commas and whitespace, empties and duplicates dropped (order
  and case kept). Blank means every suite the configuration enables; the estimate lists them.
  No client-side cap: the Engine's 422 lands on the field.
- ``seeds``: a whole number from 1 to 5, initial 3.
- ``sample``: optional, a whole number of at least 10 (a per-suite case cap).
- ``version`` (profile only): "Latest (v<n>)" (the empty value, never sent) and each version,
  newest first, as a ``TypedChoiceField`` over the versions read (``latest`` names the profile's
  ``current_version`` in the Latest label); ``versions=None`` drops the field (the story form).

Numbers are ``NumberInput(step=1)`` with no HTML ``min``/``max`` (C10). :meth:`request_kwargs`
returns only the members that are set, as the client's keyword arguments.
"""

import re
from collections.abc import Mapping
from typing import Any

from django import forms

from console.engine.client import EngineError
from console.engine.models import ProfileVersion
from console.engine_form_errors import engine_field_errors

SEEDS_MIN, SEEDS_MAX, SEEDS_INITIAL = 1, 5, 3
SAMPLE_MIN = 10
SEEDS_INVALID = "Enter a whole number from 1 to 5"
SAMPLE_INVALID = "Enter a whole number of at least 10, or leave it blank for every case"
SUITES_HELP = (
    "Comma- or space-separated suite names; leave it blank for every suite the configuration "
    "enables — the estimate lists them"
)
SEEDS_HELP = "How many times each case is played (1 to 5)"
SAMPLE_HELP = (
    "The most cases played per suite; blank plays every case. Under 300 cases a zero-tolerance "
    "floor can only read insufficient evidence (NFR-QUAL-8)"
)
VERSION_HELP = "Latest certifies whatever version is newest when the run is requested"
LATEST = ""
FIELDS = ("suites", "seeds", "sample", "version")
_SPLIT = re.compile(r"[,\s]+")


def parse_suites(value: object) -> list[str]:
    """The suite names in ``value``: split on commas and whitespace, empties and duplicates
    dropped, order and case kept."""
    text = value if isinstance(value, str) else ""
    return list(dict.fromkeys(name for name in _SPLIT.split(text) if name))


def latest_label(versions: list[ProfileVersion], latest: int | None = None) -> str:
    """ "Latest (v<n>)": ``latest`` (the profile's ``current_version``), else the newest version
    listed; "Latest" with neither."""
    newest = latest if latest is not None else max((v.version for v in versions), default=None)
    return f"Latest (v{newest})" if newest is not None else "Latest"


def version_choices(
    versions: list[ProfileVersion], latest: int | None = None
) -> list[tuple[str, str]]:
    """ "Latest (v<n>)" then every version newest first."""
    ordered = sorted({v.version for v in versions}, reverse=True)
    return [(LATEST, latest_label(versions, latest)), *((str(n), f"v{n}") for n in ordered)]


def _whole(value: object, message: str) -> int:
    text = str(value).strip() if value is not None else ""
    if not re.fullmatch(r"[0-9]+", text):
        raise forms.ValidationError(message)
    return int(text)


class CertifyForm(forms.Form):
    suites = forms.CharField(
        required=False,
        label="Suites",
        help_text=SUITES_HELP,
        widget=forms.TextInput(attrs={"size": 50}),
    )
    seeds = forms.CharField(
        required=False,
        label="Seeds",
        help_text=SEEDS_HELP,
        initial=SEEDS_INITIAL,
        widget=forms.NumberInput(attrs={"step": 1}),
    )
    sample = forms.CharField(
        required=False,
        label="Sample",
        help_text=SAMPLE_HELP,
        widget=forms.NumberInput(attrs={"step": 1}),
    )

    def __init__(
        self,
        data: Mapping[str, Any] | None = None,
        *,
        versions: list[ProfileVersion] | None,
        latest: int | None = None,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(data, initial=dict(initial) if initial else None)
        if versions is not None:
            self.fields["version"] = forms.TypedChoiceField(
                required=False,
                label="Version",
                help_text=VERSION_HELP,
                choices=version_choices(versions, latest),
                coerce=int,
                empty_value=None,
            )

    def clean_suites(self) -> list[str]:
        return parse_suites(self.cleaned_data.get("suites"))

    def clean_seeds(self) -> int:
        return _seeds(self.cleaned_data.get("seeds"))

    def clean_sample(self) -> int | None:
        raw = self.cleaned_data.get("sample")
        if raw is None or str(raw).strip() == "":
            return None
        sample = _whole(raw, SAMPLE_INVALID)
        if sample < SAMPLE_MIN:
            raise forms.ValidationError(SAMPLE_INVALID)
        return sample

    def request_kwargs(self) -> dict[str, Any]:
        """The client's keyword arguments for a valid form: only the set members (a blank
        ``suites`` or ``sample`` and Latest's ``version`` are left out)."""
        data = self.cleaned_data
        kwargs: dict[str, Any] = {"seeds": data["seeds"]}
        if data.get("suites"):
            kwargs["suites"] = data["suites"]
        if data.get("sample") is not None:
            kwargs["sample"] = data["sample"]
        if data.get("version") is not None:
            kwargs["version"] = data["version"]
        return kwargs

    def apply_engine_error(self, exc: EngineError) -> int:
        """E21: each ``validation_error`` failure at ``body.<field>`` (``body.suites.0`` too) on
        that field; returns how many failures could not be placed."""
        failures = engine_field_errors(exc)
        unplaced = 0
        for failure in failures:
            name = loc_field(failure.path)
            if name in FIELDS and name in self.fields:
                self.add_error(name, failure.message or exc.message)
            else:
                unplaced += 1
        return unplaced if failures else 1


def _seeds(raw: object) -> int:
    if raw is None or str(raw).strip() == "":
        raise forms.ValidationError(SEEDS_INVALID)
    seeds = _whole(raw, SEEDS_INVALID)
    if not SEEDS_MIN <= seeds <= SEEDS_MAX:
        raise forms.ValidationError(SEEDS_INVALID)
    return seeds


def loc_field(path: str) -> str:
    """E21: a ``body.<field>`` (or ``body.<field>.0`` / ``<field>[0]``) loc's field name; a loc
    outside the body keeps its prefix, so it never matches a field."""
    if not path.startswith("body."):
        return path
    return re.split(r"[.\[]", path.removeprefix("body."), maxsplit=1)[0]


def bound_data(body: Mapping[str, Any]) -> dict[str, str]:
    """A signed request body as the form's data (the pinned suites joined with ", "), so a
    confirm-stage refusal re-renders the form as it was sent."""
    data = {"seeds": str(body.get("seeds", "")), "sample": ""}
    suites = body.get("suites")
    data["suites"] = ", ".join(str(s) for s in suites) if isinstance(suites, list) else ""
    if body.get("sample") is not None:
        data["sample"] = str(body["sample"])
    data["version"] = str(body["version"]) if body.get("version") is not None else LATEST
    return data
