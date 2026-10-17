# AgentScope: API + dashboard in one image.
#
#   docker build -t agentscope .
#   docker run --rm -p 127.0.0.1:8765:8765 -v agentscope-data:/data agentscope
#
# Publish the port on 127.0.0.1 ONLY. The container runs `agentscope serve --container`:
# it listens on 0.0.0.0 inside its own network namespace without an API token, and accepts
# only loopback Host headers, which is what a port published on 127.0.0.1 delivers. The Host
# check stops browsers (DNS rebinding), not a client that reaches the port directly and sends
# a loopback Host itself: publishing on 0.0.0.0 or a LAN address would expose an
# unauthenticated API. For network access, run `agentscope serve --container --allow-remote
# --allowed-host <name>` with AGENTSCOPE_API_TOKEN set.
# For PostgreSQL, see docker-compose.yml.

# --- 1. dashboard ---------------------------------------------------------------------------
FROM node:24.19-alpine AS dashboard
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# --- 2. Python environment ------------------------------------------------------------------
FROM python:3.13-slim AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Third-party dependencies first, from the lockfile only, so code changes reuse this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --extra postgres --no-install-project --no-editable
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
# Where api.app.find_web_dist looks first; the wheel ships it (hatch `artifacts`).
COPY --from=dashboard /build/frontend/dist/ ./src/agentscope/web/dist/
# Installed as a regular (non-editable) package, so the runtime image needs no source tree.
# No shared cache here: uv keys a local project's built wheel on pyproject.toml alone, so a
# cached wheel could predate the sources just copied in.
RUN uv sync --frozen --no-dev --extra postgres --no-editable --no-cache \
    && /app/.venv/bin/python -c "from agentscope.api.app import find_web_dist; import sys; d = find_web_dist(); print('dashboard:', d); sys.exit(0 if d and '/site-packages/' in str(d) else 1)"

# --- 3. runtime -----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime
LABEL org.opencontainers.image.title="AgentScope" \
      org.opencontainers.image.description="Local-first platform to debug, evaluate, and compare AI agents." \
      org.opencontainers.image.source="https://github.com/mateoosoriodelhonte/agentscope" \
      org.opencontainers.image.licenses="MIT"
RUN groupadd --system --gid 10001 agentscope \
    && useradd --system --uid 10001 --gid agentscope --home-dir /data --no-create-home \
       --shell /usr/sbin/nologin agentscope \
    && install -d -o agentscope -g agentscope -m 0700 /data
COPY --from=python-build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENTSCOPE_DATA_DIR=/data \
    AGENTSCOPE_PORT=8765 \
    LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false
USER agentscope:agentscope
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8765
# urllib with proxies disabled: a health probe never leaves the container.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; opener = urllib.request.build_opener(urllib.request.ProxyHandler({})); response = opener.open('http://127.0.0.1:' + os.environ.get('AGENTSCOPE_PORT', '8765') + '/api/health', timeout=4); sys.exit(0 if response.status == 200 else 1)"]
CMD ["agentscope", "serve", "--container"]
