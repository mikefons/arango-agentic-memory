# Contributing

Thanks for your interest in the ArangoDB Agentic Memory System. This guide covers
local setup, the checks your change must pass, and the PR workflow.

## Prerequisites

- **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) (core).
- **Node 22+** and `npm` (Vercel adapter + dungeon example).
- **Docker** — integration tests spin a real ArangoDB Enterprise container via
  [testcontainers](https://testcontainers.com/) (evaluation mode, no license).
- **gitleaks** + **pre-commit** for the secret-scanning hook.

## Setup

```bash
# Core (Python)
cd core
make sync                 # install deps into the relocated venv
make dev                  # run the API with autoreload on :8080  (optional)

# Secret-scanning hook (one-time per clone)
brew install gitleaks pre-commit   # or: pip install pre-commit
pre-commit install
```

> The venv is relocated outside the repo (iCloud-sync safety); the `Makefile` bakes in
> `UV_PROJECT_ENVIRONMENT` + `PYTHONPATH=src` so commands work regardless of the
> editable-install state. See [DESIGN.md §25](docs/DESIGN.md#25-development-tooling--infrastructure).

## Before you push — `make ci` must pass

CI runs the same thing on every PR; run it locally first:

```bash
cd core && make ci        # ruff (lint) + mypy --strict (types) + pytest (testcontainers)
```

- **Python:** `ruff` clean, `mypy --strict` clean, all tests green. Tests are keyless
  (deterministic fake embedder/generator/extractor) and isolated (a fresh DB per test).
- **Adapter:** `cd packages/vercel && npm run typecheck && npm run build && npm test`.
- New behavior needs tests; new config needs a doc line (ops.md/api.md as relevant).

## PR workflow

- **Branch → PR → squash-merge.** Never push directly to `main`.
- Keep PRs small and single-purpose; CI (core + adapter + dungeon + secret scan) must be
  green before merge.
- **Never commit secrets** (`.env`, keys, tokens). The gitleaks hook + CI secret scan
  guard this; keep credentials in the host env or a gitignored `.env`.
- Conventional, imperative commit subjects (e.g. `feat(cache): …`, `docs: …`).
- Update **`CHANGELOG.md`** (`[Unreleased]`) for user-visible changes, and bump the
  `docs/DESIGN.md` rev line if you touch the spec.

## Conventions

- Match the surrounding code's style, naming, and comment density. Comments explain
  *why*, not *what*.
- Validate at system boundaries (request bodies, external APIs); trust internal calls.
- The HTTP `/v1` contract is adapter-neutral — keep it that way (DESIGN §19); adapters
  stay thin.

## Where things live

| Path | What |
|---|---|
| `core/src/arango_memory/` | the Python core (ingest · retrieve · lifecycle · security · telemetry) |
| `packages/vercel/` | the TypeScript adapter |
| `docs/` | spec ([DESIGN](docs/DESIGN.md)), [API](docs/api.md), [ops](docs/ops.md), [adapters](docs/adapters/) |
| `examples/` | dungeon + vercel-agent reference apps |

Questions? Start with [docs/README.md](docs/README.md) for the doc map.
