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

## Build system
- Use `uv` for the Python environment, with the `Makefile` as a convenience
  wrapper around `uv` commands.
- Frontend deps are managed with `pnpm` from `frontend/`.

## Documentation
- Keep 1:1:1 alignment across docs, code, and tests. All three are changeable —
  if you see an opportunity to improve any of them, take it.
- Update the relevant material in `design/` (technical) or `specs/` (user-facing)
  before making code changes.

## Testing
- Testing should focus on behavioural testing, ensuring code function matches the
  requirements embodied in the design material.
- Whenever new functionality is added or an existing function changes, write or
  update the corresponding tests in `backend/tests/`.
- Test files mirror the module they cover: `backend/foo/bar.py` →
  `backend/tests/test_bar.py`.
- Each new public function or method must have at least one happy-path test and
  one error/edge-case test.
- Run `make test-unit` after every change and fix any failures before considering
  the task done.
- Prefer small, focused tests; mock external I/O (LLM calls, DB, filesystem) so
  tests are fast and deterministic.
- **TDD for bug fixes**: when fixing a bug or edge case observed in a live trace,
  always write a failing test first that reproduces the exact failure, verify it
  fails, then apply the fix and confirm the test passes.
