"""Plausibility floor for a parsed test-evidence set.

Behavioural reference: specs/13-quality-and-convergence-guarantees.md
§Evidence integrity. Bazel's synthesized fallback XML claims one PASSED
testcase per target with ``duration="0"``/``time="0"``, the target's own
name and an empty test.log — a suite claiming passes with no measurable
execution time and no output is not evidence.
"""

from __future__ import annotations

from pathlib import Path

from backend.quality.evidence_plausibility import (
    EvidenceRecord,
    check_evidence_plausibility,
    collect_evidence,
)

REAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" time="0.412">
  <testcase classname="tests.test_motion" name="test_plan" time="0.221"/>
  <testcase classname="tests.test_motion" name="test_stop" time="0.191"/>
</testsuite></testsuites>
"""

SYNTHETIC_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="tests/test_motion" tests="1" time="0">
  <testcase name="tests/test_motion" status="run" duration="0" time="0"/>
</testsuite></testsuites>
"""


def _record(**over: object) -> EvidenceRecord:
    base: dict[str, object] = {
        "test_id": "tests/test_motion.py::test_plan",
        "file_path": "tests/test_motion.py",
        "function_name": "test_plan",
        "status": "passed",
        "duration_ms": 221,
        "output_bytes": 640,
    }
    base.update(over)
    return EvidenceRecord(**base)  # type: ignore[arg-type]


# ── Happy path ──────────────────────────────────────────────────────────────


def test_per_function_evidence_with_time_and_output_is_plausible() -> None:
    assert check_evidence_plausibility([_record(), _record(
        test_id="tests/test_motion.py::test_stop", function_name="test_stop",
        duration_ms=191)]) == []


def test_empty_evidence_set_makes_no_claim() -> None:
    assert check_evidence_plausibility([]) == []


def test_fast_test_with_output_is_plausible() -> None:
    """Zero duration alone is not implausible — zero duration AND zero output is."""
    assert check_evidence_plausibility([_record(duration_ms=0)]) == []


def test_slow_test_without_captured_output_is_plausible() -> None:
    assert check_evidence_plausibility([_record(output_bytes=0)]) == []


# ── Vacuous per-target evidence ─────────────────────────────────────────────


def test_passing_record_without_a_function_name_is_rejected() -> None:
    problems = check_evidence_plausibility(
        [_record(test_id="tests/test_motion.py", function_name="")]
    )
    assert len(problems) == 1
    assert "tests/test_motion.py" in problems[0]
    assert "function" in problems[0].lower()


def test_failing_target_level_stub_is_not_a_vacuity_violation() -> None:
    """A failing file-level stub claims no proof — it already fails the suite."""
    assert check_evidence_plausibility(
        [_record(function_name="", status="failed", duration_ms=0, output_bytes=0)]
    ) == []


def test_unknown_status_is_rejected() -> None:
    problems = check_evidence_plausibility([_record(status="PASSED")])
    assert len(problems) == 1
    assert "status" in problems[0].lower()


# ── Plausibility floor ──────────────────────────────────────────────────────


def test_all_passes_with_zero_duration_and_zero_output_is_implausible() -> None:
    records = [
        _record(test_id=f"tests/test_{i}.py::test_x", file_path=f"tests/test_{i}.py",
                function_name="test_x", duration_ms=0, output_bytes=0)
        for i in range(5)
    ]
    problems = check_evidence_plausibility(records)
    assert len(problems) == 1
    assert "5" in problems[0]
    assert "implausible" in problems[0].lower()


def test_one_real_passing_result_clears_the_floor() -> None:
    records = [
        _record(test_id="tests/test_a.py::test_x", file_path="tests/test_a.py",
                duration_ms=0, output_bytes=0),
        _record(test_id="tests/test_b.py::test_y", file_path="tests/test_b.py",
                duration_ms=12, output_bytes=0),
    ]
    assert check_evidence_plausibility(records) == []


def test_suite_of_only_failures_is_not_flagged_by_the_floor() -> None:
    records = [_record(status="failed", duration_ms=0, output_bytes=0)]
    assert check_evidence_plausibility(records) == []


# ── Collection from bazel-testlogs ──────────────────────────────────────────


def _write_testlog(workspace: Path, target: str, xml: str, log: str) -> None:
    d = workspace / "bazel-testlogs" / "tests" / target
    d.mkdir(parents=True)
    (d / "test.xml").write_text(xml, encoding="utf-8")
    if log:
        (d / "test.log").write_text(log, encoding="utf-8")
    (workspace / "tests").mkdir(exist_ok=True)
    (workspace / "tests" / "test_motion.py").write_text("", encoding="utf-8")


def test_collect_evidence_reads_per_function_results_and_output(tmp_path: Path) -> None:
    _write_testlog(tmp_path, "test_motion", REAL_XML, "2 passed in 0.41s\n")
    records = collect_evidence(tmp_path)
    assert {r.function_name for r in records} == {"test_plan", "test_stop"}
    assert all(r.duration_ms > 0 for r in records)
    assert all(r.output_bytes > 0 for r in records)
    assert check_evidence_plausibility(records) == []


def test_collect_evidence_exposes_synthesized_xml_as_implausible(tmp_path: Path) -> None:
    _write_testlog(tmp_path, "test_motion", SYNTHETIC_XML, "")
    records = collect_evidence(tmp_path)
    assert len(records) == 1
    assert records[0].function_name == ""
    assert records[0].duration_ms == 0
    assert records[0].output_bytes == 0
    problems = check_evidence_plausibility(records)
    assert len(problems) == 2  # no function name + zero-signal floor
    assert any("tests/test_motion.py" in p for p in problems)


def test_collect_evidence_on_workspace_without_testlogs(tmp_path: Path) -> None:
    assert collect_evidence(tmp_path) == []
