from django.contrib.staticfiles.management.commands.runserver import (
    Command as StaticfilesRunserver,
)

from console.management.commands.runserver import Command


def test_runserver_defaults_to_console_port() -> None:
    assert Command.default_port == "7739"
    assert Command.default_addr == "127.0.0.1"
    assert Command.default_addr_ipv6 == "::1"


def test_runserver_keeps_dev_static_serving() -> None:
    assert issubclass(Command, StaticfilesRunserver)
