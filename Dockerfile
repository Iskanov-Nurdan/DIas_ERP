# syntax=docker/dockerfile:1.7
# ─────────── Stage 1: сборка зависимостей в изолированный venv ───────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r requirements.txt

# ─────────── Stage 2: runtime ───────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

# fonts-dejavu-core — кириллица в PDF (reportlab/xhtml2pdf); curl — для HEALTHCHECK
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system dias \
 && useradd --system --gid dias --home-dir /app --shell /usr/sbin/nologin dias

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=dias:dias . /app
RUN install -d -o dias -g dias /app/staticfiles \
 && chmod +x /app/docker/entrypoint.sh

USER dias
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health/ || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# Daphne (ASGI) — обязателен, HTTP + WebSocket (Channels) в одном процессе
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "--proxy-headers", "config.asgi:application"]
