# Contributing to FORGE

Thanks for your interest in FORGE. This guide covers how to get a development
environment running and what's expected of a change before it's merged.

## Getting set up

**Prerequisites:** Python 3.12+ (see `.python-version`), Node 20 (see `.nvmrc`)
with pnpm, and [uv](https://github.com/astral-sh/uv). Bazel is only needed if you
exercise generated workspaces.

```bash
git clone https://github.com/akudilcz/forge.git
cd forge
make install                # uv sync + pnpm install
cp .env.example .env        # add an LLM key (POE_API_KEY by default)
./start.sh                  # backend :7340, frontend :5173
```

`./stop.sh` shuts both down. Note it uses `fuser`, which is Linux-only — on macOS,
stop the two processes manually or run the servers in separate terminals with
`make dev-server` and `make dev-frontend`.

## Before you open a pull request

```bash
make check                            # ruff + mypy + backend unit tests
cd frontend && pnpm test && pnpm run type-check
```

CI runs exactly these. Integration tests (`make test-integration`) call a real LLM
and cost money, so they're excluded from CI — run them locally if your change
touches agent orchestration.

## How the test suite is organised

Tests fall into three tiers by cost. Knowing which tier your change needs saves
a lot of time and money.

**Tier 1 — offline, free, runs in CI.** Everything under `backend/tests/` except
`integration/`. This includes the phase contract tests, which are the fastest way
to catch a pipeline regression:

| File | What it pins down |
|---|---|
| `test_phase_contracts.py` | Postconditions of the five deterministic phases (0, 1, 11, 13, 14) against a **real** `ProjectGraph` — not a mock, so a wrong `parent_id`, `layer` or `trace_to` actually fails |
| `test_phase_contracts_llm.py` | Postconditions of the LLM phases, with a scripted agent standing in for the model. Only `dispatch.run_agent_task` and `agent.astream_events` are faked; gap analysis, the quality steps and `PhaseAuditor` all run for real |
| `test_oracle_framework.py` | That the oracle framework rejects wrong code — including a one-character stability defect |
| `test_oracle_conformance.py` | That each oracle *accepts* a spec-conformant reference implementation |
| `test_reparent_guards.py` | That reparent guards fire through both tool entry points |

**Tier 2 — real LLM, slow, costs money.** `backend/tests/integration/`, marked
`integration` and excluded by default. Driven by `make test-integration`.

**Tier 3 — needs bazel.** Marked `slow`, also excluded by default.

### Adding an end-to-end build case

The end-to-end suite proves FORGE builds *correct* software, not merely software
that compiles. Each case is a whitepaper plus an oracle:

1. Write `backend/tests/integration/whitepapers/NN_<slug>.md`. Follow the shape of
   an existing one — Abstract, numbered sections, Complexity, **Correctness
   Properties**, **Failure Modes**, exact **Public API**, Implementation Notes.
   The Implementation Notes must forbid the stdlib shortcut that would trivially
   solve the problem, or the pipeline can emit a wrapper that passes every
   functional test while implementing nothing.
2. Write `backend/tests/integration/oracles/<slug>.py` exporting `ORACLE`. Author
   it **from the whitepaper only** — the oracle is the one quality gate FORGE
   cannot grade itself on, and it is never shown to any agent.
3. Add a reference implementation and a parametrize entry to
   `test_oracle_conformance.py`, then confirm it passes. An oracle that has never
   executed will fail a *correct* build hours into a paid run.
4. Register the case in `BUILDS` in `test_algorithm_builds.py`.

Read the docstring at the top of `oracles/_base.py` for why this structure exists.

## Conventions

These are enforced by review rather than tooling, and they matter as much as the
tests passing:

- **Docs, code, and tests move together.** A behaviour change should update the
  relevant spec in `specs/` (user-facing) or `design/` (technical) in the same PR.
- **Tests mirror the module they cover.** `backend/foo/bar.py` →
  `backend/tests/test_bar.py`. Every new public function needs at least one
  happy-path and one error/edge-case test.
- **Fixing a bug? Write the failing test first.** Reproduce the exact failure,
  watch it fail, then fix it. This is the one place TDD is non-negotiable.
- **No silent fallbacks.** Missing preconditions raise loud errors rather than
  degrading output — no default arguments papering over missing config, no
  `.get(key, default)` hiding an absent node.
- **Keep it small.** Functions under ~50 lines, files under ~500.
- Mock external I/O (LLM calls, DB, filesystem) so unit tests stay fast and
  deterministic.

## Commit and PR style

Write commit messages in the imperative mood ("Add coverage report renderer", not
"Added..."). Keep PRs focused on one concern — a large refactor bundled with a
behaviour change is very hard to review.

## Reporting bugs and requesting features

Use the [issue templates](https://github.com/akudilcz/forge/issues/new/choose). For
bugs, the phase number and a trace excerpt are usually the fastest path to a fix.

Security issues should **not** be filed as public issues — see
[SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE) that covers this project, per section 5 of that
license. You do not need to sign a separate CLA.
