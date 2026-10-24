import os
import stat
import time
from pathlib import Path

import pytest

from epistrel_console import secret_key
from epistrel_console.secret_key import DEV_FALLBACK, StartupError, data_dir, resolve


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_env_key_wins_and_writes_no_file(tmp_path: Path) -> None:
    assert resolve("  env-key  ", tmp_path, debug=False) == "env-key"
    assert resolve("env-key", tmp_path, debug=True) == "env-key"
    assert not (tmp_path / "secret_key").exists()


def test_generated_once_then_reused(tmp_path: Path) -> None:
    key = resolve(None, tmp_path, debug=False)
    path = tmp_path / "secret_key"
    assert len(key) == 50
    assert mode(path) == 0o600
    before = path.stat()
    assert resolve("", tmp_path, debug=False) == key
    after = path.stat()
    # Complement: the second start does not rewrite the file.
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_each_dir_gets_its_own_key(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert resolve(None, tmp_path / "a", False) != resolve(None, tmp_path / "b", False)


def test_debug_without_env_key_uses_fallback_and_touches_nothing(tmp_path: Path) -> None:
    assert resolve(None, tmp_path, debug=True) == DEV_FALLBACK
    assert list(tmp_path.iterdir()) == []


def test_existing_world_readable_file_is_used_and_tightened(tmp_path: Path) -> None:
    path = tmp_path / "secret_key"
    path.write_text("k" * 60 + "\n")
    path.chmod(0o644)
    assert resolve(None, tmp_path, debug=False) == "k" * 60
    assert mode(path) == 0o600


def test_short_file_refuses(tmp_path: Path) -> None:
    path = tmp_path / "secret_key"
    path.write_text("x" * 10)
    with pytest.raises(StartupError) as exc:
        resolve(None, tmp_path, debug=False)
    assert exc.value.name == str(path)
    assert "too short" in exc.value.reason


def lose_race(monkeypatch: pytest.MonkeyPatch, path: Path, content: str) -> None:
    """The other process links its key file into place just before this one tries to."""
    real_link = os.link

    def racing_link(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == path:
            path.write_text(content)
            raise FileExistsError(str(path))
        real_link(src, dst)

    monkeypatch.setattr(os, "link", racing_link)


def test_lost_race_rereads_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "secret_key"
    winner = "w" * 50
    lose_race(monkeypatch, path, winner)
    assert resolve(None, tmp_path, debug=False) == winner
    assert path.read_text() == winner
    assert list(tmp_path.iterdir()) == [path]  # the loser's temp file is gone


def test_lost_race_waits_for_a_winner_still_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The winner created the file but has not written the key yet (#95).
    path = tmp_path / "secret_key"
    winner = "w" * 50
    lose_race(monkeypatch, path, "")
    waits: list[float] = []

    def winner_finishes(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            path.write_text(winner + "\n")

    monkeypatch.setattr(time, "sleep", winner_finishes)
    assert resolve(None, tmp_path, debug=False) == winner
    assert len(waits) == 2


def test_lost_race_to_a_short_file_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secret_key"
    lose_race(monkeypatch, path, "x" * 10)
    waits: list[float] = []
    monkeypatch.setattr(time, "sleep", waits.append)
    with pytest.raises(StartupError) as exc:
        resolve(None, tmp_path, debug=False)
    assert "too short" in exc.value.reason
    # Complement: the wait is bounded.
    assert len(waits) == secret_key.RACE_RETRIES
    assert sum(waits) <= 2


def test_generation_leaves_only_the_key_file(tmp_path: Path) -> None:
    resolve(None, tmp_path, debug=False)
    assert [p.name for p in tmp_path.iterdir()] == ["secret_key"]


def test_data_dir_rules(tmp_path: Path) -> None:
    assert data_dir(None, tmp_path) == tmp_path
    assert data_dir("", tmp_path) == tmp_path
    assert data_dir("sub/dir", tmp_path) == tmp_path / "sub/dir"
    assert data_dir("/abs", tmp_path) == Path("/abs")
    assert data_dir("~/x", tmp_path) == Path.home() / "x"


def test_unwritable_data_dir_refuses(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(StartupError) as exc:
            secret_key.ensure_dir(locked)
        assert exc.value.name == "CONSOLE_DATA_DIR"
    finally:
        locked.chmod(0o700)
