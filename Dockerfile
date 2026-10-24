# syntax=docker/dockerfile:1
# Multi-stage build: uv installs the dependencies into /app/.venv; the runtime stage copies only
# that and the app. The Engine repo's Dockerfile is the reference for this layout.

FROM python:3.14-slim AS builder
# uv is pinned to an exact release (resolved 2026-10-24); Dependabot's docker ecosystem bumps it.
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so the layer is reused while only the app changes. No dev dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project
COPY manage.py pyproject.toml ./
COPY epistrel_console ./epistrel_console
COPY console ./console
COPY templates ./templates
# collectstatic runs here, as root, with debug on and before any CONSOLE_DATA_DIR=/data, so no
# secret key file is ever written into an image layer and nothing touches /data at build time.
RUN DJANGO_DEBUG=1 /app/.venv/bin/python manage.py collectstatic --noinput

FROM python:3.14-slim AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    CONSOLE_DATA_DIR=/data \
    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
RUN groupadd --system --gid 10001 console \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin console \
    && mkdir -p /data \
    && chown 10001:10001 /data
WORKDIR /app
COPY --from=builder --chown=console:console /app /app
COPY --chown=console:console docker/entrypoint.sh ./entrypoint.sh
RUN chmod 0755 /app/entrypoint.sh
# /data is created owned by `console` before VOLUME, so a fresh named volume inherits that owner.
VOLUME /data
USER console
EXPOSE 7739
# Stdlib only. 4 s urllib timeout: inside Docker's 5 s, over the Console's 3 s Engine probe.
# /health is 200 whatever the Engine's state, so an Engine outage never marks the Console unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:7739/health', timeout=4)"]
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["serve"]
