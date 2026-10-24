"""URL configuration for the Console (layout: #43's engine-wide conventions)."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from console.auth import OperatorAuthenticationForm
from console.views import status
from console.views.placeholders import placeholder

urlpatterns = [
    path("", status.status, name="status"),
    path("health", status.health, name="health"),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(authentication_form=OperatorAuthenticationForm),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Placeholders: each owning M2 story replaces its own.
    path("stories/", placeholder("Stories"), name="stories"),
    path("operations/", placeholder("Operations"), name="operations"),
    path("models/", placeholder("Models"), name="models"),
    path("guardrails/ratings/", placeholder("Guardrail ratings"), name="guardrail-ratings"),
    path("engine/", placeholder("Engine"), name="engine"),
    path("admin/", admin.site.urls),
]
