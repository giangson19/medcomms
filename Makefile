.PHONY: help install dev dev-light test lint format check build clean

help:
	@echo "make install    install medcomms with dev tools (editable)"
	@echo "make dev        run everything locally on http://localhost:8000"
	@echo "make dev-light  same, with tiny models (fast; for work on the page)"
	@echo "make test       run tests with coverage"
	@echo "make lint       lint and check formatting"
	@echo "make format     format the code"
	@echo "make check      lint + test"
	@echo "make build      build a wheel and sdist"
	@echo "make clean      remove build and test artifacts"

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

dev:
	medcomms dev

dev-light:
	medcomms dev --light

test:
	coverage run -m pytest -rs
	coverage combine
	coverage report

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

check: lint test

build: clean
	python -m build

clean:
	rm -rf build/ dist/ *.egg-info/ .coverage .coverage.* coverage.xml htmlcov/ .pytest_cache/ .ruff_cache/
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
