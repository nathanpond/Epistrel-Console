"""#320: the bundle import's spool (E8): ULID names inside ``DATA_DIR/imports``, the sweep, and
the signed spool field."""

import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile

from console import twostage
from console.ids import is_ulid
from console.import_spool import (
    SALT,
    SUFFIX,
    Spool,
    delete_spool,
    open_spool,
    sign_spool,
    spool,
    spool_path,
    sweep_spools,
    unsign_spool,
)

BYTES = b"\x1f\x8b not really a bundle"


@pytest.fixture
def data_dir(settings: Any, tmp_path: Path) -> Path:
    settings.DATA_DIR = tmp_path
    return tmp_path / "imports"


def upload(name: str = "harbour.epistrel.tgz", content: bytes = BYTES) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content, content_type="application/gzip")


def test_spool_writes_a_ulid_name_under_data_dir(data_dir: Path) -> None:
    held = spool(upload())
    assert held.name.endswith(SUFFIX)
    assert is_ulid(held.name.removesuffix(SUFFIX))
    assert (held.filename, held.size, held.content_type) == (
        "harbour.epistrel.tgz",
        len(BYTES),
        "application/gzip",
    )
    assert os.listdir(data_dir) == [held.name]  # no temporary name left behind
    assert (data_dir / held.name).read_bytes() == BYTES
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((data_dir / held.name).stat().st_mode) == 0o600


def test_an_upload_named_outside_stays_inside(data_dir: Path) -> None:
    held = spool(upload("../evil.tgz"))
    assert held.filename == "evil.tgz"
    assert os.listdir(data_dir) == [held.name]
    assert not (data_dir.parent / "evil.tgz").exists()
    assert "evil" not in held.name


class Nameless:
    """An upload with no name and no declared type."""

    name = None
    content_type = None

    def chunks(self) -> list[bytes]:
        return [BYTES]


def test_a_blank_name_and_type_get_defaults(data_dir: Path) -> None:
    held = spool(Nameless())
    assert (held.filename, held.content_type) == ("bundle.epistrel.tgz", "application/octet-stream")
    assert held.size == len(BYTES)


def test_a_failed_write_leaves_nothing(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(src: Any, dst: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError, match="No space"):
        spool(upload())
    assert os.listdir(data_dir) == []


def test_open_and_delete(data_dir: Path) -> None:
    held = spool(upload())
    handle = open_spool(held.name)
    assert handle is not None
    with handle:
        assert handle.read() == BYTES
    delete_spool(held.name)
    assert open_spool(held.name) is None
    delete_spool(held.name)  # a missing file is not an error


def test_sweep_spools_deletes_only_old_ones(data_dir: Path) -> None:
    old, new = spool(upload()), spool(upload())
    stray = data_dir / f".spool-{old.name}"
    stray.write_bytes(b"half")
    other = data_dir / "notes.txt"
    other.write_bytes(b"keep me")
    past = time.time() - twostage.MAX_AGE_S - 1
    for path in (data_dir / old.name, stray, other):
        os.utime(path, (past, past))
    assert sweep_spools() == 2
    assert sorted(os.listdir(data_dir)) == sorted([new.name, "notes.txt"])


def test_sweep_spools_without_a_directory(data_dir: Path) -> None:
    assert sweep_spools() == 0


def test_sweep_ignores_a_file_that_vanishes(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held = spool(upload())
    past = time.time() - twostage.MAX_AGE_S - 1
    os.utime(data_dir / held.name, (past, past))

    def gone(path: Any) -> None:
        raise FileNotFoundError(path)

    monkeypatch.setattr(os, "unlink", gone)
    assert sweep_spools() == 0


def test_the_signed_field_round_trips(data_dir: Path) -> None:
    held = spool(upload())
    assert unsign_spool(sign_spool(held)) == held


def test_a_tampered_signed_name_opens_nothing(data_dir: Path) -> None:
    held = spool(upload())
    token = sign_spool(held)
    assert unsign_spool(token[:-2] + "xx") is None
    assert unsign_spool("") is None
    assert unsign_spool(None) is None


@pytest.mark.parametrize(
    "name", ["../../db.sqlite3", "/etc/passwd", "01JM7SP0000000000000000001.tgz", "x" + SUFFIX]
)
def test_a_signed_name_outside_the_directory_opens_nothing(data_dir: Path, name: str) -> None:
    raw = Spool(name, "a.tgz", 1, "application/gzip").as_json()
    token = signing.dumps(raw, salt=SALT)  # validly signed, but not a spool name
    assert unsign_spool(token) is None
    assert spool_path(name) is None
    assert open_spool(name) is None


def test_an_old_signed_field_is_refused(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    held = spool(upload())
    token = sign_spool(held)
    later = time.time() + twostage.MAX_AGE_S + 5
    monkeypatch.setattr(time, "time", lambda: later)
    assert unsign_spool(token) is None
