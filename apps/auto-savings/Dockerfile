FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app/apps/auto-savings
COPY shared/connect/ /app/shared/connect/
COPY apps/auto-savings/pyproject.toml apps/auto-savings/uv.lock ./
RUN uv sync --frozen --no-install-project
COPY apps/auto-savings/ ./
ENV PATH="/app/apps/auto-savings/.venv/bin:$PATH" \
    PYTHONPATH="/app/apps/auto-savings/src"
CMD ["python", "-m", "auto_savings.main"]
