#!/usr/bin/env bash
set -euo pipefail

echo "Stopping Forge servers..."

fuser -k 7340/tcp 2>/dev/null && echo "  backend (7340) stopped" || echo "  backend (7340) not running"
fuser -k 5173/tcp 2>/dev/null && echo "  frontend (5173) stopped" || echo "  frontend (5173) not running"
