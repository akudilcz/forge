"""Tests for the codegen_hash helper and its STALE_CODE detection integration."""

from __future__ import annotations

from backend.crew.code_gen import codegen_hash


def test_codegen_hash_deterministic() -> None:
    a = codegen_hash("design", "contract", "model")
    b = codegen_hash("design", "contract", "model")
    assert a == b


def test_codegen_hash_changes_with_design() -> None:
    a = codegen_hash("design-1", "contract", "model")
    b = codegen_hash("design-2", "contract", "model")
    assert a != b


def test_codegen_hash_changes_with_contract() -> None:
    a = codegen_hash("design", "contract-A", "model")
    b = codegen_hash("design", "contract-B", "model")
    assert a != b


def test_codegen_hash_changes_with_model() -> None:
    a = codegen_hash("design", "contract", "gpt-5")
    b = codegen_hash("design", "contract", "opus-4")
    assert a != b


def test_codegen_hash_handles_empty_inputs() -> None:
    """Empty strings hash reproducibly."""
    a = codegen_hash("", "", "")
    b = codegen_hash("", "", "")
    assert a == b
    assert len(a) == 64  # sha256 hex
