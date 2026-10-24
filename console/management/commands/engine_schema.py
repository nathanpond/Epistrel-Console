"""Record the Engine's OpenAPI document, or compare the live one with the recorded copy.

    manage.py engine_schema --write [--url URL]   fetch <URL>/openapi.json and record it
    manage.py engine_schema --check [--url URL]   compare; print the difference

Exit codes: 0 match (or written), 1 drift, 2 fetch failure, 3 no usable URL or recorded file,
4 an unexpected error (traceback on stderr; never 1, so a crash never reads as drift).
Django's own bad-argument exit is also 2, so callers treat anything but 0/1 as "no verdict".
Never calls ``engine_config.require()``: this is tooling, not a serving entry point.
"""

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser

from console.engine import schema
from console.engine.client import EngineClient, EngineError, EngineUnreachable
from epistrel_console.engine_config import parse_url

FETCH_TIMEOUT_S = 10.0
CRASHED = 4
MATCH = "Engine API matches the recorded schema."


class Command(BaseCommand):
    help = "Record the Engine's OpenAPI document, or check the live one against the recording."

    def add_arguments(self, parser: CommandParser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--write", action="store_true", help="record the live document")
        mode.add_argument("--check", action="store_true", help="compare with the recording")
        parser.add_argument("--url", help="Engine base URL (default: CONSOLE_ENGINE_URL)")
        parser.add_argument("--schema-path", default=None, help=argparse.SUPPRESS)

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            code = self.run(options)
        except Exception:
            # Python's own uncaught-exception exit is 1, which the drift check would read as drift.
            self.stderr.write(traceback.format_exc(), ending="")
            self._error("engine_schema crashed; no verdict")
            code = CRASHED
        sys.exit(code)

    def _error(self, message: str) -> None:
        self.stderr.write(f"error: {message}")

    def run(self, options: dict[str, Any]) -> int:
        path = Path(options["schema_path"]) if options["schema_path"] else schema.RECORDED_PATH
        raw_url = options["url"] if options["url"] is not None else settings.ENGINE_URL
        if raw_url is None:
            self._error("no Engine URL: pass --url or set CONSOLE_ENGINE_URL")
            return 3
        url, reason = parse_url(raw_url)
        if url is None:
            self._error(f"--url {raw_url!r} {reason}")
            return 3
        recorded: dict[str, Any] | None = None
        if options["check"]:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._error(f"recorded schema {path}: {exc}")
                return 3
            if not isinstance(loaded, dict):
                self._error(f"recorded schema {path}: not a JSON object")
                return 3
            recorded = loaded
        try:
            live = EngineClient(url, None).openapi(timeout=FETCH_TIMEOUT_S)
        except EngineUnreachable as exc:
            self._error(f"could not fetch {url}/openapi.json: {exc.reason}")
            return 2
        except EngineError as exc:
            why = "bad response" if exc.code == "bad_response" else f"HTTP {exc.http_status}"
            self._error(f"could not fetch {url}/openapi.json: {why}")
            return 2
        if recorded is None:
            _write_atomic(path, schema.dumps(live))
            ops, comps = len(schema.operations(live)), len(schema.components(live))
            self.stdout.write(f"Recorded {ops} operations, {comps} components to {path}")
            return 0
        difference = schema.diff(recorded, live)
        if not difference:
            self.stdout.write(MATCH)
            return 0
        self.stdout.write(difference.render(), ending="")
        return 1


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
