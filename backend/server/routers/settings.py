"""Settings router — read and update ForgeConfig stored in the project SQLite database."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from backend.config.loader import save_config
from backend.config.models import ForgeConfig
from backend.server.dependencies import get_config_path, get_forge_config

router = APIRouter(prefix="/settings", tags=["settings"])


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *patch* into *base*, returning a new dict."""
    result = {**base}
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@router.get("", response_model=None)
async def get_settings(
    config: ForgeConfig = Depends(get_forge_config),
) -> dict[str, Any]:
    """Return the current ForgeConfig as a JSON-serialisable dict."""
    if config is None:
        return ForgeConfig().model_dump(mode="json")
    return config.model_dump(mode="json")


@router.patch("", response_model=None)
async def patch_settings(
    body: dict[str, Any],
    request: Request,
    config: ForgeConfig = Depends(get_forge_config),
    config_path: Path | None = Depends(get_config_path),
) -> dict[str, Any]:
    """Deep-merge *body* into the current config, persist to the project database, and return the result."""
    base = (config or ForgeConfig()).model_dump(mode="json")
    merged = _deep_merge(base, body)

    try:
        new_config = ForgeConfig.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    if config_path is not None:
        save_config(new_config, config_path)

    # Live-update the app state so subsequent requests see the new config
    request.app.state.config = new_config

    # Rebuild agents and refresh tools if LLM settings changed
    if "llm" in body:
        pool = getattr(request.app.state, "agent_pool", None)
        if pool is not None:
            pool.rebuild(config=new_config)

        # Push new LLM config to analysis tools (derive_requirement, etc.)
        tool_registry = getattr(request.app.state, "tool_registry", None)
        if tool_registry is not None:
            tool_registry.update_llm_config(new_config.llm)

        # Update the global LLM call throttle
        from backend.agents.throttle import llm_throttle
        llm_throttle.delay_ms = int(new_config.llm.call_delay_ms)

    from backend.server.forge_logger import forge_logger
    sections = ", ".join(sorted(body.keys())) or "—"
    forge_logger.user_action("update settings", f"sections changed: {sections}")

    return new_config.model_dump(mode="json")
