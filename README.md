# pytest-slowtrace

`pytest-slowtrace` is a pytest plugin that reports what a test was doing when it ran too long. It is meant to catch tests that are slow because they are waiting — on sleep, I/O, locks, subprocesses, network calls, etc. — rather than tests that are simply doing CPU work.

There are more targeted pytest plugins that can nerf `sleep()` calls or block network access entirely. Slowtrace is a more holistic detector: it flags any mostly-idle test and points you at where to start looking, whether the wait is in the test itself, the code under test, a library call, or a subprocess invocation that makes the test "slow" in practice.

## Enabling the plugin

Install the package in the environment where pytest runs:

```bash
pip install pytest-slowtrace
```

Pytest discovers the plugin automatically through its `pytest11` entry point; there is no explicit activation step. Once installed, run pytest as usual:

```bash
pytest
```

If a test exceeds the slowtrace threshold and is mostly idle, the terminal summary includes a `slow tests` section with the test name, CPU percentage, and the most-sampled application frame.

## Setting the threshold

By default, tests are reported when they take at least `0.2` seconds and are at least `50%` idle. On a reasonably fast machine, well-mocked unit tests should normally be effectively instantaneous, so the default should be noisy only when a test is accidentally waiting on something.

Set a project-wide threshold in pytest config via `addopts`:

```toml
[tool.pytest.ini_options]
addopts = "--slowtrace-threshold=0.5"
```

Or pass it on the command line:

```bash
pytest --slowtrace-threshold=0.5
```

For one test, override the threshold with `@pytest.mark.slowtrace`:

```python
import pytest

@pytest.mark.slowtrace(seconds=1.0)
def test_allowed_to_take_about_a_second():
    ...
```

## Mark real integration tests with `xslowtrace`

Slowtrace is most useful for fast unit tests that should use mocks instead of real external systems. If you intentionally have integration tests that talk to real services, wait on subprocesses, or otherwise spend time doing real I/O, mark them with `xslowtrace` so they do not produce noise:

```python
import pytest

@pytest.mark.xslowtrace
def test_real_service_integration():
    ...
```

## Choosing the application package

When a test is slow and idle, slowtrace samples the stack and reports the application frame that most likely caused the wait. By default, "application code" means code under pytest's root directory.

If your virtualenv or test dependencies live under the project root — for example in `.venv/` or `.tox/` — that root-directory check can mistake library frames for application frames. In that case, set the app package explicitly:

```toml
[tool.pytest.ini_options]
addopts = "--slowtrace-app-packages=myapp"
```

For multiple top-level packages, use a comma-separated list:

```bash
pytest --slowtrace-app-packages=myapp,myotherapp
```

This matches frames by their module name, such as `myapp.worker`, instead of by file path.

## Version compatibility

This library is compatible with Python 3.10+, but should be linted under the newest stable version.

## Versioning

This library follows [meanver](https://meanver.org/), which is like [semver](https://semver.org/) with a promise to rename when the major version changes.

## License

pytest-slowtrace is copyright [Tim Hatch](https://timhatch.com/), and licensed under the MIT license. See the `LICENSE` file for details.
