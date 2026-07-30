import pytest

from agent import jobs


@pytest.fixture(autouse=True)
def stop_worker_after_test():
    """Ensure the global task worker is stopped after every test.

    The persistent worker is a global singleton; tests that start it via
    start_ask_job may leave it running. Stopping it after each test keeps
    tests isolated and prevents temp-directory cleanup failures.
    """
    yield
    jobs.stop_worker(wait=True, timeout=4.0)
