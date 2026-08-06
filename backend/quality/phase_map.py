"""Phase ↔ node-type mapping for the quality pipeline.

Which pipeline phase authors each node type, and the inverse view.
Extracted from ``checks.py``, which re-exports both names so import
sites remain stable.
"""

from __future__ import annotations

# Phase in which each node type's quality boundary runs. SUITE maps to 10
# (U9, specs/03 Phases 9-10): phase 9 is the UNSUITED dispatch only, and the
# SUITE is judged inside phase 10's merged quality/semantic boundary. Listed
# after the CASE types so CASE_HLR stays PHASE_TO_NODE_TYPES[10][0].
NODE_TYPE_TO_PHASE: dict[str, int] = {
    "PARA": 2,
    "HLR": 3,
    "ARCHITECTURE": 4,
    "MODULE": 5,
    "CONTRACT": 6,
    "LLR": 7,
    "DESIGN": 8,
    "CASE_HLR": 10,
    "CASE_LLR": 10,
    "SUITE": 10,
    "CODE": 13,
    "TEST": 13,
}

# Inverse: phase number → node types produced in that phase.
PHASE_TO_NODE_TYPES: dict[int, list[str]] = {}
for _nt, _ph in NODE_TYPE_TO_PHASE.items():
    PHASE_TO_NODE_TYPES.setdefault(_ph, []).append(_nt)

