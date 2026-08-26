# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`pytest-slowtrace` is a pytest plugin that reports what a test was doing when it ran too long — surfacing that context rather than just flagging that a test was slow.

## Commands

- Install dependencies: `uv sync`
- Run the test suite: `uv run pytest`
- Run a single test: `uv run pytest tests/test_plugin.py::test_slow_test_is_reported`
- Run the plugin against a real test to see its own output: `uv run pytest --slowtrace-threshold=0.1 <path>`

## Architecture

The plugin is registered under the `pytest11` entry point (see `pyproject.toml`), pointing at `pytest_slowtrace.plugin`. Pytest discovers and loads it automatically in any environment where the package is installed — there is no explicit activation step.

`plugin.py` follows the standard pytest plugin-class pattern: `pytest_configure` builds one `SlowTracePlugin` instance per session and registers it with the plugin manager, so per-run state (the threshold, the list of slow reports) lives on that instance rather than in module globals. `pytest_runtest_logreport` fires once per test phase (`setup`/`call`/`teardown`); the plugin only looks at the `call` phase report to decide if a test exceeded `--slowtrace-threshold`. `pytest_terminal_summary` runs once at the end of the session and renders the collected slow-test reports.

Tests in `tests/` use pytest's built-in `pytester` fixture (enabled via `tests/conftest.py`) to run the plugin against a throwaway test file in a subprocess-like sandbox and assert on its terminal output — this is the standard way to test a pytest plugin's behavior end-to-end rather than unit-testing hook functions in isolation.
