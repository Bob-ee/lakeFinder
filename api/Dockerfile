# Referenced by docker-compose.yml (`api: build: ./api`). The headless-MacBook deploy uses launchd
# instead (see README.md); this image is for the Docker Compose path in the design doc.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    SEAPLANE_DATA_OUT=/srv/data \
    SEAPLANE_DATA_MANUAL=/srv/manual

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY seaplane_api ./seaplane_api
RUN uv sync --no-dev --frozen || uv sync --no-dev

EXPOSE 8000
CMD ["uv", "run", "seaplane-api", "--host", "0.0.0.0", "--port", "8000"]
