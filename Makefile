# Common development commands
PY ?= .venv/bin/python
TAILWIND_VERSION ?= v4.3.3

.PHONY: setup dev test e2e lint format css tailwind migrate admin backup docker

setup:            ## Create the venv and install dependencies
	uv venv --python 3.12 .venv && uv pip install --python $(PY) -e ".[dev,postgres]"

tailwind:         ## Download the Tailwind standalone CLI into tools/
	@mkdir -p tools
	@os=$$(uname -s | tr A-Z a-z); arch=$$(uname -m); \
	case "$$os-$$arch" in \
	  darwin-arm64) f=macos-arm64 ;; darwin-x86_64) f=macos-x64 ;; \
	  linux-x86_64) f=linux-x64 ;; linux-aarch64|linux-arm64) f=linux-arm64 ;; *) echo "unsupported"; exit 1 ;; \
	esac; \
	curl -fsSL -o tools/tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/download/$(TAILWIND_VERSION)/tailwindcss-$$f && chmod +x tools/tailwindcss

css:              ## Build CSS
	tools/tailwindcss -i app/static/src/app.css -o app/static/css/app.css --minify

migrate:
	$(PY) -m app.cli migrate

dev: migrate      ## Run the app at http://127.0.0.1:8000
	$(PY) -m uvicorn app.main:create_app --factory --reload --port 8000

test:
	$(PY) -m pytest

e2e:              ## End-to-end tests in a real browser (Playwright; falls back to system Chrome)
	E2E=1 $(PY) -m pytest tests/e2e -q -p no:warnings

lint:             ## PEP 8 (ruff) and English-only comments/docstrings
	$(PY) -m ruff format --check app tests tools migrations
	$(PY) -m ruff check app tests tools migrations
	$(PY) tools/check_english.py

format:           ## Auto-format Python code (PEP 8, 79 columns)
	$(PY) -m ruff format app tests tools migrations
	$(PY) -m ruff check --fix app tests tools migrations

backup:
	$(PY) -m app.cli backup

docker:
	docker compose build
