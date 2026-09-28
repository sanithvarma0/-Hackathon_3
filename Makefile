# MemoryOps — one entry point for every workflow (BUILD_PLAN.md 17.5).
.PHONY: setup dev backend frontend test lint format typecheck check spikes dryrun eval eval-quick usage clean

setup:            ## install backend + frontend dependencies
	uv sync
	cd frontend && npm ci

backend:          ## run the API on :8000 with reload
	uv run uvicorn backend.main:app --reload --port 8000

frontend:         ## run the dashboard on :3000
	cd frontend && npm run dev

dev:              ## run backend and frontend together
	$(MAKE) -j2 backend frontend

test:             ## backend pytest + frontend reducer tests (no network, no keys)
	uv run pytest
	cd frontend && npm test

lint:             ## ruff lint + format check, frontend eslint
	uv run ruff check .
	uv run ruff format --check .
	cd frontend && npm run lint

format:           ## auto-format Python
	uv run ruff format .
	uv run ruff check --fix .

typecheck:        ## mypy --strict on the backend, tsc on the frontend
	uv run mypy backend
	cd frontend && npm run typecheck

check: lint typecheck test  ## everything CI runs (except the frontend build)

spikes:           ## Phase 0.5 measurements against live services (needs .env)
	uv run python scripts/spike_groq.py
	uv run python scripts/spike_hindsight.py
	uv run python scripts/spike_recall_design.py sig

dryrun:           ## agent end to end on live services (needs .env; spends tokens)
	uv run python scripts/demo_dryrun.py

eval:             ## full evaluation suite on live services (needs .env; spends ~$10, ~1 h)
	uv run python scripts/run_eval.py

eval-quick:       ## 1 seed x 12 incidents smoke run of the evaluation suite
	uv run python scripts/run_eval.py --quick

usage:            ## LLM spend + token report from the usage ledger
	uv run python scripts/usage_report.py

clean:            ## remove local simulator data and caches
	rm -rf data/memoryops.db .pytest_cache .mypy_cache .ruff_cache  # keeps data/usage.db (spend history)
