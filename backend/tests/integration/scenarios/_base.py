"""Base dataclass for integration test scenario expectations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExpectedOutcome:
    """Declares what a scenario must produce after Phase 11 + 12."""

    # Phase 11 — rendered docs
    doc_count: int = 8
    required_doc_node_ids: list[str] = field(default_factory=list)

    # Phase 12 — generated files
    min_source_files: int = 1
    min_test_files: int = 1

    # Phase 12 — traceability
    required_llr_ids: list[str] = field(default_factory=list)

    # Phase 12 — quality
    gaps_resolved: bool = True
    bazel_tests_pass: bool = True
    all_tests_pass: bool = True
    no_dead_code: bool = True

    # Phase 12 — coverage
    min_statement_coverage: float = 100.0
    min_branch_coverage: float = 100.0

    # Phase 12 — trace quality (scope creep detection)
    max_source_functions: int | None = None  # None = no limit
    forbidden_function_names: list[str] = field(default_factory=list)
