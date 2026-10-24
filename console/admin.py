"""The operation log in the admin: read-only (#44)."""

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.http import HttpRequest

from console.models import EngineOperation

# ``ModelAdmin`` is generic only in django-stubs, not at runtime.
if TYPE_CHECKING:
    ModelAdmin = admin.ModelAdmin[EngineOperation]
else:
    ModelAdmin = admin.ModelAdmin


@admin.register(EngineOperation)
class EngineOperationAdmin(ModelAdmin):
    list_display = ("created_at", "operator_username", "kind", "story_id", "outcome", "error_code")
    list_filter = ("outcome", "kind", "is_guardrail_rejection")
    search_fields = ("story_id", "engine_operation_id", "idempotency_key", "operator_username")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        return [f.name for f in EngineOperation._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
