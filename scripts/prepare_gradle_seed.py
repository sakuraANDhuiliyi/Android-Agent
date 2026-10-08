"""Prepare offline dependencies from the trusted template, never tenant source."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cache = args.output.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trusted-gradle-template-") as temp:
        workspace = Path(temp) / "template"
        shutil.copytree(ROOT / "template", workspace,
                        ignore=shutil.ignore_patterns(".gradle", "build", "local.properties"))
        env = {key: value for key, value in os.environ.items() if key in (
            "PATH", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "LANG", "LC_ALL"
        )}
        env.update(HOME=str(Path(temp) / "home"), GRADLE_USER_HOME=str(cache))
        Path(env["HOME"]).mkdir()
        wrapper = workspace / "gradlew"
        wrapper.chmod(wrapper.stat().st_mode | 0o100)
        tasks = ["assembleDebug", "testDebugUnitTest", "lintDebug", "--no-daemon", "--stacktrace"]
        subprocess.run([str(wrapper), *tasks], cwd=workspace, env=env, check=True)
        # Verify the prepared dependencies with networking disabled at Gradle's level.
        subprocess.run([str(wrapper), "clean", *tasks, "--offline"], cwd=workspace, env=env, check=True)
    (cache / ".agent-cache-id").write_text(uuid.uuid4().hex + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
