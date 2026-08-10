.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
TF_DIR := terraform
ENV ?= prod

.PHONY: help venv install lint fmt test coverage build clean tf-init tf-fmt tf-validate tf-plan tf-apply check teardown teardown-plan

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the local virtualenv
	python3 -m venv $(VENV)

install: venv ## Install development dependencies
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements-dev.txt

lint: ## Run ruff checks
	$(VENV)/bin/ruff check lambda tests
	$(VENV)/bin/ruff format --check lambda tests

fmt: ## Auto-format and fix lint issues
	$(VENV)/bin/ruff check --fix lambda tests
	$(VENV)/bin/ruff format lambda tests

test: ## Run the test suite
	$(VENV)/bin/pytest

coverage: ## Run tests with a coverage report
	$(VENV)/bin/pytest --cov --cov-report=term-missing

build: ## Build the Lambda deployment zip
	./scripts/build_lambda.sh

check: lint test ## Lint and test

tf-init: ## terraform init
	terraform -chdir=$(TF_DIR) init

tf-fmt: ## terraform fmt (recursive)
	terraform fmt -recursive $(TF_DIR)

tf-validate: ## terraform validate
	terraform -chdir=$(TF_DIR) validate

tf-plan: build ## terraform plan
	terraform -chdir=$(TF_DIR) plan -out=tfplan

tf-apply: ## terraform apply the saved plan
	terraform -chdir=$(TF_DIR) apply tfplan

teardown-plan: ## Show what a teardown would destroy (changes nothing)
	./scripts/teardown.sh $(ENV) --plan

teardown: ## Destroy the deployed stack (prompts for confirmation)
	./scripts/teardown.sh $(ENV)

clean: ## Remove local build and cache artifacts
	rm -rf build .pytest_cache .ruff_cache .coverage htmlcov coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
