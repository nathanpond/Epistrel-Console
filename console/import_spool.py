"""The bundle import's spool (#320, E8's "Import's file"): where the Console holds an uploaded
bundle between the import's first stage and its confirm.

An upload can't travel in a confirm token, so the first stage copies it to
``settings.DATA_DIR / "imports" / "<ULID>.epistrel.tgz"`` (:func:`spool`) and signs only the
spool's name (:func:`sign_spool`, salt :data:`SALT`, read back by :func:`unsign_spool` within
``twostage.MAX_AGE_S``). The confirm, or a resubmit after a refusal a metadata change can fix
(``rating_required``, ``validation_error``, ``profile_deleted``), reopens it (:func:`open_spool`)
and the view deletes it (:func:`delete_spool`) once the Engine answered. Every import-page GET
sweeps the files older than ``twostage.MAX_AGE_S`` (:func:`sweep_spools`), so an abandoned
confirm's file goes within the hour after its token stops working.

- **Names.** A spool's name is always a fresh ULID plus :data:`SUFFIX`, never the uploaded
  name, so a ``../evil.tgz`` upload stays inside the directory. :func:`spool_path` accepts only
  a name of that shape and answers ``None`` for anything else (a signed name that names a path
  outside the directory opens nothing).
- **Writing.** :func:`spool` copies ``upload.chunks()`` into a temporary name in the same
  directory, then ``os.replace``s it to the ULID name, so a half-written spool is never opened.
  The directory is created ``parents=True, exist_ok=True`` at 0o700 and each file is 0o600. An
  ``OSError`` is the caller's (the view shows "The Console couldn't hold the upload").
- **Sweeping** matches only ``*.epistrel.tgz`` and stray temporary names directly in the
  directory, by mtime, and ignores a file that vanishes mid-sweep.

No local size check: the compressed upload's size says little about its uncompressed size, and
the Engine's 413 ``bundle_too_large`` is authoritative.
"""

import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any

from django.conf import settings
from django.core import signing

from console import twostage
from console.ids import new_ulid

log = logging.getLogger("console.import_spool")

DIRECTORY = "imports"
SUFFIX = ".epistrel.tgz"
TEMP_PREFIX = ".spool-"
SALT = "console.import_spool"
DIR_MODE = 0o700
FILE_MODE = 0o600
IMPORT_SPOOL_EXPIRED = "The uploaded bundle is no longer held — choose the file again"
OCTET_STREAM = "application/octet-stream"
DEFAULT_FILENAME = "bundle.epistrel.tgz"

_NAME = re.compile(r"^[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{26}" + re.escape(SUFFIX) + r"$")


@dataclass(frozen=True)
class Spool:
    """One held upload: the spool's file ``name`` (``<ULID>.epistrel.tgz``) and the upload's
    ``filename`` (its base name, as the import sends it), ``size`` and ``content_type``."""

    name: str
    filename: str
    size: int
    content_type: str

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def spool_dir() -> Path:
    """``settings.DATA_DIR / "imports"`` (read at call time, so tests can override it)."""
    return Path(settings.DATA_DIR) / DIRECTORY


def upload_filename(name: object) -> str:
    """The name the import sends: the upload's base name, else :data:`DEFAULT_FILENAME`."""
    base = os.path.basename(name) if isinstance(name, str) else ""
    return base or DEFAULT_FILENAME


def spool_path(name: object) -> Path | None:
    """The spool file ``name`` names, or ``None`` when ``name`` isn't a spool name (a ULID plus
    :data:`SUFFIX`, nothing else: no separator, no ``..``)."""
    if not isinstance(name, str) or not _NAME.match(name):
        return None
    return spool_dir() / name


def _ensure_dir() -> Path:
    directory = spool_dir()
    directory.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    return directory


def spool(upload: Any) -> Spool:
    """Copy ``upload`` (a Django ``UploadedFile``) to a new spool file and describe it. Raises
    ``OSError`` when the directory or the file can't be written (nothing is left behind)."""
    directory = _ensure_dir()
    name = f"{new_ulid()}{SUFFIX}"
    temp = directory / f"{TEMP_PREFIX}{name}"
    size = 0
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        with os.fdopen(fd, "wb") as out:
            if hasattr(upload, "seek"):
                upload.seek(0)
            for chunk in upload.chunks():
                out.write(chunk)
                size += len(chunk)
        os.replace(temp, directory / name)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    content_type = getattr(upload, "content_type", None) or OCTET_STREAM
    return Spool(name, upload_filename(getattr(upload, "name", None)), size, str(content_type))


def open_spool(name: object) -> IO[bytes] | None:
    """The spool file opened for reading, or ``None`` when ``name`` isn't a spool name or the
    file is gone (swept, or already used)."""
    path = spool_path(name)
    if path is None:
        return None
    try:
        return path.open("rb")
    except OSError:
        return None


def spool_exists(name: object) -> bool:
    path = spool_path(name)
    return path is not None and path.is_file()


def delete_spool(name: object) -> None:
    """Remove the spool file ``name`` names (nothing for a non-spool name or a missing file)."""
    path = spool_path(name)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("couldn't delete the import spool %s", name, exc_info=True)


def _sweepable(name: str) -> bool:
    return name.endswith(SUFFIX) or name.startswith(TEMP_PREFIX)


def sweep_spools(*, max_age: int = twostage.MAX_AGE_S, now: float | None = None) -> int:
    """Delete the spool files (and stray temporary names) directly in the directory whose mtime
    is more than ``max_age`` seconds old; returns how many went. A missing directory, or a file
    that vanishes mid-sweep, is not an error."""
    directory = spool_dir()
    cutoff = (time.time() if now is None else now) - max_age
    removed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0
    for entry in entries:
        if not _sweepable(entry.name):
            continue
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            log.warning("couldn't sweep the import spool %s", entry.name, exc_info=True)
    return removed


# --- the signed spool field -----------------------------------------------------------------------


def sign_spool(held: Spool) -> str:
    """The hidden ``spool`` field's value: the spool's description, signed."""
    return signing.dumps(held.as_json(), salt=SALT)


def spool_from_json(raw: object) -> Spool | None:
    """A :class:`Spool` from its JSON (a token's ``base``, or the signed field), or ``None``
    when it isn't one or names no spool file shape."""
    if not isinstance(raw, dict):
        return None
    name, filename = raw.get("name"), raw.get("filename")
    size, content_type = raw.get("size"), raw.get("content_type")
    if (
        spool_path(name) is None
        or not isinstance(filename, str)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not isinstance(content_type, str)
    ):
        return None
    return Spool(str(name), filename or DEFAULT_FILENAME, size, content_type or OCTET_STREAM)


def unsign_spool(value: object, *, max_age: int = twostage.MAX_AGE_S) -> Spool | None:
    """The spool a signed field names, or ``None`` when it is blank, tampered with, older than
    ``max_age`` or names a path outside the spool directory."""
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = signing.loads(value, salt=SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    return spool_from_json(raw)
