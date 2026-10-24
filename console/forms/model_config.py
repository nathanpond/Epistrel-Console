"""Model configuration forms (#46): ``PUT /admin/model-config/{purpose}`` and its api-key. They
copy only the bounds the recorded schema states (#43)."""

from typing import Any

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from epistrel_console.engine_config import parse_url

# ConfigReplacement / ModelParams / ApiKeyBody bounds, as the recorded schema states them.
BASE_URL_MAX = 2048
MODEL_MAX = 200
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 2.0
TOP_P_MIN, TOP_P_MAX = 0.0, 1.0
MAX_TOKENS_MIN, MAX_TOKENS_MAX = 1, 4000
TIMEOUT_MAX = 600.0  # and above 0 (exclusive)
STOP_MAX = 4
SEED_MIN, SEED_MAX = 0, 2_147_483_647
API_KEY_MAX = 512


def _http_url(value: str) -> None:
    """An ``http(s)://`` URL with a host (#29's parser), so single-label hosts work."""
    _, reason = parse_url(value)
    if reason:
        raise forms.ValidationError(f"Enter a URL that {reason}.")


def _above_zero(value: float) -> None:
    if value <= 0:
        raise forms.ValidationError("Ensure this value is greater than 0.")


class ModelConfigForm(forms.Form):
    """A purpose's own ``base_url``, ``model`` and params. Blank inherits (``null`` on
    ``default``). Each field's placeholder shows what a blank field inherits."""

    base_url = forms.CharField(
        label="Base URL", max_length=BASE_URL_MAX, required=False, validators=[_http_url]
    )
    model = forms.CharField(label="Model", max_length=MODEL_MAX, required=False)
    temperature = forms.FloatField(
        label="temperature",
        required=False,
        validators=[MinValueValidator(TEMPERATURE_MIN), MaxValueValidator(TEMPERATURE_MAX)],
    )
    top_p = forms.FloatField(
        label="top_p",
        required=False,
        validators=[MinValueValidator(TOP_P_MIN), MaxValueValidator(TOP_P_MAX)],
    )
    max_tokens = forms.IntegerField(
        label="max_tokens",
        required=False,
        validators=[MinValueValidator(MAX_TOKENS_MIN), MaxValueValidator(MAX_TOKENS_MAX)],
    )
    timeout = forms.FloatField(
        label="timeout (seconds)",
        required=False,
        validators=[_above_zero, MaxValueValidator(TIMEOUT_MAX)],
    )
    stop = forms.CharField(
        label="stop",
        required=False,
        widget=forms.Textarea(attrs={"rows": STOP_MAX}),
        help_text="One sequence per line.",
    )
    seed = forms.IntegerField(
        label="seed",
        required=False,
        validators=[MinValueValidator(SEED_MIN), MaxValueValidator(SEED_MAX)],
    )

    PARAM_FIELDS = ("temperature", "top_p", "max_tokens", "timeout", "stop", "seed")

    def __init__(
        self, *args: Any, placeholders: dict[str, str] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        for name, text in (placeholders or {}).items():
            self.fields[name].widget.attrs["placeholder"] = text

    def clean_stop(self) -> list[str] | None:
        """One sequence per line: lines trimmed, blank lines dropped; none → ``None``."""
        text: str = self.cleaned_data["stop"] or ""
        lines = [line.strip() for line in text.splitlines()]
        kept = [line for line in lines if line]
        if len(kept) > STOP_MAX:
            raise forms.ValidationError(f"At most {STOP_MAX} stop sequences.")
        return kept or None

    def replacement(self, *, is_default: bool) -> dict[str, Any]:
        """The ``ConfigReplacement`` body. A blank field is absent (it inherits), or ``null``
        on ``default`` (unset); params go as one object, only when one is given (always on
        ``default``)."""
        data = self.cleaned_data
        body: dict[str, Any] = {}
        for name in ("base_url", "model"):
            value = data[name] or None
            if value is not None or is_default:
                body[name] = value
        params = {
            name: data[name] for name in self.PARAM_FIELDS if data[name] is not None or is_default
        }
        if params:
            body["params"] = params
        return body


class ApiKeyForm(forms.Form):
    """The key for ``PUT …/api-key``. Never rendered back (``render_value=False``)."""

    api_key = forms.CharField(
        label="API key",
        max_length=API_KEY_MAX,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}, render_value=False),
    )
