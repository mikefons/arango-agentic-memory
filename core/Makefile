# Core dev tasks. Bakes in two env settings so every command is robust:
#   UV_PROJECT_ENVIRONMENT -> keep the venv OUTSIDE iCloud-synced Documents
#                             (iCloud creates "name 2.ext" copies that corrupt .venv)
#   PYTHONPATH=src         -> import the package regardless of editable-install state
#
# For ad-hoc `uv` commands outside make, export the same:
#   export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/arango-memory"
export UV_PROJECT_ENVIRONMENT := $(HOME)/.venvs/arango-memory
export PYTHONPATH := src

UVRUN := uv run --no-sync

.PHONY: sync dev check test lint type ci clean-venv benchmark handoff-eval

# LoCoMo benchmark (§23). Override DATASET/MODE/K; needs a running ArangoDB and, for a
# real run, real providers (EMBEDDING_PROVIDER=openai GENERATION_PROVIDER=anthropic + keys).
DATASET ?= tests/data/locomo_smoke.json
MODE    ?= lite
K       ?= 10

sync:            ## Install/update deps into the relocated venv
	uv sync --extra dev

dev:             ## Run the core API with autoreload
	$(UVRUN) uvicorn arango_memory.api.app:app --reload --port 8080

check:           ## Connection/round-trip probe (set ARANGO_TARGET for cloud)
	$(UVRUN) python -m arango_memory.check

test:            ## Run the test suite
	$(UVRUN) pytest

lint:            ## Ruff lint
	$(UVRUN) ruff check .

type:            ## Mypy type-check
	$(UVRUN) mypy src

ci: lint type test  ## Everything CI runs

benchmark:       ## LoCoMo benchmark vs §23 (DATASET=… MODE=lite|full K=…)
	$(UVRUN) python -m arango_memory.eval.benchmark $(DATASET) --mode $(MODE) --k $(K)

HANDOFF_DATASET ?= tests/data/handoff_smoke.json
handoff-eval:    ## Multi-agent handoff eval vs §23 (MA-5; HANDOFF_DATASET=…)
	$(UVRUN) python -m arango_memory.eval.handoff $(HANDOFF_DATASET)

clean-venv:      ## Rebuild the relocated venv from scratch
	rm -rf "$(UV_PROJECT_ENVIRONMENT)" && uv sync --extra dev
