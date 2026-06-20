import importlib

import pytest

import dev


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
