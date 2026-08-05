"""WorkQueueService — system-wide prioritized task queue with action history.

Singleton service (like forge_logger) that manages a visible, prioritized
list of work items. Any agent in any phase can add, remove, or promote
items via LangChain tools. Each mutation broadcasts the full queue state
via WebSocket so the frontend renders updates in real time.

An ActionHistory tracks every fix attempt to prevent thrashing — the system
never repeats a failed approach.

Design reference: design/01_architecture.md §10
"""

from __future__ import annotations

import threading
from typing import Any

from backend.server.forge_logger import forge_logger

# ── Data types ───────────────────────────────────────────────────────────────


#: Most recent action records retained (see WorkQueueService.record_action).
_MAX_HISTORY = 200

URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
IMPORTANCE_ORDER = {"high": 0, "medium": 1, "low": 2}
EFFORT_ORDER = {"low": 0, "medium": 1, "high": 2}


class WorkItem:
    """A single item in the work queue.

    Prioritised using two Eisenhower dimensions:
    - **urgency**: how soon must this be done (critical/high/medium/low)
    - **importance**: how much does it matter (high/medium/low)

    Sort order: low-effort items first within the same urgency band
    (fix the easy wins to reduce noise), then by urgency, then importance.
    """

    __slots__ = (
        "id",
        "phase",
        "urgency",
        "importance",
        "category",
        "description",
        "target",
        "affected_files",
        "effort",
        "rationale",
        "status",
    )

    def __init__(
        self,
        *,
        id: str,
        phase: int,
        urgency: str = "medium",
        importance: str = "medium",
        category: str,
        description: str,
        target: str = "",
        affected_files: list[str] | None = None,
        effort: str = "medium",
        rationale: str = "",
        status: str = "pending",
    ) -> None:
        self.id = id
        self.phase = phase
        self.urgency = urgency if urgency in URGENCY_ORDER else "medium"
        self.importance = importance if importance in IMPORTANCE_ORDER else "medium"
        self.category = category
        self.description = description
        self.target = target
        self.affected_files = affected_files or []
        self.effort = effort if effort in EFFORT_ORDER else "medium"
        self.rationale = rationale
        self.status = status

    @property
    def sort_key(self) -> tuple[int, int, int, int]:
        """Sort: effort first (easy wins), then urgency, then importance, then FIFO."""
        eff = EFFORT_ORDER.get(self.effort, 1)
        urg = URGENCY_ORDER.get(self.urgency, 2)
        imp = IMPORTANCE_ORDER.get(self.importance, 1)
        try:
            num = int(self.id.split("-")[1])
        except (IndexError, ValueError):
            num = 9999
        return (eff, urg, imp, num)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phase": self.phase,
            "urgency": self.urgency,
            "importance": self.importance,
            "category": self.category,
            "description": self.description,
            "target": self.target,
            "affected_files": self.affected_files,
            "effort": self.effort,
            "rationale": self.rationale,
            "status": self.status,
        }


class ActionRecord:
    """Record of one fix attempt — what was tried and what happened."""

    __slots__ = (
        "round",
        "work_item_id",
        "phase",
        "category",
        "files_modified",
        "tool_calls",
        "gap_count_before",
        "gap_count_after",
        "outcome",
        "summary",
    )

    def __init__(
        self,
        *,
        round: int,
        work_item_id: str,
        phase: int,
        category: str,
        files_modified: list[str] | None = None,
        tool_calls: int = 0,
        gap_count_before: int = 0,
        gap_count_after: int = 0,
        outcome: str = "no_change",
        summary: str = "",
    ) -> None:
        self.round = round
        self.work_item_id = work_item_id
        self.phase = phase
        self.category = category
        self.files_modified = files_modified or []
        self.tool_calls = tool_calls
        self.gap_count_before = gap_count_before
        self.gap_count_after = gap_count_after
        self.outcome = outcome
        self.summary = summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "work_item_id": self.work_item_id,
            "phase": self.phase,
            "category": self.category,
            "files_modified": self.files_modified,
            "tool_calls": self.tool_calls,
            "gap_count_before": self.gap_count_before,
            "gap_count_after": self.gap_count_after,
            "outcome": self.outcome,
            "summary": self.summary,
        }


# ── Service ──────────────────────────────────────────────────────────────────



class WorkQueueService:
    """System-wide work queue — any agent can add/modify items."""

    def __init__(self) -> None:
        self._items: list[WorkItem] = []
        self._history: list[ActionRecord] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._ws_manager: Any | None = None

    def initialise(self, ws_manager: Any) -> None:
        """Register the WebSocket manager for broadcasting."""
        self._ws_manager = ws_manager

    # ── Queue operations ─────────────────────────────────────────────

    def add(
        self,
        *,
        phase: int,
        category: str,
        description: str,
        target: str = "",
        affected_files: list[str] | None = None,
        effort: str = "medium",
        urgency: str = "medium",
        importance: str = "medium",
        rationale: str = "",
    ) -> str:
        """Add an item to the queue. Returns the auto-generated ID."""
        with self._lock:
            self._counter += 1
            item_id = f"wq-{self._counter:03d}"
            item = WorkItem(
                id=item_id,
                phase=phase,
                urgency=urgency,
                importance=importance,
                category=category,
                description=description,
                target=target,
                affected_files=affected_files,
                effort=effort,
                rationale=rationale,
            )
            self._items.append(item)
            self._sort()

        forge_logger.emit(
            "INFO",
            "QUEUE",
            f"+ {item_id}: [{effort}] {category} — {description[:80]}",
            item_id=item_id,
            category=category,
            effort=effort,
            phase=phase,
            queue_depth=len(self._items),
        )
        self.broadcast()
        return item_id

    def remove(self, item_id: str) -> bool:
        """Remove an item by ID. Returns True if found."""
        with self._lock:
            before = len(self._items)
            self._items = [i for i in self._items if i.id != item_id]
            if len(self._items) == before:
                return False
            self._sort()
            depth = len(self._items)

        forge_logger.emit(
            "INFO", "QUEUE", f"- {item_id} removed",
            item_id=item_id, queue_depth=depth,
        )
        self.broadcast()
        return True

    def promote(
        self,
        item_id: str,
        urgency: str | None = None,
        importance: str | None = None,
    ) -> bool:
        """Change an item's urgency or importance. Returns True if found."""
        with self._lock:
            idx = self._find_index(item_id)
            if idx is None:
                return False
            item = self._items[idx]
            changes: list[str] = []
            if urgency and urgency in URGENCY_ORDER:
                changes.append(f"urgency: {item.urgency}→{urgency}")
                item.urgency = urgency
            if importance and importance in IMPORTANCE_ORDER:
                changes.append(f"importance: {item.importance}→{importance}")
                item.importance = importance
            if not changes:
                return False
            self._sort()

        forge_logger.emit(
            "INFO",
            "QUEUE",
            f"↑ {item_id} {', '.join(changes)}",
        )
        self.broadcast()
        return True

    def update_status(self, item_id: str, status: str) -> None:
        """Change an item's status and broadcast."""
        with self._lock:
            idx = self._find_index(item_id)
            if idx is None:
                return
            self._items[idx].status = status

        label = {"in_progress": "▶", "done": "✓", "failed": "✗"}.get(status, "·")
        forge_logger.emit("INFO", "QUEUE", f"{label} {item_id} → {status}")
        self.broadcast()

    def record_action(self, record: ActionRecord) -> None:
        """Append an action record to history and broadcast."""
        with self._lock:
            self._history.append(record)
            # Bounded: history is append-only across the whole run and every
            # broadcast serialises all of it, so an unbounded list makes
            # broadcasting quadratic in the number of dispatches. The window is
            # far larger than the trailing streak `category_failure_count` needs
            # and than the Control Station panel displays.
            if len(self._history) > _MAX_HISTORY:
                del self._history[:-_MAX_HISTORY]

        forge_logger.emit(
            "INFO",
            "QUEUE",
            f"Action: {record.category} → {record.outcome} "
            f"({record.gap_count_before}→{record.gap_count_after} gaps)",
        )
        self.broadcast()

    def clear_phase(self, phase: int) -> None:
        """Clear the pending work items for a phase. History is preserved.

        ``collect_gaps`` calls this at the start of every batch to rebuild the
        queue from the current gap list. It used to drop the phase's history
        too, which made history unable to outlive a single batch — so
        ``category_failure_count``, whose whole job is counting *trailing*
        failures across attempts, could never see past the current one. History
        is an append-only record of what was tried; only the queue is a
        rebuildable view of what is outstanding.
        """
        with self._lock:
            self._items = [i for i in self._items if i.phase != phase]
            self._sort()

        forge_logger.emit("INFO", "QUEUE", f"Cleared phase {phase}")
        self.broadcast()

    # ── Queries ──────────────────────────────────────────────────────

    def next_pending(self, phase: int) -> WorkItem | None:
        """Return the highest-priority pending item for a phase."""
        with self._lock:
            for item in self._items:
                if item.phase == phase and item.status == "pending":
                    return item
        return None

    def items_for_phase(self, phase: int) -> list[WorkItem]:
        """Return all items for a phase, ordered by priority."""
        with self._lock:
            return [i for i in self._items if i.phase == phase]

    def history_for_category(self, category: str) -> list[ActionRecord]:
        """Return action records for a specific category."""
        with self._lock:
            return [h for h in self._history if h.category == category]

    def category_failure_count(self, category: str) -> int:
        """Count trailing consecutive failures for a category.

        Reporting only. Retry throttling lives in the structural loop's own
        circuit breaker (``_MAX_GAP_ATTEMPTS``), which is per-gap rather than
        per-category; a second competing throttle here would make the two
        interact unpredictably.

        Counts backwards from the most recent attempt — resets on any success.
        """
        with self._lock:
            count = 0
            for h in reversed(self._history):
                if h.category != category:
                    continue
                if h.outcome in ("no_change", "worse"):
                    count += 1
                else:
                    break  # success resets the streak
            return count

    @property
    def all_items(self) -> list[dict[str, Any]]:
        """Return all items as dicts (for serialisation)."""
        with self._lock:
            return [i.to_dict() for i in self._items]

    @property
    def all_history(self) -> list[dict[str, Any]]:
        """Return all history as dicts (for serialisation)."""
        with self._lock:
            return [h.to_dict() for h in self._history]

    # ── Broadcasting ─────────────────────────────────────────────────

    def broadcast(self) -> None:
        """Send full queue + history state via WebSocket."""
        if self._ws_manager is None:
            return

        from backend.server.websocket.events import WSEvent, WSEventType

        event = WSEvent(
            event_type=WSEventType.WORK_QUEUE,
            payload={
                "items": self.all_items,
                "history": self.all_history,
            },
        )
        self._ws_manager.broadcast_threadsafe(event)

    # ── Internal ─────────────────────────────────────────────────────

    def _find_index(self, item_id: str) -> int | None:
        for i, item in enumerate(self._items):
            if item.id == item_id:
                return i
        return None

    def _sort(self) -> None:
        """Sort items by priority level (critical first), then FIFO within level."""
        self._items.sort(key=lambda item: item.sort_key)


# Module-level singleton — import and use directly.
work_queue = WorkQueueService()
