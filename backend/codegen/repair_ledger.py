"""Repair-depth ledger — per-cluster repair tracking for Phase 12 passes.

U10 phase-12 rebalance (Olausson et al.: self-repair gains vanish once
cost is counted; diverse regeneration beats deep repair chains): the
mission loop tracks how many consecutive passes each *gap cluster*
persisted through. A cluster is keyed by the DESIGN's source path
(``src/<slug>.py``) — the stable identity of one vertical slice. After
``REPAIR_DEPTH_CAP`` passes in which the same cluster still carries
FAILING_TESTS gaps, the next pass regenerates that slice from scratch
instead of continuing to patch it (see mission_prompts.py).

Design reference: specs/03-build-pipeline.md §Repair depth and regeneration
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.codegen.gap_model import Gap, GapKind

#: Passes a cluster may persist through before its next pass regenerates
#: the slice fresh instead of continuing repair.
REPAIR_DEPTH_CAP = 2


def cluster_keys(gaps: list[Gap], ws_state: Any) -> set[str]:
    """Derive the set of DESIGN-source cluster keys touched by *gaps*.

    Only FAILING_TESTS gaps feed the ledger (coverage percentages are
    report-only and requirement gaps are per-LLR, not per-slice):

    - a gap on a ``src/`` file keys that file directly;
    - a gap on a ``tests/`` file keys every ``src/<module>.py`` the test
      file imports (``ws_state.test_files[...].imported_modules``).
    """
    keys: set[str] = set()
    for gap in gaps:
        if gap.kind is not GapKind.FAILING_TESTS:
            continue
        path = gap.file_path or ""
        if path.startswith("src/"):
            keys.add(path)
        elif path.startswith("tests/"):
            keys |= _src_modules_imported_by(path, ws_state)
    return keys


def _src_modules_imported_by(test_path: str, ws_state: Any) -> set[str]:
    """Map a test file to the ``src/<module>.py`` files it imports."""
    file_state = ws_state.test_files.get(test_path)
    if file_state is None:
        return set()
    modules: set[str] = set()
    for dotted in file_state.imported_modules:
        parts = dotted.split(".")
        if len(parts) >= 2 and parts[0] == "src":
            modules.add(f"src/{parts[1]}.py")
    return modules


@dataclass
class RepairLedger:
    """Consecutive-pass persistence counts per gap cluster."""

    attempts: dict[str, int] = field(default_factory=dict)

    def record_pass(self, clusters: set[str]) -> None:
        """Record one completed pass: bump persisting clusters, drop resolved.

        A cluster absent from *clusters* was repaired (or its tests were
        rewritten) — its count resets so a later reappearance starts a
        fresh repair budget.
        """
        for key in clusters:
            self.attempts[key] = self.attempts.get(key, 0) + 1
        for key in list(self.attempts):
            if key not in clusters:
                del self.attempts[key]

    def regeneration_clusters(self) -> set[str]:
        """Clusters whose repair budget is exhausted — regenerate next pass."""
        return {k for k, n in self.attempts.items() if n >= REPAIR_DEPTH_CAP}
