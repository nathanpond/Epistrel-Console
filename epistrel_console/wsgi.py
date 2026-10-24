"""
WSGI config for epistrel_console project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from epistrel_console import engine_config

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "epistrel_console.settings")

# No entry point serves without a valid Engine URL and service key; gunicorn --preload makes this
# stop the master.
engine_config.require()

application = get_wsgi_application()
