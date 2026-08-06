# Phase 6: Write Contracts

## Purpose

Phase 6 writes a CONTRACT for each MODULE. A contract is the public interface
specification: function signatures, pre/post conditions, invariants. Contracts are
the last phase of the architectural skeleton -- they must be complete before LLRs.

## Node Type Created

| Field     | Value           |
|-----------|-----------------|
| Type      | CONTRACT        |
| Layer     | 4               |
| Parent    | MODULE          |
| trace_to  | (none)          |

One CONTRACT per MODULE. The contract does not use trace_to because its scope is
defined entirely by its parent MODULE (which already traces to HLRs).

## Structured Public API Surface

Prose alone proved lossy: a live build (merge_sort, oracle 1/24) shipped a
workspace where none of the whitepaper's required symbols (`sort`,
`sorted_copy`, `is_sorted`) existed under their required module name. Every
CONTRACT therefore also carries the public API surface as STRUCTURED data:

```json
properties.public_api = [
  {"module": "merge_sort", "symbol": "sort", "kind": "function",
   "signature": "def sort(items: list, *, key=None, reverse=False) -> list"},
  {"module": "merge_sort", "symbol": "SortStats", "kind": "class",
   "signature": "class SortStats"},
  {"module": "merge_sort", "symbol": "SortStats.merge", "kind": "method",
   "signature": "def merge(self, other: 'SortStats') -> 'SortStats'"}
]
```

Schema (enforced at write time):
- `public_api` is a **non-empty list** of objects.
- Each entry has exactly the string keys `module`, `symbol`, `kind`,
  `signature`, all non-empty.
- `kind` is one of `function`, `class`, `method`.
- `module` is the top-level module name the symbol must be importable from
  (maps to `src/<module>.py`); `symbol` for a method is `Class.method`.
- `signature` is transcribed **exactly** from the whitepaper's normative
  signature block — never paraphrased or summarised.

### Prohibited constructs

Whitepapers sometimes forbid implementation techniques outright (live
trace: expression_evaluator §12 forbids `eval`/`compile`/`ast`, yet the
generated tokenizer delegated to `compile()` — functionally "correct"
while implementing nothing). Contracts carry these bans as structured
data too, **optional** unlike `public_api`:

```json
properties.prohibited_constructs = [
  {"construct": "compile", "rationale": "§12: evaluator must not delegate
    to Python's compiler"},
  {"construct": "ast", "rationale": "§12: no ast-module parsing"}
]
```

Schema (validated only when the key is present):
- a list of objects, each with non-empty string keys `construct` and
  `rationale`;
- `construct` is a dotted callable/module name (`eval`, `compile`,
  `ast`, `ast.literal_eval`) transcribed from the whitepaper's explicit
  prohibitions ("must not use X", "forbidden", "without using");
- omitted or empty when the whitepaper states no prohibitions.

Phase 12's gate statically scans every `src/` file for uses of each
construct — direct calls, attribute calls, imports, and aliased imports
— and raises a blocking gap on any hit (design/22). Tests are exempt:
prohibitions constrain the implementation, not its verification.

Enforcement is split by what each layer can see:
- **Write time** (`check_contract_public_api` in
  `backend/analysis/node_invariants.py`, wired through
  `backend/tools/graph_write_validation.py`): a CONTRACT written without a
  valid, non-empty `public_api` is rejected with an actionable `ERROR:` —
  the analyser cannot read the whitepaper, so presence + shape are the
  write-time invariant.
- **Phase 8+** (`CONTRACT_VIOLATION`, design/01 §3 and design/18): the gap
  analyser checks each DESIGN's declared signatures against `public_api` —
  a DESIGN may only be flagged when it contradicts a `public_api` function
  signature; internal helpers are never violations.
- **Phase 12** (API-surface gate, design/22): each entry is verified against
  the actual workspace — see "API-surface gate".

## Gap Type

**UNCONTRACTED** -- raised for each MODULE that has no CONTRACT child. The gap
scanner walks every MODULE and checks for the presence of a CONTRACT child node.

## Dispatch Strategy

**Structural dispatch.** One gap per MODULE, one agent call per gap. Contracts are
independent of each other -- MODULE A's interface does not constrain MODULE B's
interface at authoring time. Parallelism is safe.

## Tools

- `graph_read` -- read MODULE, ARCHITECTURE, and traced HLRs.
- `graph_add_node` -- create the CONTRACT node.

## Context Provided to the Agent

| Slot              | Content                                            |
|-------------------|----------------------------------------------------|
| Ancestor chain    | MODULE (id, title, content) + PROJECT (id, title)  |
| ARCHITECTURE      | Full ARCHITECTURE content                          |
| Traced HLRs       | HLRs traced to this MODULE (id, title, content)   |

The contract must reflect the module's requirements within the system architecture.
The agent needs the module's responsibility statement, the broader architecture for
cross-module interface alignment, and the specific HLRs the module must satisfy.

## Agent Procedure

1. Read the MODULE content (responsibility statement).
2. Read the ARCHITECTURE content for system-wide interface conventions.
3. Read all HLRs traced to this MODULE.
4. Write the CONTRACT specifying:
   - Public function/method signatures with parameter and return types.
   - Preconditions (what callers must guarantee).
   - Postconditions (what the module guarantees on success).
   - Invariants (properties maintained across all operations).
   - Error behaviour (failure modes and their handling).
5. Distil the public API into `properties.public_api` (see "Structured
   Public API Surface") — signatures transcribed exactly from the source
   material's signature blocks.
6. Transcribe any explicit implementation prohibitions into
   `properties.prohibited_constructs` (see "Prohibited constructs");
   omit when the source material states none.
7. Create the CONTRACT node as child of the MODULE.

## Pipeline Steps

| Order | Step           | Purpose                                         |
|-------|----------------|--------------------------------------------------|
| 1     | structural       | Detect and dispatch UNCONTRACTED gaps            |
| 2     | quality_gaps     | Check CONTRACT nodes for content quality         |
| 3     | combined_quality | Batched LLM judging of authored nodes (title axes) |
| 4     | semantic         | Detect and remove semantic duplicate CONTRACTs   |

## Quality Checks

| Quality Gap         | Trigger                                               |
|---------------------|-------------------------------------------------------|
| STALE_NODE          | CONTRACT's provenance hash mismatches parent MODULE content |
| EMPTY_CONTENT       | CONTRACT has blank or whitespace-only content          |
| INADEQUATE_CONTENT  | Content lacks function signatures or conditions        |

INADEQUATE_CONTENT is strict for contracts. A contract that says "handles user
authentication" without specifying function signatures, parameters, return types,
and error cases is inadequate. Precision is the entire point.

## Frontend Dashboard

**Phase Dashboard.** NodeTablePanel listing CONTRACT nodes alongside their parent
MODULE names. Columns: MODULE title, CONTRACT title, content preview, status badge,
quality gaps.

Users can expand a row to see the full contract content inline. This pairs the
contract with its module context for easy review.

## Relationship to Other Phases

- **Depends on:** Phase 5 (MODULEs must exist with HLR assignments).
- **Blocks:** Phase 7 (LLR derivation uses contracts as boundaries).
- Contracts close the architectural skeleton. After Phase 6, the system has:
  PROJECT → ARCHITECTURE → MODULE[] → CONTRACT[]. Phase 7 fills in the
  detailed requirements (LLRs) within this structure.
