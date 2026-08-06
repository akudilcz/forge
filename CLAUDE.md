# Project Instructions

## Design and code
- Design should be as simple as possible; as complex as necessary.
- Keep the code well organised and clean.
- Keep functions small (no more than 50 lines).
- Keep files small too (aim for no more than 500 lines).
- Apply SOLID principles at all times.
- No silent fallbacks: missing preconditions raise loud errors rather than
  degrading output. No default function arguments, no implicit
  `.get(key, default)` fallbacks.
- New backend code lives in the package matching its concern (pipeline /
  quality / codegen / workspace / rendering / prompting / core / ...) — no new
  top-level orphan modules.
- Module names describe the artifact, not its history: no `_helpers`-by-
  extraction names, no `test_` prefix outside `backend/tests/`.

## Build system
- Use `uv` for the Python environment, with the `Makefile` as a convenience
  wrapper around `uv` commands.
- Frontend deps are managed with `pnpm` from `frontend/` (node lives in
  `~/.local/node/bin` — prefix PATH with it).

## Documentation
- **The code is the design.** `specs/` is the single source of truth for what
  FORGE does and guarantees (user-facing behaviour, artifact model,
  quality/convergence guarantees, configuration surface). Technical intent
  lives in the code, its docstrings, and its behavioural tests — there is no
  separate design-document tree.
- Keep specs, code, and tests aligned: when a guarantee or user-visible
  behaviour changes, update the relevant `specs/` file in the same change.
  Internal refactors that keep guarantees intact need no spec edit.

## Testing
- Testing should focus on behavioural testing, ensuring code function matches
  the guarantees stated in `specs/`.
- Whenever new functionality is added or an existing function changes, write or
  update the corresponding tests in `backend/tests/`.
- Test files mirror the module they cover, including its package:
  `backend/foo/bar.py` → `backend/tests/foo/test_bar.py`.
- Each new public function or method must have at least one happy-path test and
  one error/edge-case test.
- Run `make test-unit` after every change and fix any failures before considering
  the task done.
- Prefer small, focused tests; mock external I/O (LLM calls, DB, filesystem) so
  tests are fast and deterministic. Unit tests must never make network calls
  (the FORGE_UNIT_LLM_GUARD sentinel enforces this).
- **TDD for bug fixes**: when fixing a bug or edge case observed in a live trace,
  always write a failing test first that reproduces the exact failure, verify it
  fails, then apply the fix and confirm the test passes.
