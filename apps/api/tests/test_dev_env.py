import importlib
import os
from pathlib import Path

import pytest

import dev
from dev import load_local_env


def test_load_local_env_loads_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEV_ENV_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text("DEV_ENV_TEST_KEY=loaded-value\n")

    existed = load_local_env(tmp_path)

    assert existed is True
    assert os.environ["DEV_ENV_TEST_KEY"] == "loaded-value"


def test_load_local_env_tolerates_missing_file(
    tmp_path: Path,
) -> None:
    existed = load_local_env(tmp_path)

    assert existed is False


def test_load_local_env_does_not_override_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEV_ENV_TEST_KEY", "already-set")
    (tmp_path / ".env").write_text("DEV_ENV_TEST_KEY=from-file\n")

    existed = load_local_env(tmp_path)

    assert existed is True
    assert os.environ["DEV_ENV_TEST_KEY"] == "already-set"


def test_importing_dev_does_not_load_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing ``dev`` must NOT load any ``.env`` (hermeticity contract).

    Locks the invariant that ``load_local_env(REPO_ROOT)`` stays behind the
    ``__main__`` guard: if a regression moved it to module scope, reimporting
    ``dev`` would call ``load_dotenv`` and this spy would fire.
    """
    calls: list[object] = []

    def spy(*args: object, **kwargs: object) -> bool:
        calls.append((args, kwargs))
        return False

    monkeypatch.setattr("dotenv.load_dotenv", spy)
    try:
        importlib.reload(dev)
        assert calls == []
    finally:
        # Restore the real ``load_dotenv`` reference inside ``dev`` for other tests.
        monkeypatch.undo()
        importlib.reload(dev)
