"""Local dev launcher for the auto-savings worker.

This module lives OUTSIDE the ``auto_savings`` package on purpose so that nothing
in ``auto_savings`` or the test suite imports it. Loading the root ``.env`` must
happen ONLY when launching the worker locally, never during pytest (tests stay
hermetic).
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

# apps/auto-savings/dev.py -> parents[2] is the monorepo root
# (apps/auto-savings -> apps -> root).
REPO_ROOT = Path(__file__).resolve().parents[2]

# The worker package lives under ``src/`` (src layout). pytest picks it up via
# ``pythonpath = ["src"]`` in pyproject.toml, but that setting is pytest-only and
# the project declares no build-system, so ``uv run python dev.py`` never puts
# ``src`` on the import path. Add it here so the local launcher can import
# ``auto_savings`` without an editable install.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def load_local_env(root: Path) -> bool:
    """Tolerantly load ``root/.env`` for local dev.

    Returns whether the file existed. Never raises if the file is absent. Loads
    ``root/.env`` with ``override=True`` so the ``.env`` file is the source of
    truth for local dev, overriding any variables already present in the
    environment (this avoids stale shell exports silently shadowing ``.env``).
    """
    env_path = root / ".env"
    existed = env_path.is_file()
    load_dotenv(env_path, override=True)
    return existed


if __name__ == "__main__":
    load_local_env(REPO_ROOT)
    # The worker entrypoint (auto_savings.main) is added in a later task.
    from auto_savings.main import run

    run()
