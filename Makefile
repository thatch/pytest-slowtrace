.venv:
	uv sync --extra dev --extra test

.PHONY: setup
setup:
	uv sync --extra dev --extra test

.PHONY: test
test:
	uv run coverage run -m pytest $(TESTOPTS)
	uv run coverage report

.PHONY: format
format:
	uv run ruff format
	uv run ruff check --fix

.PHONY: lint
lint:
	uv run ruff check
	uv run python -m checkdeps --allow-names pytest_slowtrace src/pytest_slowtrace
	uv run mypy --strict --install-types --non-interactive src/pytest_slowtrace
	uv run ty check
