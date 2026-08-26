from __future__ import annotations

import pytest


class SlowTracePlugin:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        self.slow_reports: list[pytest.TestReport] = []
        self.overrides: dict[str, float] = {}

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

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        threshold = self.overrides.get(report.nodeid, self.threshold)
        if report.when == "call" and report.duration >= threshold:
            self.slow_reports.append(report)

    def pytest_terminal_summary(self, terminalreporter: pytest.TerminalReporter) -> None:
        if not self.slow_reports:
            return
        terminalreporter.section("slow tests")
        for report in sorted(self.slow_reports, key=lambda r: r.duration, reverse=True):
            terminalreporter.write_line(f"{report.duration:.2f}s {report.nodeid}")


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
