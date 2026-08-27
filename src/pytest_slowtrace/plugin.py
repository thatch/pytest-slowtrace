from __future__ import annotations

import collections
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest

# How finely to sample a test's stack, relative to the threshold it's judged
# against: a test right at the threshold still gets a handful of samples, one
# at 10x the threshold gets ~100. Floored so a very tight --slowtrace-threshold
# (or a marker override) can't turn the sampler into a busy loop.
_SAMPLE_INTERVAL_DIVISOR = 10
_MIN_SAMPLE_INTERVAL = 0.001


class SlowTracePlugin:
    def __init__(self, threshold: float, idle_threshold: float, rootdir: Path) -> None:
        self.threshold = threshold
        self.idle_threshold = idle_threshold
        self.rootdir = rootdir
        self.slow_reports: list[pytest.TestReport] = []
        self.overrides: dict[str, float] = {}
        self.skipped: set[str] = set()

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

    def _app_frame(self, frame):
        """Walk outward from `frame` to the first frame under the project's
        rootdir, skipping over library/stdlib frames along the way.

        A wait buried inside a library (e.g. requests -> urllib3 -> socket)
        samples as a stdlib-internals line that means nothing without more
        context; the caller in app code that made the blocking call is the
        useful line. Falls back to `frame` itself if nothing in the chain
        lives under rootdir -- the wait might be entirely inside a library.
        """
        node = frame
        while node is not None:
            if Path(node.f_code.co_filename).is_relative_to(self.rootdir):
                return node
            node = node.f_back
        return frame

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item):
        start = time.process_time()
        samples: list[tuple[str, int, str]] = []
        stop_sampling = threading.Event()
        main_ident = threading.get_ident()
        threshold = self.overrides.get(item.nodeid, self.threshold)
        interval = max(_MIN_SAMPLE_INTERVAL, threshold / _SAMPLE_INTERVAL_DIVISOR)

        def sample_stacks() -> None:
            while not stop_sampling.wait(interval):
                # The sampler starts concurrently with the test resuming on
                # the main thread, so sys._current_frames() may not have
                # `main_ident` yet on the very first tick -- skip that tick.
                frame = sys._current_frames().get(main_ident)
                if frame is not None:
                    frame = self._app_frame(frame)
                    samples.append(
                        (frame.f_code.co_filename, frame.f_lineno, frame.f_code.co_name)
                    )

        sampler = threading.Thread(target=sample_stacks, daemon=True)
        sampler.start()
        try:
            return (yield)
        finally:
            stop_sampling.set()
            sampler.join(timeout=interval * 2)
            # Under pytest-xdist, this hook only runs in the worker that
            # executed the test -- the controller's own SlowTracePlugin
            # instance never sees it. item.user_properties rides along on
            # the TestReport (xdist's report_to_serializable/from_serializable
            # carry it verbatim) so the data reaches pytest_runtest_logreport
            # wherever that report ends up, worker or controller.
            item.user_properties.append(
                ("slowtrace_cpu_time", time.process_time() - start)
            )
            if samples:
                (filename, lineno, function), count = collections.Counter(
                    samples
                ).most_common(1)[0]
                item.user_properties.append(
                    (
                        "slowtrace_stack_summary",
                        (filename, lineno, function, count, len(samples)),
                    )
                )

    def _cpu_time(self, report: pytest.TestReport) -> float | None:
        return cast("float | None", dict(report.user_properties).get("slowtrace_cpu_time"))

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when != "call":
            return
        if report.nodeid in self.skipped:
            return
        threshold = self.overrides.get(report.nodeid, self.threshold)
        if report.duration < threshold:
            return
        cpu_time = self._cpu_time(report)
        if cpu_time is not None and report.duration > 0:
            # Clamp at 0: clock-resolution jitter between time.process_time()
            # and the wall-clock duration (now compounded by the sampler
            # thread's own sliver of process CPU time) can otherwise push
            # cpu_time a hair above duration, producing a nonsensical
            # negative "idle percentage" for a fully CPU-bound test.
            idle_pct = max(0.0, 100 * (1 - cpu_time / report.duration))
            if idle_pct < self.idle_threshold:
                return
        self.slow_reports.append(report)

    def _stack_summary(self, report: pytest.TestReport) -> str:
        """Summarize this test's sampled stacks as one clause, or "" if none.

        A slow-and-idle test was flagged because it was waiting on something;
        the most frequently sampled frame is a cheap proxy for "what". The
        summary was already reduced to a single (filename, lineno, function,
        count, total) in pytest_runtest_call, so there's nothing left to
        collapse here -- just format it.
        """
        summary = cast(
            "tuple[str, int, str, int, int] | None",
            dict(report.user_properties).get("slowtrace_stack_summary"),
        )
        if summary is None:
            return ""
        filename, lineno, function, count, total = summary
        return f" -- mostly at {filename}:{lineno} in {function} ({count}/{total} samples)"

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if not self.slow_reports:
            return
        terminalreporter.section("slow tests")
        for report in sorted(self.slow_reports, key=lambda r: r.duration, reverse=True):
            cpu_time = self._cpu_time(report)
            if cpu_time is None:
                line = f"{report.duration:.2f}s {report.nodeid}"
            else:
                cpu_pct = 100 * cpu_time / report.duration
                line = f"{report.duration:.2f}s ({cpu_pct:.0f}% cpu) {report.nodeid}"
            terminalreporter.write_line(line + self._stack_summary(report))


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
        SlowTracePlugin(threshold, idle_threshold, config.rootpath), "slowtrace-plugin"
    )
