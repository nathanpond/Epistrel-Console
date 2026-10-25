"""The operation log in the admin, read-only (#44); the acting principals and known principals
(#64)."""

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.http import HttpRequest

from console.models import ActingPrincipal, EngineOperation, KnownPrincipal

# ``ModelAdmin`` is generic only in django-stubs, not at runtime.
if TYPE_CHECKING:
    ModelAdmin = admin.ModelAdmin[EngineOperation]
    ActingAdminBase = admin.ModelAdmin[ActingPrincipal]
    KnownAdminBase = admin.ModelAdmin[KnownPrincipal]
else:
    ModelAdmin = ActingAdminBase = KnownAdminBase = admin.ModelAdmin


@admin.register(EngineOperation)
class EngineOperationAdmin(ModelAdmin):
    list_display = (
        "created_at",
        "operator_username",
        "principal",
        "kind",
        "entity_type",
        "entity_id",
        "outcome",
        "error_code",
    )
    list_filter = ("outcome", "kind", "entity_type", "is_guardrail_rejection")
    search_fields = (
        "story_id",
        "entity_id",
        "job_id",
        "engine_operation_id",
        "idempotency_key",
        "operator_username",
        "principal",
    )

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        return [f.name for f in EngineOperation._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(ActingPrincipal)
class ActingPrincipalAdmin(ActingAdminBase):
    list_display = ("user", "principal", "updated_at")
    readonly_fields = ("updated_at",)


@admin.register(KnownPrincipal)
class KnownPrincipalAdmin(KnownAdminBase):
    list_display = ("principal", "added_at", "last_used_at")
    search_fields = ("principal",)
    readonly_fields = ("added_at", "last_used_at")
