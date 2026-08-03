SHELL := $(shell which bash) -e
.DEFAULT_GOAL := help
.PHONY: bootstrap start clean reset

VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip
SNAPSHOT_VERSION ?= 0.0.1.dev$(shell date -u +%Y%m%d%H%M%S)
SNAPSHOT_BUILD_VERSION = $(if $(version),$(version),$(SNAPSHOT_VERSION))

#*********************************
# Primary publicly exposed targets
#*********************************
help: ## This help screen
	@IFS=$$'\n' ;\
  help_lines=(`fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##/:/'`) ;\
  printf "%-30s %s\n" "Target" "Function" ;\
  printf "%-30s %s\n" "------" "----" ;\
  for help_line in $${help_lines[@]}; do \
      IFS=$$':' ;\
      help_split=($$help_line) ;\
      help_command=`echo $${help_split[0]} | sed -e 's/^ *//' -e 's/ *$$//'` ;\
      help_info=`echo $${help_split[2]} | sed -e 's/^ *//' -e 's/ *$$//'` ;\
      printf '\033[36m' ;\
      printf "%-30s %s" $$help_command ;\
      printf '\033[0m' ;\
      printf "%s\n" $$help_info ;\
  done

bootstrap: ## Bootstraps the Entigram Compiler dependencies and internal SQLite ledger
	@echo "Bootstrapping Entigram Compiler..."
	@if [ ! -d "$(VENV_DIR)" ]; then python3 -m venv $(VENV_DIR); fi
	@$(PIP) install --upgrade pip
	@$(PIP) install -e .
	@echo "Initializing internal SQLite state ledger..."
	@mkdir -p .etg
	@$(PYTHON) -c "import sys; from pathlib import Path; sys.path.append(str(Path.cwd())); from entigram.sqlite_ledger.manager import LedgerManager; LedgerManager('.etg/state.db')"
	@echo "Bootstrap complete. Entigram is ready."

cloudflare-ollama-proxy: ## Starts an Ollama-compatible proxy backed by Cloudflare Workers AI
	@echo "Starting Cloudflare-backed Ollama proxy on http://127.0.0.1:11435..."
	@$(PYTHON) -m entigram.cli_runner.etg_cli cloudflare-ollama-proxy

cloudflare-ollama-smoke: ## Verifies Cloudflare Workers AI auth using the local .env file
	@echo "Testing Cloudflare Workers AI auth..."
	@$(PYTHON) -m entigram.cli_runner.etg_cli cloudflare-ollama-proxy --smoke-test

cloudflare-claude: ## Starts a dynamic Cloudflare proxy and launches Claude Code through Ollama
	@echo "Launching Claude Code through Cloudflare Workers AI..."
	@$(PYTHON) -m entigram.cli_runner.etg_cli cloudflare-claude

agent-start: ## Prints portable agent boot instructions and compact hydration context
	@$(PYTHON) -m entigram.cli_runner.etg_cli agent start

test: ## Runs unit tests and Entigram-on-Entigram self-validation
	@echo "Running Entigram Test Suite..."
	@export PYTHONPATH=$${PYTHONPATH}:. && $(PYTHON) -m unittest discover tests

clean: ## Removes local compiled artifacts
	@echo "Cleaning up local build artifacts..."
	@rm -rf .etg-build

reset: ## Factory reset: Removes the Entigram engine venv and internal DB
reset: clean
	@echo "Resetting Entigram engine..."
	@rm -rf $(VENV_DIR)
	@rm -f .etg/state.db

handoff: ## Runs the immutable governance pre-handoff gate sequence
	@echo "Anchoring delivery state..."
	@$(PYTHON) -m entigram.cli_runner.etg_cli broker handoff
	@echo "Governance sequence complete. Ready to commit."

generate-agent: ## Scaffolds a new custom edge-agent pre-wired to the state ledger (Usage: make generate-agent name=my_custom_api)
	@if [ -z "$(name)" ]; then echo "Error: Must provide an agent name (e.g., make generate-agent name=stripe)"; exit 1; fi
	@echo "Bootstrapping custom agent: $(name)..."
	@$(PYTHON) entigram/cli_runner/agent_builder.py $(name)

snapshot: ## Builds a local PyPI distribution snapshot (Usage: make snapshot [version=0.0.1.dev1])
	@echo "Building local PyPI snapshot $(SNAPSHOT_BUILD_VERSION)..."
	@rm -rf dist/ build/ entigram_ai.egg-info/
	@if [ ! -d "$(VENV_DIR)" ]; then python3 -m venv $(VENV_DIR); fi
	@$(PIP) install build setuptools wheel
	@$(PYTHON) scripts/versioning.py build "$(SNAPSHOT_BUILD_VERSION)" -- $(PYTHON) -m build
	@echo "Snapshot created in ./dist/"

release: ## Tags the repository to trigger GitHub Actions release (Usage: make release version=0.0.1 or version=1.100.100)
	@if [ -z "$(version)" ]; then echo "Error: Must provide a version string (e.g., make release version=0.0.1)"; exit 1; fi
	@if [ ! -d "$(VENV_DIR)" ]; then python3 -m venv $(VENV_DIR); fi
	@$(PYTHON) scripts/versioning.py validate "$(version)"
	@echo "Tagging release v$(version) and pushing to origin..."
	@git tag v$(version)
	@git push origin v$(version)
	@echo "Release v$(version) triggered! Follow the progress in the GitHub Actions tab."
