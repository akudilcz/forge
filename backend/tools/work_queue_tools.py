"""LangChain tools for managing the system-wide work queue.

Agents call these tools to add, remove, or promote work items.
Each tool wraps the WorkQueueService singleton.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _QueueTool(ForgeTool):
    """Base for work-queue tools — adapts ForgeTool's untyped entry point.

    ``name``/``description`` are re-declared with defaults because every
    concrete tool below supplies them as class-level field defaults;
    ``BaseTool`` declares them as required construction fields, which a bare
    ``super().__init__()`` cannot satisfy statically.

    ``_execute`` receives whatever keyword arguments the LLM produced for the
    tool's ``args_schema`` and forwards them to ``_invoke``, which each tool
    declares with its real, schema-matching signature.
    """

    name: str = ""
    description: str = ""

    def _execute(self, **kwargs: Any) -> str:
        return self._invoke(**kwargs)

    def _invoke(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError


class _AddArgs(BaseModel):
    category: str = Field(description="Root cause category (e.g. missing_import, api_mismatch)")
    description: str = Field(description="One-sentence description of the work item")
    target: str = Field(default="", description="Specific file or node ID to fix")
    affected_files: list[str] = Field(default_factory=list, description="Files that need changes")
    effort: str = Field(default="medium", description="Estimated effort: low, medium, or high")
    urgency: str = Field(default="medium", description="How soon: critical, high, medium, or low")
    importance: str = Field(default="medium", description="How much it matters: high, medium, or low")
    rationale: str = Field(default="", description="Why this priority — what evidence supports it")


class QueueAddTool(_QueueTool):
    """Add a work item to the prioritized queue."""

    name: str = "queue_add"
    description: str = (
        "Add a work item to the priority queue. Use this to queue fixes you've "
        "identified. Each item should be a single, specific fix — e.g. one file "
        "that needs an import added. The item appears in the UI immediately."
    )
    args_schema: type[BaseModel] = _AddArgs

    _phase: int = 0

    def __init__(self, phase: int = 0) -> None:
        super().__init__()
        object.__setattr__(self, "_phase", phase)

    def _invoke(
        self,
        category: str,
        description: str,
        target: str = "",
        affected_files: list[str] | None = None,
        effort: str = "medium",
        urgency: str = "medium",
        importance: str = "medium",
        rationale: str = "",
    ) -> str:
        from backend.work_queue import work_queue

        if effort not in ("low", "medium", "high"):
            return f"ERROR: effort must be low, medium, or high (got '{effort}')"

        item_id = work_queue.add(
            phase=self._phase,
            category=category,
            description=description,
            target=target,
            affected_files=affected_files or [],
            effort=effort,
            urgency=urgency,
            importance=importance,
            rationale=rationale,
        )
        return f"OK: added {item_id} — {category}: {description[:60]}"


class _RemoveArgs(BaseModel):
    item_id: str = Field(description="ID of the work item to remove (e.g. wq-001)")


class QueueRemoveTool(_QueueTool):
    """Remove a work item from the queue."""

    name: str = "queue_remove"
    description: str = (
        "Remove a work item from the queue by ID. Use this when you determine "
        "an item is no longer needed or was added in error."
    )
    args_schema: type[BaseModel] = _RemoveArgs

    def _invoke(self, item_id: str) -> str:
        from backend.work_queue import work_queue

        if work_queue.remove(item_id):
            return f"OK: removed {item_id}"
        return f"ERROR: item {item_id} not found"


class _PromoteArgs(BaseModel):
    item_id: str = Field(description="ID of the work item (e.g. wq-003)")
    urgency: str = Field(default="", description="New urgency: critical, high, medium, or low")
    importance: str = Field(default="", description="New importance: high, medium, or low")


class QueuePromoteTool(_QueueTool):
    """Change a work item's urgency or importance."""

    name: str = "queue_promote"
    description: str = (
        "Change a work item's urgency (critical/high/medium/low) or "
        "importance (high/medium/low). The queue auto-sorts "
        "by effort first (easy wins), then urgency, then importance."
    )
    args_schema: type[BaseModel] = _PromoteArgs

    def _invoke(
        self, item_id: str,
        urgency: str = "", importance: str = "",
    ) -> str:
        from backend.work_queue import work_queue

        if work_queue.promote(
            item_id,
            urgency=urgency or None,
            importance=importance or None,
        ):
            parts = []
            if urgency:
                parts.append(f"urgency={urgency}")
            if importance:
                parts.append(f"importance={importance}")
            return f"OK: {item_id} updated — {', '.join(parts)}"
        return f"ERROR: item {item_id} not found or no valid changes"
