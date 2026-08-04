#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Load .env if it exists so secrets (e.g. POE_API_KEY) are available
# without needing them pre-exported in the shell environment.
if [ -f .env ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi

FORGE_ROOT="$(pwd)"
echo "Starting Forge backend on http://localhost:7340 ..."
echo "Watching for changes in: ${FORGE_ROOT}/backend (CWD-based, uvicorn always watches CWD)"
# uvicorn always watches CWD in addition to --reload-dir (see uvicorn supervisors/watchfilesreload.py:68-69)
# so we cd into backend/ and set PYTHONPATH so backend.* imports still resolve.
(cd "${FORGE_ROOT}/backend" && \
    env FORGE_DEV_MODE=1 FORGE_WORKSPACE="${FORGE_ROOT}" \
    PYTHONPATH="${FORGE_ROOT}" \
    uv run --project "${FORGE_ROOT}" uvicorn backend.server.app:create_app \
        --factory \
        --host localhost \
        --port 7340) &

echo "Starting Forge frontend on http://localhost:5173 ..."
(cd "$(dirname "$0")/frontend" && exec pnpm run dev) &

wait
