from __future__ import annotations

import time

import pytest


class SlowTracePlugin:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
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
        threshold = self.overrides.get(report.nodeid, self.threshold)
        if report.when == "call" and report.duration >= threshold:
            self.slow_reports.append((report, self.cpu_times.get(report.nodeid)))

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
        default=1.0,
        help="Report tests that take longer than this many seconds to run (default: 1.0)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slowtrace(seconds): report this test as slow if it exceeds `seconds`, "
        "overriding --slowtrace-threshold",
    )
    threshold = config.getoption("--slowtrace-threshold")
    config.pluginmanager.register(SlowTracePlugin(threshold), "slowtrace-plugin")
