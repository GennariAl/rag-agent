FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.13-slim

ENV UV_COMPILE_BYTE=1
ENV UV_LINK_MODE=copy

WORKDIR /srv
COPY ./pyproject.toml ./uv.lock ./.python-version ./

RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY ./app ./app
COPY ./static ./static
USER nobody
CMD ["/srv/.venv/bin/fastapi", "run", "app/main.py", "--port", "8080", "--host", "0.0.0.0"]
