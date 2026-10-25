"""``django.contrib.admin`` with the operator-only site (``console.admin_site``).

Kept out of ``console/apps.py``: Django picks an app's config from the AppConfig subclasses in
that module, and an imported ``AdminConfig`` (``default = True``) would win there.
"""

from django.contrib.admin.apps import AdminConfig


class OperatorAdminConfig(AdminConfig):
    default_site = "console.admin_site.OperatorAdminSite"
