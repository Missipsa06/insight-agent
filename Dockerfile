FROM ghcr.io/astral-sh/uv:0.11.1-python3.12-trixie-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dépendances d'abord : couche mise en cache tant que pyproject/uv.lock ne changent pas
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY src ./src
COPY tests ./tests
RUN uv sync --frozen

# Par défaut : génère la base puis lance les tests
CMD ["sh", "-c", "uv run python -m insight_agent.data.generate && uv run pytest -q"]
