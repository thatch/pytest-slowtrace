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
