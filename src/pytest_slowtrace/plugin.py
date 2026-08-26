from __future__ import annotations

import time

import pytest


class SlowTracePlugin:
    def __init__(self, threshold: float, idle_threshold: float) -> None:
        self.threshold = threshold
        self.idle_threshold = idle_threshold
        self.slow_reports: list[tuple[pytest.TestReport, float | None]] = []
        self.overrides: dict[str, float] = {}
        self.cpu_times: dict[str, float] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
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
        try:
            return (yield)
        finally:
            self.cpu_times[item.nodeid] = time.process_time() - start

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when != "call":
            return
        threshold = self.overrides.get(report.nodeid, self.threshold)
        if report.duration < threshold:
            return
        cpu_time = self.cpu_times.get(report.nodeid)
        if cpu_time is not None and report.duration > 0:
            idle_pct = 100 * (1 - cpu_time / report.duration)
            if idle_pct < self.idle_threshold:
                return
        self.slow_reports.append((report, cpu_time))

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if not self.slow_reports:
            return
        terminalreporter.section("slow tests")
        for report, cpu_time in sorted(
            self.slow_reports, key=lambda pair: pair[0].duration, reverse=True
        ):
            if cpu_time is None:
                terminalreporter.write_line(f"{report.duration:.2f}s {report.nodeid}")
            else:
                cpu_pct = 100 * cpu_time / report.duration
                terminalreporter.write_line(
                    f"{report.duration:.2f}s ({cpu_pct:.0f}% cpu) {report.nodeid}"
                )


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
    threshold = config.getoption("--slowtrace-threshold")
    idle_threshold = config.getoption("--slowtrace-idle-threshold")
    config.pluginmanager.register(
        SlowTracePlugin(threshold, idle_threshold), "slowtrace-plugin"
    )
