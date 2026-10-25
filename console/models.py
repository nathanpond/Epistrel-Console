"""The Console's own records (#44, #64).

The Engine M2 has no operations-list, event-log or guardrail-log reads, so the Console keeps its
own log of every mutating Engine call it made (owner decision). This is the "saved debug
session" data CLAUDE.md allows in the Console's database; story data itself is never stored.

#64 adds the operator's acting principal (:class:`ActingPrincipal`) and the principals the
operator has typed (:class:`KnownPrincipal`): Console preferences, never Engine data.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class EngineOperation(models.Model):
    """One mutating Engine call made through ``engine_client_for(request)``, as observed.

    ``request`` and ``response`` are stored redacted (``console.redaction``); the service key and
    the principal header are never captured (the principal has its own column). An absent
    string value (``story_id``, ``engine_operation_id``, ``idempotency_key``, ``error_code``) is
    ``""``, Django's convention for text columns; absent numbers and JSON are ``NULL``.
    """

    class Outcome(models.TextChoices):
        OK = "ok", "ok"
        QUEUED = "queued", "queued"
        ERROR = "error", "error"
        UNREACHABLE = "unreachable", "unreachable"

    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="engine_operations",
    )
    # The operator's username when the call was made: kept when the user is deleted.
    operator_username = models.CharField(max_length=150)
    principal = models.CharField(max_length=255)
    story_id = models.CharField(max_length=26, blank=True, db_index=True)
    kind = models.CharField(max_length=64, db_index=True)
    http_method = models.CharField(max_length=8)
    path = models.TextField()
    request = models.JSONField(default=dict)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    engine_operation_id = models.CharField(max_length=26, blank=True, db_index=True)
    timeline_revision = models.BigIntegerField(null=True, blank=True)
    config_revision = models.BigIntegerField(null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, blank=True)
    outcome = models.CharField(max_length=16, choices=Outcome.choices, db_index=True)
    error_code = models.CharField(max_length=64, blank=True)
    response = models.JSONField(null=True, blank=True)
    elapsed_ms = models.PositiveIntegerField(null=True, blank=True)
    replayed = models.BooleanField(default=False)
    is_guardrail_rejection = models.BooleanField(default=False, db_index=True)
    # The entity the call was about (#64): an Engine ``EntityType`` string (``console.entities``)
    # and its id; ``job_id`` is the Engine job a 202 handed the work to (``complete_job``).
    # NULL, not "": rows recorded before #64 have none, and the story asks for nullable columns.
    entity_type = models.CharField(max_length=32, null=True, blank=True)  # noqa: DJ001
    entity_id = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    job_id = models.CharField(max_length=64, null=True, blank=True, db_index=True)  # noqa: DJ001

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "Engine operation"
        indexes = (
            models.Index(fields=["entity_type", "entity_id"], name="console_op_entity_idx"),
            models.Index(fields=["principal"], name="console_op_principal_idx"),
        )

    def __str__(self) -> str:
        return f"{self.kind} {self.outcome} #{self.pk}"


class ActingPrincipal(models.Model):
    """The principal an operator acts as (#64), when it is not their own ``console:<username>``.

    A row exists only while acting as someone else: "Back to me", and selecting yourself, delete
    it. It persists across logout and login (a global setting, not a session value).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="acting_principal",
    )
    principal = models.CharField(max_length=256)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "acting principal"

    def __str__(self) -> str:
        return f"{self.user} acting as {self.principal}"


class KnownPrincipal(models.Model):
    """A principal the operator has selected or typed into a valid form (#64): the choices the
    acting selector and every ``PrincipalField`` datalist offer. Starts empty; rows are removed
    only in Django admin. Principals read from Engine responses are never added."""

    principal = models.CharField(max_length=256, unique=True)
    added_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-last_used_at", "principal")
        indexes = (
            models.Index(fields=["-last_used_at", "principal"], name="console_known_recent_idx"),
        )

    def __str__(self) -> str:
        return self.principal
