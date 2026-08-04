"""Workspace router — file tree inspection, file content, and forge.md management endpoints.

Provides read access to the project workspace file tree and individual files,
plus upload/save/retrieve endpoints for the forge.md source document.
"""

from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from backend.config.models import ForgeConfig
from backend.core.session import ForgeSession
from backend.graph.engine import ProjectGraph
from backend.server.dependencies import (
    get_forge_config,
    get_forge_session,
    get_project_graph,
)

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _build_tree(root: Path, max_depth: int = 4, current_depth: int = 0) -> dict[str, Any]:
    """Recursively build a file tree dict."""
    if not root.exists():
        return {"name": root.name, "type": "missing", "children": []}
    if current_depth >= max_depth or not root.is_dir():
        return {"name": root.name, "type": "file" if root.is_file() else "directory"}
    children = []
    try:
        for child in sorted(root.iterdir()):
            if child.name.startswith(".") or child.name in {"__pycache__", "node_modules"}:
                continue
            children.append(_build_tree(child, max_depth, current_depth + 1))
    except PermissionError:
        pass
    return {"name": root.name, "type": "directory", "children": children}


@router.get("/tree")
async def get_workspace_tree(
    depth: int = 3,
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, Any]:
    """Return the workspace file tree."""
    workspace_root = Path(session.workspace_root)
    return {
        "root": str(workspace_root),
        "tree": _build_tree(workspace_root, max_depth=min(depth, 6)),
    }


@router.get("/functions")
async def get_workspace_functions(
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, Any]:
    """Parse all .py files in src/ and tests/ and return function listings.

    Uses the trace parser's AST analysis so the frontend can display
    every function regardless of whether trace sync has been run.
    """
    from backend.crew.trace_parser import analyse_traces  # noqa: PLC0415

    workspace = Path(session.workspace_root)
    result: dict[str, Any] = {}

    for subdir in ("src", "tests"):
        directory = workspace / subdir
        if not directory.is_dir():
            continue
        for py_file in sorted(directory.rglob("*.py")):
            if py_file.name in ("__init__.py", "conftest.py"):
                continue
            rel = str(py_file.relative_to(workspace))
            try:
                code = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            analysis = analyse_traces(code)
            result[rel] = {
                "functions": [
                    {"name": f.name, "start": f.start, "end": f.end,
                     "is_private": f.is_private, "class_name": f.class_name}
                    for f in analysis.untraced
                ] + [
                    {"name": t.symbol, "start": t.start, "end": t.end,
                     "is_private": False, "class_name": t.class_name}
                    for t in analysis.traces if t.symbol
                ],
                "traces": [
                    {
                        "start": t.start, "end": t.end,
                        "llr_ids": t.llr_ids, "symbol": t.symbol,
                        "case_ids": t.case_ids, "class_name": t.class_name,
                    }
                    for t in analysis.traces
                ],
                "total_functions": analysis.total_functions,
                "traced_functions": analysis.traced_functions,
            }

    return result


class FileBody(BaseModel):
    """Request body for saving a workspace file."""

    content: str


@router.put("/file")
async def save_workspace_file(
    path: str,
    body: FileBody,
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, Any]:
    """Save content to a file within the workspace."""
    if not path:
        raise HTTPException(status_code=400, detail="path query parameter is required.")

    workspace_root = Path(session.workspace_root).resolve()
    target = (workspace_root / path).resolve()

    if not str(target).startswith(str(workspace_root)):
        raise HTTPException(status_code=400, detail="Path is outside the workspace root.")

    if not target.parent.exists():
        raise HTTPException(status_code=400, detail="Parent directory does not exist.")

    async with aiofiles.open(target, "w", encoding="utf-8") as f:
        await f.write(body.content)

    return {"status": "saved", "path": path, "size": len(body.content)}


@router.get("/file", response_class=PlainTextResponse)
async def get_workspace_file(
    path: str,
    session: ForgeSession = Depends(get_forge_session),
) -> str:
    """Return the raw text content of a file within the workspace."""
    if not path:
        raise HTTPException(status_code=400, detail="path query parameter is required.")

    workspace_root = Path(session.workspace_root).resolve()
    target = (workspace_root / path).resolve()

    # Prevent path traversal outside the workspace.
    if not str(target).startswith(str(workspace_root)):
        raise HTTPException(status_code=400, detail="Path is outside the workspace root.")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Refuse obviously binary extensions without attempting to read.
    _binary_suffixes = {
        ".pyc",
        ".pyd",
        ".so",
        ".dll",
        ".exe",
        ".bin",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".rar",
        ".pdf",
        ".docx",
        ".xlsx",
        ".db",
        ".sqlite",
        ".sqlite3",
    }
    if target.suffix.lower() in _binary_suffixes:
        raise HTTPException(
            status_code=415,
            detail=f"Binary file type '{target.suffix}' cannot be displayed as text.",
        )

    async with aiofiles.open(target, encoding="utf-8", errors="replace") as f:
        content: str = await f.read()
    return content


# ─── Forge.md endpoints ───────────────────────────────────────────────────────


async def _parse_forgemd(
    content: str,
    graph: ProjectGraph,
    config: ForgeConfig,
    changed_by: str = "engineer",
) -> dict[str, Any]:
    """Parse forge.md content into graph nodes. Returns parse result counts."""
    from backend.graph.parsers.document import DocumentParser

    llm_cfg = getattr(config, "llm", None)
    parser = DocumentParser(llm_config=llm_cfg)
    parse_result = await parser.parse(
        document_slug="forgemd",
        content=content,
        graph=graph,
        changed_by=changed_by,
    )
    return {"parsed": True, "created": len(parse_result.created), "updated": len(parse_result.updated)}


@router.post("/forgemd")
async def upload_forgemd(
    file: UploadFile,
    config: ForgeConfig = Depends(get_forge_config),
) -> dict[str, Any]:
    """Upload a forge.md file and write it to disk.

    Only writes the file — no LLM calls.  Phase 1 (Run Phase) creates the
    DOCUMENT node; Phase 2 handles chunking into PARA nodes.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    content = raw.decode("utf-8", errors="replace")

    # Write to disk using the configured forgemd filename
    workspace = Path(config.project.workspace_dir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    dest = workspace / config.project.forgemd
    async with aiofiles.open(dest, "w", encoding="utf-8") as f:
        await f.write(content)

    return {"status": "ok", "size": len(raw), "path": str(dest)}


@router.get("/forgemd", response_class=PlainTextResponse)
async def get_forgemd(
    graph: ProjectGraph = Depends(get_project_graph),
) -> str:
    """Return the current forge.md content from the graph node."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available.")
    doc = await graph.find_node_by_slug("forgemd")
    if doc is None:
        return ""  # No forge.md yet — empty editor is the correct state
    return doc.content or ""


class ForgemdBody(BaseModel):
    """Request body for saving forge.md content via the PUT endpoint."""

    content: str


@router.put("/forgemd")
async def save_forgemd(
    body: ForgemdBody,
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
) -> dict[str, Any]:
    """Save edited forge.md content directly into the graph and re-parse."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available.")

    result: dict[str, Any] = {"status": "saved"}

    if config is not None:
        result.update(await _parse_forgemd(body.content, graph, config))

    return result


# ─── Tests summary ────────────────────────────────────────────────────────────


@router.get("/tests/summary")
async def get_tests_summary(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return the test suite summary derived from RESULT nodes in the graph."""
    if graph is None:
        return {
            "total": 0, "passed": 0, "failed": 0, "skipped": 0,
            "coverage_percent": None, "last_run": None, "status": "not_started",
        }

    from backend.graph.models import NodeType
    all_nodes = graph.all_nodes()
    cases = [n for n in all_nodes if n.node_type in (NodeType.CASE_HLR.value, NodeType.CASE_LLR.value)]
    results = [n for n in all_nodes if n.node_type == NodeType.RESULT.value]

    total = len(cases)
    passed = sum(1 for r in results if "pass" in (r.content or "").lower())
    failed = sum(1 for r in results if "fail" in (r.content or "").lower())
    skipped = sum(1 for r in results if "skip" in (r.content or "").lower())
    last_run = max((r.updated_at for r in results), default=None)

    if total == 0:
        status = "not_started"
    elif failed > 0:
        status = "failed"
    elif passed == total:
        status = "passed"
    else:
        status = "running"

    # Coverage: read from DESIGN node properties (persisted during Phase 12)
    coverage_pct, branch_pct = _read_coverage_from_graph(all_nodes)

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "coverage_percent": coverage_pct,
        "branch_coverage_percent": branch_pct,
        "last_run": last_run.isoformat() if last_run else None,
        "status": status,
    }


def _read_coverage_from_graph(
    all_nodes: list[Any],
) -> tuple[float | None, float | None]:
    """Read statement and branch coverage from DESIGN node properties."""
    for node in all_nodes:
        if node.node_type != "DESIGN":
            continue
        props = node.properties or {}
        if "statement_coverage" in props:
            return (
                props.get("statement_coverage"),
                props.get("branch_coverage"),
            )
    return None, None


# ─── Deliverables endpoints ──────────────────────────────────────────────────


@router.get("/deliverables/download")
async def download_deliverables(
    session: ForgeSession = Depends(get_forge_session),
) -> FileResponse:
    """Serve the deliverables ZIP archive for download."""
    workspace = Path(session.workspace_root)
    zip_path = workspace / "deliverables.zip"
    if not zip_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Deliverables pack not found — run Phase 14 first.",
        )
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="deliverables.zip",
    )


@router.get("/deliverables/manifest")
async def deliverables_manifest(
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, Any]:
    """Return the file manifest of the deliverables pack."""
    workspace = Path(session.workspace_root)
    deliv_dir = workspace / "deliverables"
    if not deliv_dir.exists():
        return {"exists": False, "files": []}

    files: list[dict[str, Any]] = []
    for path in sorted(deliv_dir.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(deliv_dir))
            files.append({"path": rel, "size": path.stat().st_size})

    return {"exists": True, "files": files}
