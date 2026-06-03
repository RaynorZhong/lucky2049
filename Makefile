PYTHON ?= ./.venv/bin/python

.PHONY: help install-dev test watch cov

help:
	@echo "make install-dev  - install runtime + test tooling into the venv"
	@echo "make test         - run the test suite once (pytest)"
	@echo "make watch        - re-run tests on every save (TDD red-green loop)"
	@echo "make cov          - run tests with a coverage report"

install-dev:
	$(PYTHON) -m pip install -r requirements.txt -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

# TDD loop: watches the tree and re-runs pytest on each change.
watch:
	$(PYTHON) -m pytest_watcher . --now -- -q

cov:
	$(PYTHON) -m pytest --cov --cov-report=term-missing
