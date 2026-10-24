"""The admin, restricted to operators (superusers), so a staff-only account cannot sign in there
and so bypass the operator login (#43)."""

from django.contrib import admin
from django.http import HttpRequest

from console.auth import OperatorAuthenticationForm
from console.operators import is_operator


class OperatorAdminSite(admin.AdminSite):
    login_form = OperatorAuthenticationForm

    def has_permission(self, request: HttpRequest) -> bool:
        return is_operator(request.user)
