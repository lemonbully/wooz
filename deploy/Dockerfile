# OneWooz application image  Django (Gunicorn) + Celery in one image, the role
# chosen at runtime by the entrypoint (web | worker | beat | migrate).
#
# Multi-stage: a build stage compiles the wheels (mysqlclient and Pillow need a
# toolchain), the runtime stage carries only the virtualenv + app and runs as an
# unprivileged user. Targets ARM64 (AWS Graviton, t4g.*) as well as x86_64 
# nothing here is architecture-specific.

# --- build ----------------------------------------------------------------
FROM python:3.13-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        default-libmysqlclient-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libpng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip wheel \
    && /venv/bin/pip install -r requirements.txt

# --- runtime --------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=onewooz.settings.prod

# Runtime shared libraries only (no compiler): the MySQL client lib for
# mysqlclient, libjpeg/zlib for Pillow, curl for the container health check.
RUN apt-get update && apt-get install -y --no-install-recommends \
        default-libmysqlclient-dev \
        libjpeg62-turbo \
        zlib1g \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=build /venv /venv

WORKDIR /app
COPY . .
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app

USER app
EXPOSE 8000

# Liveness: the app's own probe, host-agnostic (handled before host routing).
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
