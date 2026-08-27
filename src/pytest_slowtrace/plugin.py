from __future__ import annotations

import collections
import sys
import threading
import time

import pytest

# How often the background sampler reads the main thread's stack. Short enough
# to catch a sub-200ms sleep in a couple of ticks, long enough not to spin.
_SAMPLE_INTERVAL = 0.02


class SlowTracePlugin:
    def __init__(self, threshold: float, idle_threshold: float) -> None:
        self.threshold = threshold
        self.idle_threshold = idle_threshold
        self.slow_reports: list[tuple[pytest.TestReport, float | None]] = []
        self.overrides: dict[str, float] = {}
        self.cpu_times: dict[str, float] = {}
        self.skipped: set[str] = set()
        self.stack_samples: dict[str, list[tuple[str, int, str]]] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            if item.get_closest_marker("xslowtrace") is not None:
                self.skipped.add(item.nodeid)
            marker = item.get_closest_marker("slowtrace")
            if marker is None:
                continue
            if "seconds" in marker.kwargs:
                seconds = marker.kwargs["seconds"]
            else:
                seconds = marker.args[0]
            self.overrides[item.nodeid] = seconds

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item):
        start = time.process_time()
        samples: list[tuple[str, int, str]] = []
        stop_sampling = threading.Event()
        main_ident = threading.get_ident()

        def sample_stacks() -> None:
            while not stop_sampling.wait(_SAMPLE_INTERVAL):
                # The sampler starts concurrently with the test resuming on
                # the main thread, so sys._current_frames() may not have
                # `main_ident` yet on the very first tick -- skip that tick.
                frame = sys._current_frames().get(main_ident)
                if frame is not None:
                    samples.append(
                        (frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name)
                    )

        sampler = threading.Thread(target=sample_stacks, daemon=True)
        sampler.start()
        try:
            return (yield)
        finally:
            stop_sampling.set()
            sampler.join(timeout=_SAMPLE_INTERVAL * 2)
            self.cpu_times[item.nodeid] = time.process_time() - start
            self.stack_samples[item.nodeid] = samples

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when != "call":
            return
        if report.nodeid in self.skipped:
            return
        threshold = self.overrides.get(report.nodeid, self.threshold)
        if report.duration < threshold:
            self.stack_samples.pop(report.nodeid, None)
            return
        cpu_time = self.cpu_times.get(report.nodeid)
        if cpu_time is not None and report.duration > 0:
            # Clamp at 0: clock-resolution jitter between time.process_time()
            # and the wall-clock duration (now compounded by the sampler
            # thread's own sliver of process CPU time) can otherwise push
            # cpu_time a hair above duration, producing a nonsensical
            # negative "idle percentage" for a fully CPU-bound test.
            idle_pct = max(0.0, 100 * (1 - cpu_time / report.duration))
            if idle_pct < self.idle_threshold:
                self.stack_samples.pop(report.nodeid, None)
                return
        self.slow_reports.append((report, cpu_time))

    def _stack_summary(self, nodeid: str) -> str:
        """Summarize this test's sampled stacks as one clause, or "" if none.

        A slow-and-idle test was flagged because it was waiting on something;
        the most frequently sampled frame is a cheap proxy for "what". This
        deliberately collapses potentially dozens of samples into a single
        (filename, lineno, function) so the report stays one line per test.
        """
        samples = self.stack_samples.get(nodeid)
        if not samples:
            return ""
        (filename, lineno, function), count = collections.Counter(samples).most_common(1)[0]
        return f" -- mostly at {filename}:{lineno} in {function} ({count}/{len(samples)} samples)"

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if not self.slow_reports:
            return
        terminalreporter.section("slow tests")
        for report, cpu_time in sorted(
            self.slow_reports, key=lambda pair: pair[0].duration, reverse=True
        ):
            if cpu_time is None:
                line = f"{report.duration:.2f}s {report.nodeid}"
            else:
                cpu_pct = 100 * cpu_time / report.duration
                line = f"{report.duration:.2f}s ({cpu_pct:.0f}% cpu) {report.nodeid}"
            terminalreporter.write_line(line + self._stack_summary(report.nodeid))


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("slowtrace")
    group.addoption(
        "--slowtrace-threshold",
        type=float,
        default=0.2,
        help="Report tests that take longer than this many seconds to run (default: 0.2)",
    )
    group.addoption(
        "--slowtrace-idle-threshold",
        type=float,
        default=50.0,
        help=(
            "Only report a slow test if it was idle (waiting, not computing) for at "
            "least this percent of its duration (default: 50.0). A slow test that was "
            "busy on CPU the whole time isn't worth flagging -- it's doing real work, "
            "not waiting on something."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slowtrace(seconds): report this test as slow if it exceeds `seconds`, "
        "overriding --slowtrace-threshold",
    )
    config.addinivalue_line(
        "markers",
        "xslowtrace: skip slowtrace reporting for this test entirely",
    )
    threshold = config.getoption("--slowtrace-threshold")
    idle_threshold = config.getoption("--slowtrace-idle-threshold")
    config.pluginmanager.register(
        SlowTracePlugin(threshold, idle_threshold), "slowtrace-plugin"
    )
