"""The validation gate must report a failure even when a tool cannot start."""
import sys

from scripts.release_check import run


def test_missing_tool_is_a_reported_failure(tmp_path):
    result = run([str(tmp_path / "missing-tool")], timeout=1)
    assert result["ok"] is False
    assert result["returncode"] == 127
    assert result["stderr_tail"]


def test_failing_tool_keeps_diagnostic_output():
    result = run([sys.executable, "-c", "import sys; print('failed assertion'); sys.exit(3)"], timeout=5)
    assert result["ok"] is False
    assert result["returncode"] == 3
    assert "failed assertion" in result["stdout_tail"]


def test_timeout_cannot_be_reported_as_passing():
    result = run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)
    assert result["ok"] is False
    assert result["returncode"] == 124
