# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`pytest-slowtrace` is a pytest plugin that reports what a test was doing when it ran too long — surfacing that context rather than just flagging that a test was slow.

## Commands

- Install dependencies: `uv sync`
- Run the test suite: `uv run pytest`
- Run a single test: `uv run pytest tests/test_plugin.py::test_slow_test_is_reported`
- Run the plugin against a real test to see its own output: `uv run pytest --slowtrace-threshold=0.1 <path>`
- Type-check: `uv run ty check`

## Architecture

The plugin is registered under the `pytest11` entry point (see `pyproject.toml`), pointing at `pytest_slowtrace.plugin`. Pytest discovers and loads it automatically in any environment where the package is installed — there is no explicit activation step.

`plugin.py` follows the standard pytest plugin-class pattern: `pytest_configure` builds one `SlowTracePlugin` instance per session and registers it with the plugin manager, so per-run state (the threshold, per-test overrides, CPU times, the list of slow reports) lives on that instance rather than in module globals.

The threshold a test is judged against isn't just `--slowtrace-threshold`: `pytest_collection_modifyitems` walks collected items once, up front, and records any `@pytest.mark.slowtrace(seconds=...)` override into `self.overrides` keyed by `nodeid`, so a marked test can demand a stricter (or looser) threshold than the session default.

Some tests are slow and idle on purpose — a deliberate `time.sleep`-based wait, say — and a report on them is just noise. `@pytest.mark.xslowtrace` opts a test out of slowtrace reporting entirely, no arguments needed: the same collection pass records its `nodeid` into `self.skipped`, and `pytest_runtest_logreport` bails out on that set before running any threshold or idle check. It's a separate marker rather than a flag on `slowtrace`, following pytest's own `xfail` convention of a distinct `x`-prefixed marker meaning "skip this one."

`pytest_runtest_call` is a generator-style hook wrapper (`@pytest.hookimpl(wrapper=True)`) around the actual test call — it brackets the call with `time.process_time()` to record how much CPU (not wall-clock) time the test consumed, keyed by `nodeid` in `self.cpu_times`. `pytest_runtest_logreport` fires once per test phase (`setup`/`call`/`teardown`); the plugin only looks at the `call` phase report.

A test is only reported if it clears *two* thresholds, not one: `report.duration` must meet the effective time threshold (override or `--slowtrace-threshold`, default 0.2s), **and** its idle percentage — `100 * (1 - cpu_time / duration)` — must meet `--slowtrace-idle-threshold` (default 50.0). The reasoning: a slow test that was busy on CPU the whole time is doing real, explicable work; a slow test that was mostly idle is the one worth flagging, because something external (I/O, a lock, a sleep, a subprocess) is worth investigating. If `cpu_time` is missing for a report (shouldn't normally happen), the idle check is skipped and the test is reported on duration alone. Qualifying reports are appended as `(report, cpu_time)` to `self.slow_reports`; `pytest_terminal_summary` runs once at the end of the session and renders each entry as `{duration}s ({cpu%}% cpu) {nodeid}`.

Tests in `tests/` use pytest's built-in `pytester` fixture (enabled via `tests/conftest.py`) to run the plugin against a throwaway test file in a subprocess-like sandbox and assert on its terminal output — this is the standard way to test a pytest plugin's behavior end-to-end rather than unit-testing hook functions in isolation.

Flagging a test as slow-and-idle only says *that* it was waiting, not *on what*. `pytest_runtest_call` answers that by starting a daemon thread just before the `yield` that samples the main thread's current frame (via `sys._current_frames()`) every 20ms, and joining it in the `finally` block alongside the CPU-time measurement. The cost is one extra thread per test's call phase — not free, but bounded to that test's duration and cheap enough at a 20ms interval not to skew the CPU-time measurement it runs alongside. Samples are kept in `self.stack_samples` keyed by `nodeid`; `pytest_runtest_logreport` discards the entry for any test that doesn't end up flagged, so memory doesn't grow across a large run. For a flagged test, `pytest_terminal_summary` collapses its samples with `collections.Counter` down to the single most-sampled `(filename, lineno, function)` and appends it to that test's report line — the report stays one line per test; it does not dump every sample.
