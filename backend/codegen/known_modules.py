"""Single source of truth for module-name allowlists.

Historically three hand-maintained lists (codegen_helpers, bazel_gen,
build_env) drifted apart — one omitted ``datetime``/``random``/``unittest``
(valid test files were deleted), another omitted ``__future__`` (false
"add to requirements.txt" diagnostics every phase-12 iteration). This
module replaces all of them.

Design reference: design/22_phase_12_generate_code.md (Step 2).
"""

from __future__ import annotations

import sys

# Every stdlib top-level module for the running interpreter, including
# __future__ — authoritative, never hand-maintained.
STDLIB_MODULES: frozenset[str] = frozenset(sys.stdlib_module_names)

# Modules internal to the generated workspace: the generated package
# itself, its tests, the seeded @traces decorator package, and pytest's
# conftest.
WORKSPACE_MODULES: frozenset[str] = frozenset({"src", "tests", "tracing", "conftest"})
