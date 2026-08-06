# FORGE Specs

The single source of truth for what FORGE is, does, and guarantees. Each
file describes what the user sees, does, and gets — and the contracts and
guarantees the system upholds — not how it is implemented. For
implementation detail, the code is the design.

| #  | Spec                                                                          | Covers                                          |
|----|-------------------------------------------------------------------------------|-------------------------------------------------|
| 00 | [Overview](00-overview.md)                                                    | Product summary and end-to-end journey          |
| 01 | [Project Creation](01-project-creation.md)                                    | Creating the PROJECT root                       |
| 02 | [Whitepaper Ingest](02-whitepaper-ingest.md)                                  | Supplying `forge.md`                            |
| 03 | [Build Pipeline](03-build-pipeline.md)                                        | Phases 0–14, completion criteria, approval gates|
| 04 | [Loop Control](04-loop-control.md)                                            | Play/Pause and loop semantics                   |
| 05 | [Command Centre](05-command-centre.md)                                        | Home dashboard: status, queue, arena, log       |
| 06 | [Phase Dashboard](06-phase-dashboard.md)                                      | Per-phase review and re-run                     |
| 07 | [Settings](07-settings.md)                                                    | LLM provider and configuration surface          |
| 08 | [Graph & Agent Inspectors](08-graph-inspector.md)                             | Browsing artefacts and audit trail              |
| 09 | [Deliverables](09-deliverables.md)                                            | Phase 14 bundle and rendered docs               |
| 10 | [Installation and Launch](10-installation-and-launch.md)                      | Prereqs, run, deploy, workspace layout          |
| 11 | [Observability](11-observability.md)                                          | Structured logs, LLM call trace, report tools   |
| 12 | [Artifact Model and Traceability](12-artifact-model-and-traceability.md)      | Node types, trace pairs, traceability chain, resume |
| 13 | [Quality and Convergence Guarantees](13-quality-and-convergence-guarantees.md)| Invariants, judging, deletion safety, bounds    |
