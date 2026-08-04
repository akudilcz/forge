# FORGE

[![CI](https://github.com/akudilcz/forge/actions/workflows/ci.yml/badge.svg)](https://github.com/akudilcz/forge/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Node 20](https://img.shields.io/badge/node-20-green.svg)](https://nodejs.org/)

> Agentic software build system — turn a whitepaper into a fully traced, tested, production-ready codebase.

FORGE drives a team of specialised AI agents over a persistent **Project Graph** to transform a single specification document (a "whitepaper", `forge.md`) into generated source, tests, documentation, and build scaffolding. It runs an Observe-Act loop across a fixed 15-phase pipeline — from parsing the document and deriving high- and low-level requirements, through architecture and design, to code generation, workspace sync, and a final deliverables bundle.

It is built for a single user driving a single workspace from a browser **Control Station** (a React dashboard) backed by a FastAPI server. Every artefact it produces is traceable end-to-end: each requirement, design node, test case, and code file is linked back to the paragraph that justified it, with every agent tool call recorded in a queryable log. FORGE is provider-agnostic — it talks to any OpenAI-compatible LLM endpoint (Poe, OpenRouter, Ollama, etc.) via LiteLLM.

> **Project status:** beta, and actively developed as a single-maintainer project. The pipeline works end to end, but interfaces may still shift between versions. It is designed to run locally for a single user — see [SECURITY.md](SECURITY.md) before exposing it to a network.

<!-- TODO: add a screenshot or short GIF of the Control Station here — it's the fastest way for a newcomer to understand what FORGE does. -->

## Quick start

**You'll need:** Python 3.12+, Node 20.19+ with [pnpm](https://pnpm.io/installation) 9+, [uv](https://github.com/astral-sh/uv), and an API key for an OpenAI-compatible LLM provider. (Node 20.19 is the minimum — the frontend test runner needs `require(esm)` support.)

```bash
git clone https://github.com/akudilcz/forge.git
cd forge

make install                # uv sync + pnpm install
cp .env.example .env        # then add your key — POE_API_KEY by default

./start.sh                  # backend :7340, frontend :5173
```

Open **<http://localhost:5173>**, then:

1. Open **Settings**, pick your LLM provider, and save credentials.
2. Create a project (name + description).
3. Drop a `forge.md` whitepaper into the workspace directory — or copy one of the worked examples from [`demos/`](demos/) to get going immediately.
4. Press **Play** on the Command Centre to start the Observe-Act loop, approving phase transitions at the human-approval gates.
5. When the pipeline reaches Phase 14, download the deliverables ZIP.

`./stop.sh` shuts both servers down. It uses `fuser`, so it is **Linux-only** — on macOS or Windows, run the two servers in separate terminals instead:

```bash
make dev-server             # FastAPI on :7340, hot-reload
make dev-frontend           # Vite on :5173
```

## Configuration

All settings are optional except a provider key, and every credential can also be entered in the Control Station under **Settings** rather than the environment. Copy [`.env.example`](.env.example) to `.env` to get started.

| Variable | Purpose | Default |
|---|---|---|
| `POE_API_KEY` | API key for Poe, the default provider | — |
| `OPENROUTER_API_KEY` | API key for OpenRouter | — |
| `FORGE_API_KEY` | API key for any other OpenAI-compatible endpoint | — |
| `FORGE_LLM_BASE_URL` | Base URL for a custom endpoint (Ollama, vLLM, LM Studio, …) | — |
| `FORGE_LLM_MODEL` | Model identifier for a custom endpoint | — |
| `FORGE_WORKSPACE` | Directory FORGE builds into | `./workspace` |
| `FORGE_DEV_MODE` | Set to `1` for verbose errors and relaxed CORS | unset |
| `FORGE_AUTH_USER` | HTTP Basic auth username | unset |
| `FORGE_AUTH_PASS` | HTTP Basic auth password | unset |

Basic auth is enforced only when **both** `FORGE_AUTH_USER` and `FORGE_AUTH_PASS` are set. Bazel is a further prerequisite only if you compile or test inside generated workspaces; it is installed automatically in the Docker image.

## Features

- **Whitepaper-to-codebase pipeline** — a 15-phase build loop (Phase 0 create project through Phase 14 build deliverables) covering ingest, parse, HLRs, architecture, module assignment, contracts, LLRs, designs, test strategy, test cases, documentation, code generation, and workspace sync.
- **Persistent Project Graph** — a SQLite/SQLAlchemy-backed graph (with NetworkX in-memory analysis) linking every requirement, design, test, and code artefact, with version history for reversibility.
- **Specialised agent crew** — LangGraph-orchestrated agents using JSON function calling, dispatched against gap analysis over the graph; agents wield a large tool set (graph read/write, file read/write/patch, code search, git ops, test runner, shell exec, web fetch, lint, and more).
- **Browser Control Station** — a React + Vite dashboard with a Command Centre, Phase Dashboard, Graph Inspector, Agent Inspector, Traceability and Compliance views, a Monaco-based code viewer, and live updates over WebSockets.
- **Structured observability** — every agent tool call, LLM request and graph mutation is recorded to a queryable SQLite log with correlation ids (run, phase, gap, node, model, token counts, duration), on top of structlog.
- **Deliverables bundle** — Phase 14 produces a ZIP of the generated codebase plus rendered documents (requirements, architecture, interface spec, design spec, test plan, traceability matrix, coverage report).
- **No silent fallbacks** — missing preconditions (no whitepaper, no provider key, missing node) raise loud errors instead of degrading output; phase re-runs are idempotent.
- **Provider-agnostic LLM access** — configure Poe, OpenRouter, Ollama, or any OpenAI-compatible endpoint from Settings or via environment variables.

## Tech stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, Pydantic v2 / pydantic-settings, structlog. Agent orchestration via LangGraph + LangChain Core + LiteLLM. Graph storage on SQLAlchemy (async) + aiosqlite + NetworkX. Supporting libraries: tiktoken, watchfiles, mistune, Jinja2, jsonschema, numpy/scipy, click + rich (CLI).
- **Frontend:** TypeScript, React 18, Vite 5, Tailwind CSS, Zustand, TanStack Query, React Router, React Flow (`@xyflow/react`) + Dagre, Monaco editor, Framer Motion. Tested with Vitest + Testing Library.
- **Build / packaging:** Hatchling (Python wheel), uv (Python deps), pnpm (frontend deps), Make, Docker (multi-stage). Generated workspaces compile/test with Bazel.

## Running it other ways

**Packaged CLI** — the entry point `backend.main:cli` is exposed as `forge`:

```bash
uv run forge serve --workspace /path/to/workspace --host localhost --port 7340
```

**Docker** — the image serves on `$PORT` (default 7340):

```bash
make build                  # frontend bundle + Python wheel
docker build -t forge .
docker run -p 7340:7340 --env-file .env -v $(pwd)/workspace:/app/workspace forge
```

A [`render.yaml`](render.yaml) Blueprint is included for deployment to [Render](https://render.com). If you deploy FORGE anywhere reachable from a network, set `FORGE_AUTH_USER` and `FORGE_AUTH_PASS`.

## Testing

```bash
make test-unit              # backend unit tests (pytest + coverage)
make check                  # ruff + mypy, then unit tests — what CI runs
make test-integration       # real LLM agents: slow, needs an API key, costs money

cd frontend && pnpm test    # frontend tests (Vitest)
```

Integration tests are excluded from CI because they call a live LLM.

## Documentation

| Where | What |
|---|---|
| [`specs/`](specs/README.md) | User-facing feature specifications — what you see, do, and get |
| [`design/`](design/README.md) | Technical architecture, the whitepaper, and a per-phase breakdown |
| [`demos/`](demos/) | Example whitepapers you can feed straight into a build |
| [`templates/`](templates/) | Jinja2 prompt templates (`roles/`, `gaps/`, `quality/`, `shared/`) |

## Project structure

- `backend/` — FastAPI server and agent engine.
  - `server/` — app factory, routers (graph, agents, phases, requirements, compliance, settings, auth, …), middleware, WebSocket layer, lifespan.
  - `agents/`, `crew/` — agent definitions, factory, pool, throttling, and LangGraph flow/orchestration.
  - `tools/` — the agent tool set (graph ops, file read/write/patch, code search, git, run tests, shell exec, web fetch, lint, …).
  - `graph/`, `context/`, `analysis/`, `core/` — Project Graph engine, context assembly/budgeting, gap analysis, core domain logic.
  - `config/`, `comms/`, `audit/`, `observability/`, `tracing/`, `console/`, `services/` — configuration models/loader, messaging, audit logging, observability, tracing, console, and supporting services.
  - `tests/` — pytest suite (unit tests plus an `integration/` subdir).
  - `main.py` — CLI entry point; `forge_builder.py`, `work_queue.py`, `recipes.json`.
- `frontend/` — React + Vite Control Station (`src/` with `dashboards/`, `components/`, `hooks/`, `store/`, `lib/`, `test/`).
- `Makefile`, `start.sh`, `stop.sh` — dev/run helpers; `Dockerfile`, `render.yaml`, `.dockerignore` — deployment.
- `pyproject.toml`, `uv.lock` — Python project + dependencies.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the checks CI runs, and the conventions this project follows. Please report security issues privately per [SECURITY.md](SECURITY.md), and note that participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

FORGE is released under the [Apache License 2.0](LICENSE) — see [NOTICE](NOTICE) for attribution terms.

```
Copyright 2026 Andrew Kudilczak

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

Note that code **generated by** FORGE is yours — this license covers FORGE itself, not its output.
