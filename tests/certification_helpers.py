"""D19 stand-in for the certification pages before #313 registered them: E6's
``console.certification`` URL helpers read as unregistered (``None``), so a test can still check
the plain-text case of a link #313's real routes now render."""

from typing import Any

import pytest
from django.urls import NoReverseMatch
from django.urls import reverse as real_reverse

from console import certification

CERTIFICATION_PAGES = frozenset(
    {"engine:certifications", "engine:certification", "engine:certification-run"}
)


def unregistered_certification_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """``console.certification``'s ``reverse`` raises ``NoReverseMatch`` for #313's names."""

    def reverse(name: str, *args: Any, **kwargs: Any) -> str:
        if name in CERTIFICATION_PAGES:
            raise NoReverseMatch(name)
        return real_reverse(name, *args, **kwargs)

    monkeypatch.setattr(certification, "reverse", reverse)
