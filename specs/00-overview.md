# FORGE — Product Overview

FORGE turns a whitepaper into a fully traced, tested, production-ready codebase
by running specialised AI agents over a persistent Project Graph. The user's
interaction surface is a browser Control Station plus a workspace directory on
disk.

## What the user supplies

- A **whitepaper** (`forge.md`) placed in the active workspace directory. This
  is the single source specification the system parses, decomposes, and
  implements.
- An **LLM provider** configured in Settings (Poe, OpenRouter, Ollama, or any
  OpenAI-compatible endpoint). Credentials are either entered in the UI or
  exported as environment variables before launch.
- A **project name and description**, entered once when the project is created.

## What the user gets back

- A populated workspace containing generated `src/`, `tests/`, `docs/`, and
  build scaffolding (Bazel, `pyproject.toml`).
- A **Project Graph** — every requirement, design node, test case, and code
  artefact linked end-to-end. Browsable in the Graph Inspector.
- A **deliverables bundle** (Phase 14): a ZIP containing the full generated
  codebase plus seven rendered documents (requirements, architecture, interface
  spec, design spec, test plan, traceability matrix, coverage report).
- A **full audit trail** of every agent tool call, replayable per work item.

## End-to-end user journey

1. Install FORGE; launch backend (`make dev-server`) and frontend
   (`make dev-frontend`). Open the Control Station.
2. Open **Settings**; pick an LLM provider and save credentials.
3. Create the project (name + description). A PROJECT node is written to the
   graph.
4. Drop `forge.md` into the workspace.
5. Press **Play** on the Command Centre status bar. The Observe-Act loop starts
   from Phase 1.
6. Watch the work queue drain and the arena show the active agent. Approve
   phase transitions at human-approval gates.
7. When the pipeline reaches Phase 14, download the deliverables ZIP.

## Guarantees

- **Full traceability.** Every artefact traces to the paragraph that justified
  it; every paragraph traces to the artefacts derived from it. See
  [12-artifact-model-and-traceability.md](12-artifact-model-and-traceability.md).
- **No silent fallbacks.** Missing preconditions (no whitepaper, no provider
  key, missing node) produce loud errors rather than degraded output. See
  [13-quality-and-convergence-guarantees.md](13-quality-and-convergence-guarantees.md).
- **Idempotent re-runs.** Re-running a phase on a complete graph is a no-op,
  and a new process over the same graph DB and workspace resumes exactly
  where the last one stopped.
- **Reversibility.** Deletion is soft and node rewrites are versioned;
  consequential actions can be undone.

## Non-goals

- FORGE is **single-user, single-workspace** at runtime. It is not a
  multi-tenant SaaS.
- FORGE does not ship or host the generated software. Deployment of the
  produced codebase is the user's responsibility.
- FORGE does not edit the source whitepaper. Changes to `forge.md` must be made
  by the user; the system only reacts to them.
