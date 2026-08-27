def test_slow_test_is_reported(pytester):
    pytester.makepyfile(
        """
        import time

        def test_fast():
            pass

        def test_slow():
            time.sleep(0.2)
        """
    )

    result = pytester.runpytest("--slowtrace-threshold=0.1")

    result.stdout.fnmatch_lines(["*slow tests*", "*test_slow*"])


def test_fast_tests_produce_no_report(pytester):
    pytester.makepyfile(
        """
        def test_fast():
            pass
        """
    )

    result = pytester.runpytest("--slowtrace-threshold=1.0")

    assert "slow tests" not in result.stdout.str()


def test_marker_overrides_threshold_for_marked_test(pytester):
    pytester.makepyfile(
        """
        import time
        import pytest

        def test_fast():
            pass

        @pytest.mark.slowtrace(seconds=0.05)
        def test_marked_slow():
            time.sleep(0.1)
        """
    )

    result = pytester.runpytest("--slowtrace-threshold=5.0")

    result.stdout.fnmatch_lines(["*slow tests*", "*test_marked_slow*"])
    result.stdout.no_fnmatch_line("*test_fast*")


def test_cpu_bound_slow_test_reports_high_cpu_percentage(pytester):
    pytester.makepyfile(
        """
        import time

        def test_slow_cpu():
            end = time.time() + 0.2
            while time.time() < end:
                pass
        """
    )

    result = pytester.runpytest(
        "--slowtrace-threshold=0.1", "--slowtrace-idle-threshold=0"
    )

    result.stdout.fnmatch_lines(["*slow tests*", "*% cpu*test_slow_cpu*"])


def test_io_bound_slow_test_reports_cpu_percentage(pytester):
    pytester.makepyfile(
        """
        import time

        def test_slow_io():
            time.sleep(0.2)
        """
    )

    result = pytester.runpytest(
        "--slowtrace-threshold=0.1", "--slowtrace-idle-threshold=0"
    )

    result.stdout.fnmatch_lines(["*slow tests*", "*% cpu*test_slow_io*"])


def test_busy_slow_test_is_not_reported_when_not_idle_enough(pytester):
    pytester.makepyfile(
        """
        import time

        def test_slow_but_busy():
            end = time.time() + 0.25
            while time.time() < end:
                pass
        """
    )

    result = pytester.runpytest(
        "--slowtrace-threshold=0.2", "--slowtrace-idle-threshold=50"
    )

    assert "slow tests" not in result.stdout.str()


def test_idle_slow_test_is_reported_when_idle_enough(pytester):
    pytester.makepyfile(
        """
        import time

        def test_slow_and_idle():
            time.sleep(0.25)
        """
    )

    result = pytester.runpytest(
        "--slowtrace-threshold=0.2", "--slowtrace-idle-threshold=50"
    )

    result.stdout.fnmatch_lines(["*slow tests*", "*test_slow_and_idle*"])


def test_xslowtrace_marker_opts_test_out_of_reporting(pytester):
    pytester.makepyfile(
        """
        import time
        import pytest

        @pytest.mark.xslowtrace
        def test_known_slow_and_idle():
            time.sleep(0.2)

        def test_slow_and_idle():
            time.sleep(0.2)
        """
    )

    result = pytester.runpytest("--slowtrace-threshold=0.1")

    result.stdout.fnmatch_lines(["*slow tests*", "*test_slow_and_idle*"])
    result.stdout.no_fnmatch_line("*test_known_slow_and_idle*")


def test_slow_test_report_names_the_function_it_was_waiting_in(pytester):
    pytester.makepyfile(
        """
        import time

        def waiting_on_the_network():
            time.sleep(0.25)

        def test_slow_and_idle():
            waiting_on_the_network()
        """
    )

    result = pytester.runpytest(
        "--slowtrace-threshold=0.1", "--slowtrace-idle-threshold=50"
    )

    result.stdout.fnmatch_lines(["*slow tests*", "*waiting_on_the_network*"])


def test_report_survives_xdist_worker_controller_split(pytester):
    pytester.makepyfile(
        """
        import time

        def waiting_on_the_network():
            time.sleep(0.25)

        def test_slow_and_idle():
            waiting_on_the_network()
        """
    )

    result = pytester.runpytest(
        "-n2", "--slowtrace-threshold=0.1", "--slowtrace-idle-threshold=50"
    )

    result.stdout.fnmatch_lines(["*slow tests*", "*% cpu*test_slow_and_idle*"])
    result.stdout.fnmatch_lines(["*waiting_on_the_network*"])


def test_stack_summary_prefers_app_frame_over_library_frame(pytester, tmp_path_factory):
    lib_dir = tmp_path_factory.mktemp("fakelib")
    (lib_dir / "waitlib.py").write_text(
        "import time\n"
        "\n"
        "def library_internal_wait():\n"
        "    time.sleep(0.25)\n"
    )

    pytester.makepyfile(
        f"""
        import sys
        sys.path.insert(0, {str(lib_dir)!r})
        import waitlib

        def test_calls_into_a_library():
            waitlib.library_internal_wait()
        """
    )

    result = pytester.runpytest(
        "--slowtrace-threshold=0.1", "--slowtrace-idle-threshold=50"
    )

    result.stdout.fnmatch_lines(["*slow tests*", "*test_calls_into_a_library*"])
    result.stdout.no_fnmatch_line("*waitlib.py*")
