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
