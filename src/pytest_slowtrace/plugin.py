from __future__ import annotations

import collections
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from types import FrameType
from typing import cast

import pytest

# How finely to sample a test's stack, relative to the threshold it's judged
# against: a test right at the threshold still gets a handful of samples, one
# at 10x the threshold gets ~100. Floored so a very tight --slowtrace-threshold
# (or a marker override) can't turn the sampler into a busy loop.
_SAMPLE_INTERVAL_DIVISOR = 10
_MIN_SAMPLE_INTERVAL = 0.001


class SlowTracePlugin:
    def __init__(
        self,
        threshold: float,
        idle_threshold: float,
        rootdir: Path,
        app_packages: set[str],
    ) -> None:
        self.threshold = threshold
        self.idle_threshold = idle_threshold
        self.rootdir = rootdir
        self.app_packages = app_packages
        self.slow_reports: list[pytest.TestReport] = []
        self.overrides: dict[str, float] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        # Recorded onto item.user_properties, not just self.overrides/a
        # self.skipped set, because under pytest-xdist this hook never runs
        # on the controller at all (DSession.pytest_collection "prohibit[s]
        # collection of test items in controller process") -- only workers
        # collect. pytest_runtest_logreport below runs on whichever process
        # is judging the report, worker or controller, and user_properties
        # is what actually survives that trip (see pytest_runtest_call for
        # the same reasoning applied to cpu_time/stack samples).
        for item in items:
            if item.get_closest_marker("xslowtrace") is not None:
                item.user_properties.append(("slowtrace_skip", True))
                continue
            marker = item.get_closest_marker("slowtrace")
            if marker is None:
                continue
            if "seconds" in marker.kwargs:
                seconds = marker.kwargs["seconds"]
            else:
                seconds = marker.args[0]
            self.overrides[item.nodeid] = seconds
            item.user_properties.append(("slowtrace_threshold", seconds))

    def _app_frame(self, frame: FrameType) -> FrameType:
        """Walk outward from `frame` to the first frame that counts as "app
        code", skipping over library/stdlib frames along the way.

        A wait buried inside a library (e.g. requests -> urllib3 -> socket)
        samples as a stdlib-internals line that means nothing without more
        context; the caller in app code that made the blocking call is the
        useful line. Falls back to `frame` itself if nothing in the chain
        qualifies -- the wait might be entirely inside a library.

        Default test: is this frame's file under config.rootpath? That
        breaks when a project's own virtualenv is nested inside its repo
        root (tox's .tox/, uv's .venv/) -- installed third-party packages
        live under rootdir too, so the very first (innermost) library frame
        satisfies the check and the walk never reaches real app code.
        --slowtrace-app-packages sidesteps this: when set, match a frame's
        top-level module name instead of its path, since a library's
        __name__ (e.g. "urllib3.connectionpool") doesn't depend on where its
        files happen to sit on disk.
        """
        node: FrameType | None = frame
        while node is not None:
            if self.app_packages:
                name = node.f_globals.get("__name__", "")
                if name.split(".", 1)[0] in self.app_packages:
                    return node
            elif Path(node.f_code.co_filename).is_relative_to(self.rootdir):
                return node
            node = node.f_back
        return frame

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, object, object]:
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
        props = dict(report.user_properties)
        if props.get("slowtrace_skip", False):
            return
        threshold = cast("float", props.get("slowtrace_threshold", self.threshold))
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
    group.addoption(
        "--slowtrace-app-packages",
        type=str,
        default="",
        help=(
            "Comma-separated top-level package names to treat as app code when "
            "picking which frame to report for a slow-and-idle test, matched "
            "against each frame's __name__ instead of its file path. Use this "
            "when the project's own virtualenv is nested inside its rootdir "
            "(e.g. tox's .tox/ or uv's .venv/), which defeats the default "
            "rootdir-based check. Default: empty, falls back to rootdir."
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
    app_packages = {
        name.strip()
        for name in config.getoption("--slowtrace-app-packages").split(",")
        if name.strip()
    }
    config.pluginmanager.register(
        SlowTracePlugin(threshold, idle_threshold, config.rootpath, app_packages),
        "slowtrace-plugin",
    )
