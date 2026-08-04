# ---- Frontend build stage ----
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# ---- Backend runtime stage ----
FROM python:3.12-slim
WORKDIR /app

# System deps + Bazel (for workspace compilation & test execution)
RUN apt-get update && apt-get install -y --no-install-recommends git curl gnupg && \
    curl -fsSL https://bazel.build/bazel-release.pub.gpg | gpg --dearmor -o /usr/share/keyrings/bazel-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/bazel-archive-keyring.gpg] https://storage.googleapis.com/bazel-apt stable jdk1.8" \
      > /etc/apt/sources.list.d/bazel.list && \
    apt-get update && apt-get install -y --no-install-recommends bazel && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# Backend source
COPY backend/ backend/

# Prompt templates (Jinja2)
COPY templates/ templates/

# Frontend build output
COPY --from=frontend-build /app/frontend/dist frontend/dist

# Create workspace directory
RUN mkdir -p /app/workspace

# Render sets PORT env var; default to 7340
ENV PORT=7340
EXPOSE ${PORT}

CMD uv run uvicorn backend.server.app:create_app \
    --factory \
    --host 0.0.0.0 \
    --port ${PORT}
