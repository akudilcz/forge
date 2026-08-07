"""Plausibility floor for a parsed set of test evidence.

The graph-side checks in ``backend.analysis.evidence_integrity`` validate
RESULT nodes; this module validates the raw evidence *before* it becomes
nodes, using the two signals only the on-disk artefacts carry: recorded
duration and captured output.

Bazel's synthesized fallback ``test.xml`` — written when a target exits 0
without producing one — claims a single passing testcase named after the
target, with ``duration="0" time="0"``, and leaves an empty ``test.log``.
Real pytest output carries one testcase per function with a nonzero time
and a summary line in the log. A suite claiming passes where *every* pass
has neither measurable execution time nor any output did not run.

Design reference: specs/13-quality-and-convergence-guarantees.md
§Evidence integrity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.analysis.evidence_integrity import (
    NON_FAILURE_STATUSES,
    UNKNOWN_FILE,
    VALID_RESULT_STATUSES,
)

PASSED = "passed"


@dataclass(frozen=True)
class EvidenceRecord:
    """One parsed testcase plus the output volume of its target's log."""

    test_id: str
    file_path: str
    function_name: str
    status: str
    duration_ms: int
    output_bytes: int


def collect_evidence(workspace: Path) -> list[EvidenceRecord]:
    """Read every fresh ``bazel-testlogs/tests/*/test.xml`` as evidence records.

    No filtering: phase 13 purges stale artefacts before running, so every
    XML present is evidence from the run under judgement. ``test.log`` size
    for the same target supplies the output signal.
    """
    from backend.workspace.test_reports import parse_junit_xml  # noqa: PLC0415

    testlogs = workspace / "bazel-testlogs" / "tests"
    if not testlogs.exists():
        return []

    records: list[EvidenceRecord] = []
    for xml_file in sorted(testlogs.glob("*/test.xml")):
        log = xml_file.with_name("test.log")
        output_bytes = log.stat().st_size if log.exists() else 0
        for r in parse_junit_xml(xml_file):
            records.append(
                EvidenceRecord(
                    test_id=r.test_id,
                    file_path=r.file_path,
                    function_name=r.function_name,
                    status=r.status,
                    duration_ms=r.duration_ms,
                    output_bytes=output_bytes,
                )
            )
    return records


def check_evidence_plausibility(records: list[EvidenceRecord]) -> list[str]:
    """Return loud violation messages; an empty list means the evidence stands."""
    return [
        *_unknown_status_violations(records),
        *_anonymous_claim_violations(records),
        *_zero_signal_violations(records),
    ]


def _unknown_status_violations(records: list[EvidenceRecord]) -> list[str]:
    """A status outside the known set is unparsed evidence, not a verdict."""
    unknown = sorted({r.status for r in records if r.status not in VALID_RESULT_STATUSES})
    return [
        f"Test evidence carries unknown status {status!r} "
        f"(expected one of {sorted(VALID_RESULT_STATUSES)})"
        for status in unknown
    ]


def _anonymous_claim_violations(records: list[EvidenceRecord]) -> list[str]:
    """Non-failing results with no function name claim proof they cannot give.

    A *failing* target-level stub is exempt: it asserts nothing passed, and
    it already fails the suite through the normal route.
    """
    files = sorted(
        {
            r.file_path or UNKNOWN_FILE
            for r in records
            if r.status in NON_FAILURE_STATUSES and not r.function_name.strip()
        }
    )
    return [
        f"Vacuous test evidence for {path}: a non-failing result with no test "
        f"function name (Bazel's synthesized per-target XML shape). Per-function "
        f"evidence is required — target-level results can never prove a "
        f"requirement is verified."
        for path in files
    ]


def _zero_signal_violations(records: list[EvidenceRecord]) -> list[str]:
    """Passes with neither recorded duration nor any output did not execute."""
    passing = [r for r in records if r.status == PASSED]
    if not passing:
        return []
    if any(r.duration_ms > 0 or r.output_bytes > 0 for r in passing):
        return []
    return [
        f"Implausible test evidence: {len(passing)} passing result(s) with zero "
        f"recorded duration and zero captured output across "
        f"{len({r.file_path for r in passing})} file(s). A suite that executed "
        f"leaves at least one of the two. Treat as no evidence and re-run."
    ]
