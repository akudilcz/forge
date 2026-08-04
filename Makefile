.PHONY: dev-server dev-frontend build test test-unit test-integration lint format check install

dev-server:
	FORGE_DEV_MODE=1 uv run uvicorn backend.server.app:create_app --factory --reload --reload-include 'backend/**/*.py' --reload-exclude 'workspace' --reload-exclude '.forge' --reload-exclude 'frontend' --host localhost --port 7340

dev-frontend:
	cd frontend && pnpm run dev

build:
	cd frontend && pnpm run build
	uv build

install:
	uv sync
	cd frontend && pnpm install

test:
	uv run pytest backend/tests/ --ignore=backend/tests/integration/

test-unit:
	uv run pytest backend/tests/ --ignore=backend/tests/integration/

test-integration:
	uv run pytest backend/tests/integration/ -m integration -x -q

lint:
	uv run ruff check backend/ && uv run mypy backend/

format:
	uv run ruff format backend/ && uv run ruff check --fix backend/

check: lint test-unit
