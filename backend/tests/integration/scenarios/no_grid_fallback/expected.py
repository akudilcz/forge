"""Expected outcomes for the no_grid_fallback scenario.

Validates that:
1. Generated code uses ONLY motion primitives for path expansion
2. No BFS grid-cell path functions exist (regardless of naming)
3. All paths go through the primitive-based A* search
"""

from backend.tests.integration.scenarios._base import ExpectedOutcome

EXPECTED = ExpectedOutcome(
    doc_count=8,
    required_doc_node_ids=["LLR-0001", "LLR-0002", "LLR-0003"],
    min_source_files=1,
    min_test_files=1,
    required_llr_ids=["LLR-0001", "LLR-0002", "LLR-0003"],
    gaps_resolved=True,
    bazel_tests_pass=True,
    all_tests_pass=True,
    no_dead_code=True,
    min_statement_coverage=100.0,
    min_branch_coverage=100.0,
    # No grid-cell BFS patterns allowed
    forbidden_function_names=["fallback", "cell_path", "grid_path", "bfs", "flood_fill"],
)
