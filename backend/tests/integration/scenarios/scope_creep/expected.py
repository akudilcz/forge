"""Expected outcomes for the scope_creep scenario.

This scenario validates that the trace quality gate catches unrequired
functions. The requirements are deliberately tight: "raise KeyError" means
raise, not fall back. Any get_or_default, retry, cache, or fallback
function is scope creep.

After the quality gate cleanup pass, the source should contain ONLY
the 3 required functions (get_value, set_value, list_keys) plus
__init__ — nothing else.
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
    # Trace quality — no extra functions beyond what requirements specify
    max_source_functions=4,  # __init__, get_value, set_value, list_keys
    forbidden_function_names=["fallback", "default", "cache", "retry", "backup"],
)
