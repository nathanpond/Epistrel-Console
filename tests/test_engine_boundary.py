"""Only console/engine/client.py may talk HTTP: everything else goes through the Engine client."""

import ast
from pathlib import Path

from tests.helpers import REPO

ALLOWED = REPO / "console" / "engine" / "client.py"
SCANNED = ("console", "epistrel_console")
BANNED = ("httpx", "requests", "urllib.request", "urllib3", "http.client", "aiohttp")


def _banned(name: str) -> bool:
    return any(name == b or name.startswith(f"{b}.") for b in BANNED)


def http_imports(source: str) -> list[str]:
    """Every banned module ``source`` imports (``TYPE_CHECKING`` blocks included)."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if _banned(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _banned(node.module):
                found.append(node.module)
                continue
            found.extend(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if _banned(f"{node.module}.{alias.name}")
            )
    return found


def offenders(root: Path = REPO) -> list[str]:
    problems = []
    for package in SCANNED:
        for path in sorted((root / package).rglob("*.py")):
            if path.resolve() == ALLOWED.resolve():
                continue
            for name in http_imports(path.read_text(encoding="utf-8")):
                problems.append(f"{path.relative_to(root)} imports {name}")
    return problems


def test_only_the_client_imports_http_libraries() -> None:
    assert offenders() == []


def test_detects_each_import_form() -> None:
    source = "\n".join(
        [
            "import httpx",
            "import requests.adapters",
            "from urllib import request",
            "from urllib.request import urlopen",
            "import urllib3",
            "from http import client",
            "import aiohttp as a",
            "from typing import TYPE_CHECKING",
            "if TYPE_CHECKING:",
            "    import httpx as hx",
            "from urllib.parse import urlsplit",
            "import http.cookies",
        ]
    )
    assert http_imports(source) == [
        "httpx",
        "requests.adapters",
        "urllib.request",
        "urllib.request",
        "urllib3",
        "http.client",
        "aiohttp",
        "httpx",
    ]


def test_a_new_module_importing_httpx_fails(tmp_path: Path) -> None:
    for package in SCANNED:
        (tmp_path / package).mkdir()
    (tmp_path / "console" / "sneaky.py").write_text("import httpx\n")
    assert offenders(tmp_path) == ["console/sneaky.py imports httpx"]
