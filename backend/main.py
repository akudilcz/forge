"""FORGE entry point — CLI and server launcher."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# The project .env must be loaded before importing anything that reads
# configuration at import time, so these imports deliberately follow it.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import click  # noqa: E402
import uvicorn  # noqa: E402
from rich.console import Console  # noqa: E402

from backend.config.loader import load_config  # noqa: E402

console = Console()


@click.group()
def cli() -> None:
    """FORGE — Agentic Software Build System."""


@cli.command("serve")
@click.option(
    "--workspace",
    "workspace_dir",
    default="/store/forge/workspace/",
    show_default=True,
    type=click.Path(),
    help="Path to the workspace directory.",
)
@click.option("--host", default=None, help="Override server host.")
@click.option("--port", default=None, type=int, help="Override server port.")
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="Enable hot-reload (development mode).",
)
def serve(
    workspace_dir: str,
    host: str | None,
    port: int | None,
    reload: bool,
) -> None:
    """Start the FORGE Control Station server."""
    workspace = Path(workspace_dir)
    db_path = workspace / ".forge" / "forge.db"
    config = load_config(str(db_path) if db_path.exists() else None)

    effective_host = host or config.server.host
    effective_port = port or config.server.port

    console.print(
        f"[bold cyan]FORGE[/bold cyan] Control Station starting on "
        f"[link=http://{effective_host}:{effective_port}]"
        f"http://{effective_host}:{effective_port}[/link]"
    )

    import logging

    # Filter out uvicorn access logs for cleaner trace
    class AccessLogFilter(logging.Filter):
        """Suppress high-frequency polling and static-asset log entries from uvicorn access logs."""

        def filter(self, record: logging.LogRecord) -> bool:
            """Return False for quiet polling endpoints so they are not emitted to the log."""
            msg = record.getMessage()
            # Filter out high-frequency polling and static assets
            quiet_endpoints = [
                "GET /api/v1/phases",
                "GET /api/v1/session",
                "GET /api/v1/graph/nodes",
                "GET /api/v1/agents",
                "GET /health",
                "GET /assets/",
                "GET /favicon.ico",
            ]
            return not any(endpoint in msg for endpoint in quiet_endpoints)

    logging.getLogger("uvicorn.access").addFilter(AccessLogFilter())

    # Pass workspace to the factory function via environment variable
    os.environ["FORGE_WORKSPACE"] = str(workspace)

    uvicorn.run(
        "backend.server.app:create_app",
        factory=True,
        host=effective_host,
        port=effective_port,
        reload=reload,
        reload_excludes=["workspace/*"] if reload else None,
        log_level="info",
    )


if __name__ == "__main__":
    cli()
