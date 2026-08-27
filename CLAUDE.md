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

Flagging a test as slow-and-idle only says *that* it was waiting, not *on what*. `pytest_runtest_call` answers that by starting a daemon thread just before the `yield` that samples the main thread's current frame (via `sys._current_frames()`) on a fixed interval, and joining it in the `finally` block alongside the CPU-time measurement. The interval scales with the test's effective threshold (`threshold / _SAMPLE_INTERVAL_DIVISOR`, floored at `_MIN_SAMPLE_INTERVAL`) rather than a fixed constant, so resolution stays proportional whether `--slowtrace-threshold` is loosened or a per-test marker tightens it — a test judged at 2s samples every 200ms, one at 50ms samples every 5ms. The cost is one extra thread per test's call phase — not free, but bounded to that test's duration and, at these intervals, cheap enough not to skew the CPU-time measurement it runs alongside. Samples are kept in `self.stack_samples` keyed by `nodeid`; `pytest_runtest_logreport` discards the entry for any test that doesn't end up flagged, so memory doesn't grow across a large run. For a flagged test, `pytest_terminal_summary` collapses its samples with `collections.Counter` down to the single most-sampled `(filename, lineno, function)` and appends it to that test's report line — the report stays one line per test; it does not dump every sample.

The innermost sampled frame is often useless on its own: a wait buried a few calls deep inside a library (`requests` → `urllib3` → `socket`) samples as a stdlib-internals line that means nothing without the app-level call site that triggered it. `_app_frame` walks `f_back` outward from the sampled frame to the first one whose file lives under `config.rootpath` (passed into `SlowTracePlugin` at construction), and each sample records that frame instead of the raw innermost one. If nothing in the chain is under rootdir — the wait is entirely inside a library, with no app frame to fall back to — it just returns the innermost frame as-is.

Under `pytest-xdist`, `self.cpu_times` and `self.stack_samples` would be dead weight: each worker runs a full pytest session with its own `SlowTracePlugin` instance, so the instance that measures a test's CPU time and samples its stack is never the same instance whose `pytest_terminal_summary` runs at the end — that only fires on the controller, which never ran the test's call phase at all. Per-instance dicts keyed by nodeid can't cross that process boundary. `item.user_properties` can: pytest's own report machinery copies it onto the `TestReport` at creation time, and xdist's report serialization (`pytest_report_to_serializable`/`from_serializable`) carries it across the worker-to-controller wire intact. So `pytest_runtest_call`'s `finally` block appends `("slowtrace_cpu_time", cpu_time)` and, when there were samples, a pre-reduced `("slowtrace_stack_summary", (filename, lineno, function, count, total))` onto `item.user_properties` instead of into `self.cpu_times`/`self.stack_samples`. The `Counter` reduction that used to happen in `pytest_terminal_summary` now happens here, in the worker, before the data ever needs to leave the process — there's no reason to ship every raw sample across the wire when only the winner matters. `pytest_runtest_logreport` and `pytest_terminal_summary` then read both values off `report.user_properties` rather than off `self`, which works identically whether the report came from the local process (no xdist) or over the wire from a worker — one code path serves both cases.
