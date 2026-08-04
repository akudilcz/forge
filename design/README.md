# FORGE — Design Documentation

Technical architecture docs. Each file owns one architectural concern; together they describe how FORGE is built.

## Reading Order

| # | Doc | Scope |
|---|-----|-------|
| 00 | [Whitepaper](./00_whitepaper.md) | Vision, principles, the Project Graph, the Observe-Act Loop |
| 01 | [Architecture](./01_architecture.md) | The canonical spine: core concepts, graph model, gap analyser, agents, context, tools, prompts, pipeline, backend, work queue, frontend |
| 02 | [Context Management](./02_context_management.md) | How agent context is assembled across phases; zero-truncation policy and budget enforcement |
| 03 | [Phase Context Map](./03_phase_context_map.md) | Per-phase cross-reference: what actually runs, what context is assembled, which files are involved |

## Per-Phase Architecture

Each phase doc details the architecture of one build-loop phase (node types created, gap types, dispatch strategy, tools, context, convergence).

| # | Phase | Doc |
|---|-------|-----|
| 10 | 0  — Create Project         | [10_phase_00_create_project.md](./10_phase_00_create_project.md) |
| 11 | 1  — Ingest Document         | [11_phase_01_ingest_document.md](./11_phase_01_ingest_document.md) |
| 12 | 2  — Parse Document          | [12_phase_02_parse_document.md](./12_phase_02_parse_document.md) |
| 13 | 3  — Derive HLRs             | [13_phase_03_derive_hlrs.md](./13_phase_03_derive_hlrs.md) |
| 14 | 4  — Create Architecture     | [14_phase_04_create_architecture.md](./14_phase_04_create_architecture.md) |
| 15 | 5  — Assign Modules          | [15_phase_05_assign_modules.md](./15_phase_05_assign_modules.md) |
| 16 | 6  — Write Contracts         | [16_phase_06_write_contracts.md](./16_phase_06_write_contracts.md) |
| 17 | 7  — Derive LLRs             | [17_phase_07_derive_llrs.md](./17_phase_07_derive_llrs.md) |
| 18 | 8  — Create Designs          | [18_phase_08_create_designs.md](./18_phase_08_create_designs.md) |
| 19 | 9  — Write Test Strategy     | [19_phase_09_write_test_strategy.md](./19_phase_09_write_test_strategy.md) |
| 20 | 10 — Write Test Cases        | [20_phase_10_write_test_cases.md](./20_phase_10_write_test_cases.md) |
| 21 | 11 — Render Documentation    | [21_phase_11_render_documentation.md](./21_phase_11_render_documentation.md) |
| 22 | 12 — Generate Code           | [22_phase_12_generate_code.md](./22_phase_12_generate_code.md) |
| 23 | 13 — Workspace Sync          | [23_phase_13_workspace_sync.md](./23_phase_13_workspace_sync.md) |
| 24 | 14 — Build Deliverables      | [24_phase_14_build_deliverables.md](./24_phase_14_build_deliverables.md) |
| 25 | Observability                | [25_observability.md](./25_observability.md) |

## Numbering

- `00–09` — foundational architecture (whitepaper, spine, context, cross-cutting concerns)
- `10–24` — per-phase architecture, one file per build-loop phase
- `25+`   — system-wide concerns that span phases (observability, etc.)

Leave gaps in the numbering when inserting new docs; renumber only when ordering truly changes.
