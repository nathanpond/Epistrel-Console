"""Who may sign in: operators, i.e. active superusers (created with ``createsuperuser``)."""

from typing import Any

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from console.operators import is_operator

NOT_AN_OPERATOR = "This account is not an operator."


class OperatorAuthenticationForm(AuthenticationForm):
    """Django's login form, refusing every account that is not an operator (not logged in)."""

    def confirm_login_allowed(self, user: Any) -> None:
        super().confirm_login_allowed(user)  # inactive accounts keep Django's own message
        if not is_operator(user):
            raise forms.ValidationError(NOT_AN_OPERATOR, code="not_operator")
