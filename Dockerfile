# ── Build stage: install dependencies ──────────────────────────────
FROM python:3.12-slim AS base

ARG APT_MIRROR=https://mirrors.tencent.com
ARG PIP_INDEX_URL=https://mirrors.tencent.com/pypi/simple

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_INDEX_URL=${PIP_INDEX_URL}

# System deps: sqlite3 for manifest queries, CA certs for package downloads
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${APT_MIRROR}/debian-security|g" \
        -e "s|http://deb.debian.org/debian|${APT_MIRROR}/debian|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --home-dir /home/app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app /data /tmp/uv-cache \
    && chown -R app:app /app /data /tmp/uv-cache

# ── Dependency layer (cached unless pyproject.toml/requirements.lock.txt change) ──
RUN python -m venv .venv \
    && .venv/bin/python -m pip install --no-cache-dir --upgrade pip setuptools wheel
COPY pyproject.toml requirements.lock.txt ./
RUN .venv/bin/pip install --no-cache-dir -r requirements.lock.txt

# ── Source layer ────────────────────────────────────────────────────
COPY src/ src/
COPY prompts/ prompts/
COPY skills/ skills/
RUN .venv/bin/pip install --no-cache-dir --no-deps .

# ── Runtime configuration ───────────────────────────────────────────

# Persistent data: OAuth tokens + Manifest SQLite
VOLUME ["/data"]

# MCP transport settings (overridable at runtime)
ENV PYTHONPATH=/app/src \
    HOME=/home/app \
    UV_CACHE_DIR=/tmp/uv-cache \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    DESTINY_MANIFEST_PATH=/data/manifest \
    DESTINY_TOKEN_PATH=/data \
    DATA_PATH=/data

EXPOSE 8000

# Health check: verify the app responds, not just that the TCP port is open.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD /app/.venv/bin/python -c "import os, urllib.request; port=int(os.environ.get('MCP_PORT', '8000')); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).read()" || exit 1

USER app

CMD ["/app/.venv/bin/python", "-m", "destiny_mcp.server"]
