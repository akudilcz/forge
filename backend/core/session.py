"""ForgeSession — lightweight project metadata for the active build session.

Phase states are stored in the DB via PhaseStore (backend/core/phase_store.py),
not here. This model holds only the project-level config needed by the UI and
by REST endpoints that need to know workspace paths.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ForgeSession(BaseModel):
    """Lightweight project metadata. Phase states live in the DB."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_name: str
    forgemd_path: str
    workspace_root: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        project_name: str,
        forgemd_path: str = "forge.md",
        workspace_root: str = ".",
    ) -> ForgeSession:
        """Create a new ForgeSession with auto-generated session_id and timestamp."""
        return cls(
            project_name=project_name,
            forgemd_path=forgemd_path,
            workspace_root=workspace_root,
        )
