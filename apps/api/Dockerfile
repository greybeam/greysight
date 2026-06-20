# Reproducible image for the Greysight FastAPI backend (uv-managed).
#
# Build context is the REPO ROOT, not apps/api: the API loads the shared SQL
# registry from <repo-root>/sql at runtime (app/services/dashboard_registry.py
# resolves it via parents[4]). The repo layout is mirrored inside the image so
# that path resolution keeps working — /app stands in for the repo root.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Compile bytecode for faster cold starts; copy into the venv (no host symlinks).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/apps/api

# Install dependencies in their own cached layer. The lockfile is the source of
# truth: --frozen fails the build if pyproject.toml has drifted from uv.lock.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-install-project

# Application code (imported from the working dir; no build-system to install)
# plus the repo-root SQL assets the dashboard registry reads at runtime.
COPY apps/api/ ./
COPY sql/ /app/sql/
COPY shared/ /app/shared/

# Run the venv's executables directly (no uv resolution at container start).
ENV PATH="/app/apps/api/.venv/bin:$PATH"

# Railway injects $PORT at runtime; default to 8000 for plain `docker run`.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
