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

`plugin.py` follows the standard pytest plugin-class pattern: `pytest_configure` builds one `SlowTracePlugin` instance per session and registers it with the plugin manager, so per-run state (the threshold, per-test overrides, CPU times, the list of slow reports) lives on that instance rather than in module globals.

The threshold a test is judged against isn't just `--slowtrace-threshold`: `pytest_collection_modifyitems` walks collected items once, up front, and records any `@pytest.mark.slowtrace(seconds=...)` override into `self.overrides` keyed by `nodeid`, so a marked test can demand a stricter (or looser) threshold than the session default.

`pytest_runtest_call` is a generator-style hook wrapper (`@pytest.hookimpl(wrapper=True)`) around the actual test call — it brackets the call with `time.process_time()` to record how much CPU (not wall-clock) time the test consumed, keyed by `nodeid` in `self.cpu_times`. `pytest_runtest_logreport` fires once per test phase (`setup`/`call`/`teardown`); the plugin only looks at the `call` phase report, compares `report.duration` against the effective threshold (override or default), and if it qualifies, appends `(report, cpu_time)` to `self.slow_reports`. `pytest_terminal_summary` runs once at the end of the session and renders each entry as `{duration}s ({cpu%}% cpu) {nodeid}` — a low percentage means the test spent most of its wall-clock time waiting on something external (I/O, a subprocess, a sleep) rather than computing.

Tests in `tests/` use pytest's built-in `pytester` fixture (enabled via `tests/conftest.py`) to run the plugin against a throwaway test file in a subprocess-like sandbox and assert on its terminal output — this is the standard way to test a pytest plugin's behavior end-to-end rather than unit-testing hook functions in isolation.
