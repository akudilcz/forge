"""API-surface gate — verify CONTRACT public_api against the workspace.

Deterministic phase-12 check (design/22 "API-surface gate"): every
``properties.public_api`` entry on a CONTRACT node (schema: design/16)
must be exposed by the generated workspace — module file present, symbol
defined with the declared kind, and no relative imports anywhere in
``src/`` (src modules are consumed as top-level modules, where relative
imports break).

Purely static: facts come from the workspace scanner's AST pass
(``FileState.symbols`` / ``FileState.relative_imports``); no code is
executed. Live trace motivating this gate: the merge_sort build shipped
none of the whitepaper's required symbols (oracle 1/24).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.codegen.gap_model import Gap, GapKind
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.workspace.scanner import FileState

#: FileState.symbols kinds that satisfy a function/class entry via an
#: explicit absolute-import re-export.
_REEXPORT_OK: frozenset[str] = frozenset({"function", "class"})


def check_api_surface(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    graph: Any,
) -> None:
    """Emit API_SURFACE_MISMATCH gaps for unexposed CONTRACT API entries."""
    _check_relative_imports(gaps, source_files)
    for node in graph.all_nodes():
        if node.node_type != "CONTRACT":
            continue
        api = (node.properties or {}).get("public_api")
        if not api:
            # Legacy CONTRACT predating the design/16 schema: presence is
            # a phase-6 write-time invariant; the phase-12 mission agent
            # cannot author contracts, so blocking here would deadlock.
            forge_logger.emit(
                "WARN", "GAPF ",
                f"CONTRACT {node.node_id} has no public_api — "
                "API surface unverifiable (pre-schema contract)",
            )
            continue
        for entry in api:
            _check_entry(gaps, source_files, node.node_id, entry)


def _check_entry(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    contract_id: str,
    entry: dict[str, str],
) -> None:
    """Verify one public_api entry: module file, symbol, and kind."""
    module, symbol, kind = entry["module"], entry["symbol"], entry["kind"]
    path = f"src/{module}.py"
    state = source_files.get(path)
    if state is None:
        gaps.append(Gap(
            kind=GapKind.API_SURFACE_MISMATCH,
            node_id=contract_id,
            file_path=path,
            details=(
                f"CONTRACT {contract_id} requires module {module!r} "
                f"({path}) exposing {symbol!r}, but the file does not "
                f"exist. Create it — do not rename or fragment the module."
            ),
            context={"entry": dict(entry)},
        ))
        return
    actual = state.symbols.get(symbol)
    if actual is None:
        gaps.append(Gap(
            kind=GapKind.API_SURFACE_MISMATCH,
            node_id=contract_id,
            file_path=path,
            details=(
                f"CONTRACT {contract_id} requires {kind} {symbol!r} in "
                f"{path} ({entry['signature']}), but the symbol is not "
                f"defined or imported there."
            ),
            context={"entry": dict(entry)},
        ))
        return
    if actual != kind and not (actual == "import" and kind in _REEXPORT_OK):
        gaps.append(Gap(
            kind=GapKind.API_SURFACE_MISMATCH,
            node_id=contract_id,
            file_path=path,
            details=(
                f"CONTRACT {contract_id} requires {symbol!r} in {path} to "
                f"be a {kind}, but it is a {actual}. Expected signature: "
                f"{entry['signature']}."
            ),
            context={"entry": dict(entry), "actual_kind": actual},
        ))


def check_prohibited_constructs(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    graph: Any,
) -> None:
    """Emit PROHIBITED_CONSTRUCT gaps for banned constructs used in src/.

    Each CONTRACT ``prohibited_constructs`` entry (design/16; optional) is
    a hard ban on the implementation. Matching is alias-resolved and
    static: imports of the construct (or a member of it) and calls whose
    resolved dotted name is the construct (or starts with it). Test files
    never reach this check — prohibitions constrain the implementation,
    not its verification. (Live trace: expression_evaluator's tokenizer
    delegated to compile() despite the whitepaper's §12 ban.)
    """
    for node in graph.all_nodes():
        if node.node_type != "CONTRACT":
            continue
        banned = (node.properties or {}).get("prohibited_constructs")
        if not banned:
            continue  # optional by design — absent means nothing is banned
        for entry in banned:
            for path, state in source_files.items():
                _check_construct_in_file(gaps, node.node_id, entry, path, state)


def _check_construct_in_file(
    gaps: list[Gap],
    contract_id: str,
    entry: dict[str, str],
    path: str,
    state: FileState,
) -> None:
    """Emit one gap per banned-construct use site in a single file."""
    construct, rationale = entry["construct"], entry["rationale"]
    prefix = construct + "."
    hits: list[tuple[str, int]] = []
    for dotted, lines in state.imported_modules.items():
        if dotted == construct or dotted.startswith(prefix):
            hits.extend((f"import of {dotted!r}", line) for line in lines)
    for dotted, lines in state.call_targets.items():
        if dotted == construct or dotted.startswith(prefix):
            hits.extend((f"call to {dotted!r}", line) for line in lines)
    for use, line in hits:
        gaps.append(Gap(
            kind=GapKind.PROHIBITED_CONSTRUCT,
            node_id=contract_id,
            file_path=path,
            details=(
                f"{path} line {line}: {use} — CONTRACT {contract_id} "
                f"prohibits {construct!r} ({rationale}). Rewrite the "
                f"implementation without it; tests are exempt."
            ),
            context={"construct": construct, "line": line, "use": use},
        ))


def _check_relative_imports(
    gaps: list[Gap],
    source_files: dict[str, FileState],
) -> None:
    """Flag every src/ file using relative imports (breaks top-level use)."""
    for path, state in source_files.items():
        if not state.relative_imports:
            continue
        rendered = "; ".join(state.relative_imports)
        gaps.append(Gap(
            kind=GapKind.API_SURFACE_MISMATCH,
            node_id="",
            file_path=path,
            details=(
                f"{path} uses relative import(s) — {rendered} — which "
                f"fail when the module is imported top-level. Rewrite as "
                f"absolute imports (from src.<module> import ...)."
            ),
            context={"relative_imports": list(state.relative_imports)},
        ))
