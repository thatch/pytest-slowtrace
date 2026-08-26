from __future__ import annotations

import pytest


class SlowTracePlugin:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        self.slow_reports: list[pytest.TestReport] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.when == "call" and report.duration >= self.threshold:
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
    threshold = config.getoption("--slowtrace-threshold")
    config.pluginmanager.register(SlowTracePlugin(threshold), "slowtrace-plugin")
