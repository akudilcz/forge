"""Tests for backend.codegen.repair_ledger — per-cluster repair tracking.

U10 phase-12 rebalance: after ``REPAIR_DEPTH_CAP`` passes in which the
same gap cluster (keyed by the DESIGN's source path) persists, the next
mission pass regenerates that slice fresh instead of continuing repair
(Olausson et al.: diverse regeneration beats deep repair chains).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.codegen.gap_model import Gap, GapKind
from backend.codegen.repair_ledger import (
    REPAIR_DEPTH_CAP,
    RepairLedger,
    cluster_keys,
)

# ── cluster_keys ────────────────────────────────────────────────────────────


def _failing_gap(file_path: str) -> Gap:
    return Gap(
        kind=GapKind.FAILING_TESTS,
        node_id="",
        file_path=file_path,
        details="failing",
    )


def _ws_state(test_imports: dict[str, dict[str, list[int]]]) -> SimpleNamespace:
    test_files = {
        path: SimpleNamespace(imported_modules=imports)
        for path, imports in test_imports.items()
    }
    return SimpleNamespace(test_files=test_files)


class TestClusterKeys:
    def test_src_gap_maps_to_its_own_path(self) -> None:
        keys = cluster_keys([_failing_gap("src/foo.py")], _ws_state({}))
        assert keys == {"src/foo.py"}

    def test_test_gap_maps_to_imported_src_modules(self) -> None:
        ws = _ws_state({
            "tests/test_foo.py": {"src.foo.run": [3], "src.bar": [4], "ast": [1]},
        })
        keys = cluster_keys([_failing_gap("tests/test_foo.py")], ws)
        assert keys == {"src/foo.py", "src/bar.py"}

    def test_non_failing_gaps_are_ignored(self) -> None:
        gap = Gap(
            kind=GapKind.UNCOVERED_REQUIREMENT, node_id="LLR-1",
            file_path="", details="",
        )
        assert cluster_keys([gap], _ws_state({})) == set()

    def test_unknown_test_file_yields_no_key(self) -> None:
        keys = cluster_keys([_failing_gap("tests/test_ghost.py")], _ws_state({}))
        assert keys == set()


# ── RepairLedger ────────────────────────────────────────────────────────────


class TestRepairLedger:
    def test_cap_is_two_passes(self) -> None:
        assert REPAIR_DEPTH_CAP == 2

    def test_no_regeneration_before_cap(self) -> None:
        ledger = RepairLedger()
        ledger.record_pass({"src/foo.py"})
        assert ledger.regeneration_clusters() == set()

    def test_regeneration_after_cap_reached(self) -> None:
        ledger = RepairLedger()
        ledger.record_pass({"src/foo.py"})
        ledger.record_pass({"src/foo.py"})
        assert ledger.regeneration_clusters() == {"src/foo.py"}

    def test_resolved_cluster_resets(self) -> None:
        ledger = RepairLedger()
        ledger.record_pass({"src/foo.py"})
        ledger.record_pass(set())  # cluster resolved this pass
        ledger.record_pass({"src/foo.py"})  # reappears — counts from 1
        assert ledger.regeneration_clusters() == set()

    def test_independent_clusters_tracked_separately(self) -> None:
        ledger = RepairLedger()
        ledger.record_pass({"src/a.py", "src/b.py"})
        ledger.record_pass({"src/a.py"})
        assert ledger.regeneration_clusters() == {"src/a.py"}

    def test_regeneration_persists_while_cluster_persists(self) -> None:
        """Regeneration counts as a normal pass — a still-failing cluster
        stays in regeneration mode (bounded by MAX_MISSION_PASSES)."""
        ledger = RepairLedger()
        for _ in range(3):
            ledger.record_pass({"src/foo.py"})
        assert ledger.regeneration_clusters() == {"src/foo.py"}
