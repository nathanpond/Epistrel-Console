"""runserver on the Console's own port, refusing to start without a valid Engine URL."""

from typing import Any

from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserverCommand,
)

from epistrel_console import engine_config


class Command(StaticfilesRunserverCommand):
    # 7739, not 8000: the Console runs next to the Engine (8000) on one machine.
    default_addr = "127.0.0.1"
    default_addr_ipv6 = "::1"
    default_port = "7739"

    def handle(self, *args: Any, **options: Any) -> None:
        # Before super().handle(), so the autoreloader never starts on a bad configuration.
        engine_config.require()
        super().handle(*args, **options)
