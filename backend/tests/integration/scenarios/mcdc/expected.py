"""Expected outcome for the MC/DC integration scenario."""

from backend.tests.integration.scenarios._base import ExpectedOutcome

EXPECTED = ExpectedOutcome(
    required_doc_node_ids=["LLR-0001", "LLR-0002", "LLR-0003", "DESIGN-0001"],
    min_source_files=1,
    min_test_files=3,
    required_llr_ids=["LLR-0001", "LLR-0002", "LLR-0003"],
    all_tests_pass=True,
    no_dead_code=True,
    min_statement_coverage=100.0,
    min_branch_coverage=100.0,
)
