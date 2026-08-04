# Project Profile

## Structure
- Languages: Python (236 files), TypeScript (54 files), JavaScript (1 files)
- Build: Makefile + uv/pip
- Tests: pytest

## Directory Layout
CLAUDE.md
Dockerfile
LICENSE
MagicMock/ (0 files)
Makefile
README.md
backend/ (229 files)
demos/ (2 files)
devtools/ (7 files)
docs/ (12 files)
frontend/ (61 files)
pyproject.toml
render.yaml
start.sh
stop.sh
uv.lock

## Project Instructions (from CLAUDE.md)
## Design and Code Instruction
- design should be as simple as possible; as complex as necessary.
- keep the code well organise and clean
- keep functions small (no more than 50 lines)
- keep files small too (aim for no more than 500 lines)
- apply SOLID principles at all times

## Build System
- use uv for our python environment and use Makefile for convenince wrapper around uv commands

## Design
- update the relevant doco/ before making code changes

## Testing
- testing should focus on behavioural testing ensuring our code function matches the requirements embodied in the design material
- whenever new functionality is added or an existing function is changed, write or update the corresponding tests in backend/tests/
- test files mirror the module they cover: backend/foo/bar.py → backend/tests/test_bar.py
- each new public function or method must have at least one happy-path test and one error/edge-case test
- run `uv run pytest backend/tests/ -x -q` after every change and fix any failures before considering the task done
- prefer small, focused tests; mock external I/O (LLM calls, DB, filesystem) so tests are fast and deterministic
- **TDD for bug fixes**: when fixing a bug or edge case observed in a live trace, ALWAYS write a failing test first that reproduces the exact failure, verify it fails, then apply the fix and confirm the test passes. This ensures regressions are caught and the fix is validated against the real scenario.
