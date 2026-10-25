"""Who counts as an operator: an authenticated, active superuser (``createsuperuser``, #43).

No model imports, so any module may use it before the app registry is ready.
"""

from typing import Any


def is_operator(user: Any) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_superuser", False)
    )
