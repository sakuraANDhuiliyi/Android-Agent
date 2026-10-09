"""Verification input snapshots include real build inputs and fail closed."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.safe_paths import open_workspace_file
from agent.workspace_inputs import capture_workspace_inputs, compare_workspace_inputs


def put(root: Path, relative: str, content: str = "original") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.mark.parametrize("relative", [
    "settings.gradle.kts", "gradle.properties", "build.gradle.kts", "gradlew",
    "gradle/wrapper/gradle-wrapper.properties", "gradle/libs.versions.toml",
    "feature/build.gradle.kts", "feature/src/main/kotlin/Main.kt",
    "feature/src/main/res/values/strings.xml", "feature/src/main/assets/page.html",
    "buildSrc/src/main/kotlin/Convention.kt", "included-build/settings.gradle.kts",
    "feature/src/main/assets/build/config.json", "buildSrc/src/main/kotlin/example/build/Plugin.kt",
    "feature/src/test/resources/node_modules/fixture.js",
])
def test_root_configuration_wrapper_and_every_module_invalidate_evidence(tmp_path, relative):
    path = put(tmp_path, relative)
    before = capture_workspace_inputs(tmp_path)
    assert before["status"] == "complete"
    path.write_text("changed")
    assert compare_workspace_inputs(before, capture_workspace_inputs(tmp_path)) == "changed"


def test_generated_outputs_do_not_invalidate_inputs_or_create_checkpoint_blobs(tmp_path):
    put(tmp_path, "app/src/main/java/Main.java")
    before = capture_workspace_inputs(tmp_path)
    for relative in ["app/build/classes/Main.class", "feature/build/results/report.xml",
                     ".gradle/cache/state", ".git/index", "node_modules/library/index.js",
                     ".artifacts/test.log", ".idea/workspace.xml"]:
        put(tmp_path, relative, "generated")
    after = capture_workspace_inputs(tmp_path)
    assert compare_workspace_inputs(before, after) == "match"
    assert after["file_count"] == 1
    assert not (tmp_path / "checkpoints").exists()


def test_add_remove_rename_and_executable_mode_are_inputs(tmp_path):
    source = put(tmp_path, "gradlew")
    before = capture_workspace_inputs(tmp_path)
    source.chmod(source.stat().st_mode ^ 0o100)
    assert compare_workspace_inputs(before, capture_workspace_inputs(tmp_path)) == "changed"
    before = capture_workspace_inputs(tmp_path)
    source.rename(tmp_path / "other-wrapper")
    assert compare_workspace_inputs(before, capture_workspace_inputs(tmp_path)) == "changed"
    before = capture_workspace_inputs(tmp_path)
    (tmp_path / "other-wrapper").unlink()
    assert compare_workspace_inputs(before, capture_workspace_inputs(tmp_path)) == "changed"
    before = capture_workspace_inputs(tmp_path)
    put(tmp_path, "new-module/src/Example.kt")
    assert compare_workspace_inputs(before, capture_workspace_inputs(tmp_path)) == "changed"


def test_a_module_named_build_is_unknown_instead_of_omitted(tmp_path):
    put(tmp_path, "settings.gradle.kts", "include(\":build\")")
    put(tmp_path, "build/build.gradle.kts", "plugins { java }")
    put(tmp_path, "build/src/main/java/Example.java")
    result = capture_workspace_inputs(tmp_path)
    assert result["status"] == "unknown"
    assert result["digest"] is None
    assert result["reason"] == "excluded_build_module"


@pytest.mark.parametrize("target", ["internal", "external", "broken"])
def test_symlinks_are_unknown_instead_of_silently_omitted(tmp_path, target):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local = put(workspace, "source.txt")
    outside = put(tmp_path, "outside.txt")
    destination = local if target == "internal" else outside if target == "external" else tmp_path / "missing"
    (workspace / "linked-input").symlink_to(destination)
    result = capture_workspace_inputs(workspace)
    assert result["status"] == "unknown"
    assert result["digest"] is None
    assert result["reason"] == "symlink_input"


@pytest.mark.parametrize("limits,reason", [
    ({"max_files": 1}, "file_count_limit"),
    ({"max_file_bytes": 3}, "file_size_limit"),
    ({"max_total_bytes": 5}, "total_size_limit"),
])
def test_input_limits_do_not_certify_partial_workspaces(tmp_path, limits, reason):
    put(tmp_path, "one.txt", "1234")
    put(tmp_path, "two.txt", "5678")
    result = capture_workspace_inputs(tmp_path, **limits)
    assert result["status"] == "unknown"
    assert result["digest"] is None
    assert result["reason"] == reason


def test_unreadable_input_is_unknown(tmp_path):
    put(tmp_path, "settings.gradle.kts")
    with patch("agent.workspace_inputs.open_workspace_file", side_effect=PermissionError("unreadable")):
        result = capture_workspace_inputs(tmp_path)
    assert result["status"] == "unknown"
    assert result["digest"] is None


def test_concurrent_new_input_cannot_be_certified(tmp_path):
    put(tmp_path, "original.txt")

    @contextmanager
    def changing_open(root, relative):
        with open_workspace_file(root, relative) as handle:
            put(root, "added-during-capture.txt")
            yield handle

    with patch("agent.workspace_inputs.open_workspace_file", changing_open):
        result = capture_workspace_inputs(tmp_path)
    assert result["status"] == "unknown"
    assert result["reason"] == "inputs_changed_during_capture"


def test_input_modified_while_being_read_is_unknown(tmp_path):
    source = put(tmp_path, "source.kt")

    @contextmanager
    def changing_open(root, relative):
        with open_workspace_file(root, relative) as handle:
            class MutatingRead:
                changed = False

                def fileno(self):
                    return handle.fileno()

                def read(self, size):
                    data = handle.read(size)
                    if not self.changed:
                        self.changed = True
                        source.write_text("changed during the read")
                    return data

            yield MutatingRead()

    with patch("agent.workspace_inputs.open_workspace_file", changing_open):
        result = capture_workspace_inputs(tmp_path)
    assert result["status"] == "unknown"
    assert result["reason"] == "inputs_changed_during_capture"


def test_file_swapped_for_external_symlink_after_scan_fails_closed(tmp_path):
    root = tmp_path / "workspace"
    source = put(root, "source.kt")
    outside = put(tmp_path, "external.kt", "must not be read as an input")

    @contextmanager
    def swapping_open(workspace, relative):
        source.unlink()
        source.symlink_to(outside)
        with open_workspace_file(workspace, relative) as handle:
            yield handle

    with patch("agent.workspace_inputs.open_workspace_file", swapping_open):
        result = capture_workspace_inputs(root)
    assert result["status"] == "unknown"
    assert result["digest"] is None


def test_touching_unchanged_input_preserves_content_match(tmp_path):
    source = put(tmp_path, "source.kt")
    before = capture_workspace_inputs(tmp_path)
    source.touch()
    assert compare_workspace_inputs(before, capture_workspace_inputs(tmp_path)) == "match"


@pytest.mark.parametrize("old", [None, {}, {"status": "complete", "digest": "abc"},
                                    {"scope": "workspace_inputs_v1", "status": "unknown", "digest": None}])
def test_unknown_and_legacy_evidence_never_matches(tmp_path, old):
    current = capture_workspace_inputs(tmp_path)
    assert compare_workspace_inputs(old, current) == "unknown"
    assert compare_workspace_inputs(current, old) == "unknown"


def test_digest_does_not_expose_file_names_or_contents(tmp_path):
    put(tmp_path, "private-settings.properties", "synthetic-private-value")
    result = capture_workspace_inputs(tmp_path)
    assert result["status"] == "complete"
    assert "private-settings" not in str(result)
    assert "synthetic-private-value" not in str(result)
