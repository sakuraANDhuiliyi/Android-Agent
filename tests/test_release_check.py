"""The validation gate must report a failure even when a tool cannot start."""
import sys
import json
from pathlib import Path

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


def test_failure_keeps_complete_streams_when_summary_omits_first_assertion(tmp_path):
    command = "import sys; print('full stdout'); print('original assertion',file=sys.stderr); print('x'*3000,file=sys.stderr); sys.exit(2)"
    result = run([sys.executable, "-c", command], timeout=5, log_dir=tmp_path / "logs")
    assert not result["ok"] and result["returncode"] == 2
    assert "original assertion" not in result["stderr_tail"]
    assert Path(result["stderr_path"]).read_text().startswith("original assertion\n")
    assert Path(result["stdout_path"]).read_text() == "full stdout\n"


def test_timeout_keeps_output_written_before_process_stalled(tmp_path):
    result = run([sys.executable, "-u", "-c", "import time; print('before stall'); time.sleep(5)"],
                 timeout=0.5, log_dir=tmp_path / "logs")
    assert not result["ok"] and result["returncode"] == 124
    assert "before stall" in Path(result["stdout_path"]).read_text()
    assert "timeout" in Path(result["stderr_path"]).read_text()


def test_default_backend_import_and_nested_helpers_cannot_use_caller_data(tmp_path):
    protected = tmp_path / "caller-data"
    protected.mkdir()
    sentinel = protected / "keep.txt"
    sentinel.write_text("existing user files", encoding="utf-8")
    child = "import agent.jobs,agent.paths,json; print(json.dumps([str(agent.paths.DATA_DIR),str(agent.paths.WORKSPACES_DIR),str(agent.paths.BUILDS_DIR)]))"
    # A nested helper inherits the same isolation, even when its parent is a
    # desktop check rather than pytest. Passing caller roots cannot override it.
    command = "import subprocess,sys; subprocess.run([sys.executable,'-c'," + repr(child) + "],check=True)"
    result = run([sys.executable, "-c", command], timeout=30, env={
        "AGENT_DATA_DIR": str(protected), "AGENT_WORKSPACES_DIR": str(protected),
        "AGENT_BUILDS_DIR": str(protected),
    })
    assert result["ok"], result["stderr_tail"]
    roots = [Path(value) for value in json.loads(result["stdout_tail"].strip())]
    assert len(set(roots)) == 3 and all(root.parent == roots[0].parent for root in roots)
    assert all(root != protected and not root.exists() for root in roots)
    assert list(protected.iterdir()) == [sentinel]
    assert sentinel.read_text(encoding="utf-8") == "existing user files"
