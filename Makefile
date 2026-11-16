# broadmind-eeg - common developer tasks.
#
# Quick start (inside an activated virtualenv):
#   make install    editable install with dev extras (pytest, ruff)
#   make test       pytest suite - no hardware required
#   make lint       ruff over the whole repository
#   make build      build a wheel into dist/
#   make clean      remove build artefacts and caches
#
# To use the project virtualenv without activating it, override PYTHON:
#   make test PYTHON=.venv/bin/python

PYTHON ?= python3
DIST_DIR ?= dist
BUILD_DIR ?= build

.PHONY: install test lint build clean

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest tests/ -q

lint:
	$(PYTHON) -m ruff check .

build:
	$(PYTHON) -m pip wheel . --no-deps --wheel-dir $(DIST_DIR)

clean:
	rm -rf $(DIST_DIR) $(BUILD_DIR) *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
