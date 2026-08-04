# 10 — Installation and Launch

FORGE is a locally-hosted application. The user runs the backend and frontend
on their own machine (or a single container) and opens the Control Station in
a browser.

## Prerequisites

- Python **3.12+**
- Node **20 LTS** and **pnpm 9+**
- `uv` **0.4+**
- An LLM provider reachable from the machine — Ollama locally, or an API key
  for Poe, OpenRouter, or any other OpenAI-compatible service.

## Install

```bash
git clone https://github.com/akudilcz/forge.git && cd forge
make install        # uv sync + pnpm install
```

## Development launch

```bash
make dev-server     # Backend on http://localhost:7340
make dev-frontend   # Vite dev server on http://localhost:5173
```

The user opens `http://localhost:5173`. The dev frontend proxies API calls to
the backend on port 7340.

## Production launch (Docker)

```bash
docker build -t forge .
docker run -p 7340:7340 -v forge-data:/app/workspace forge
```

The workspace directory is mounted as a volume so that the graph database,
audit log, and generated files survive container restarts.

## Deployment (Render)

`render.yaml` defines the service. The user pushes to GitHub, connects the
repo, and sets `POE_API_KEY` or `OPENROUTER_API_KEY` as environment secrets.

## Workspace layout

At runtime the workspace contains:

```
workspace/
    forge.md                 — user-supplied whitepaper
    src/                     — generated source (created by Phase 12)
    tests/                   — generated tests (created by Phase 12)
    docs/                    — rendered docs (created by Phase 11)
    .forge/
        forge.db             — Project Graph + audit database (SQLite WAL)
        forge.log            — human-readable pretty-print tail
        forge.logs.db        — structured observability store (SQLite WAL)
```

## Ports

| Port  | Service                |
|-------|------------------------|
| 7340  | FastAPI backend + WS   |
| 5173  | Vite dev server (dev)  |

## Shutdown

Stopping the backend is safe at any time. In-flight agent work is either
completed at the next tool-call boundary or re-queued as an open gap on next
launch.
