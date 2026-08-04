# FORGE Specs

User-facing feature specifications. Each file describes what the user sees,
does, and gets — not how it is implemented. Architectural and internal
details live in [`design/`](../design/README.md).

| #  | Spec                                                          | Covers                                         |
|----|---------------------------------------------------------------|------------------------------------------------|
| 00 | [Overview](00-overview.md)                                    | Product summary and end-to-end journey         |
| 01 | [Project Creation](01-project-creation.md)                    | Creating the PROJECT root                      |
| 02 | [Whitepaper Ingest](02-whitepaper-ingest.md)                  | Supplying `forge.md`                           |
| 03 | [Build Pipeline](03-build-pipeline.md)                        | Phases 0–14 and approval gates                 |
| 04 | [Loop Control](04-loop-control.md)                            | Play/Pause and loop semantics                  |
| 05 | [Command Centre](05-command-centre.md)                        | Home dashboard: status, queue, arena, log      |
| 06 | [Phase Dashboard](06-phase-dashboard.md)                      | Per-phase review and re-run                    |
| 07 | [Settings](07-settings.md)                                    | LLM provider configuration                     |
| 08 | [Graph & Agent Inspectors](08-graph-inspector.md)             | Browsing artefacts and audit trail             |
| 09 | [Deliverables](09-deliverables.md)                            | Phase 14 bundle and rendered docs              |
| 10 | [Installation and Launch](10-installation-and-launch.md)      | Prereqs, run, deploy, workspace layout         |
| 11 | [Observability](11-observability.md)                          | Structured logs, categories, `/logs` viewer    |
