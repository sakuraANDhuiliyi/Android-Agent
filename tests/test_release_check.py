from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.release_check import python_environment_problem


class ReleaseEnvironmentPreflightTests(unittest.TestCase):
    def make_root(self, version: str) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        Path(temporary.name, ".python-version").write_text(version, encoding="utf-8")
        return temporary

    def test_rejects_wrong_python_minor_before_any_checks(self) -> None:
        temporary = self.make_root("3.12\n")
        with temporary:
            problem = python_environment_problem(
                root=Path(temporary.name), current_version=(3, 10, 20), pytest_available=True
            )
        self.assertIn("要求 Python 3.12", problem)
        self.assertIn("当前解释器为 3.10", problem)

    def test_rejects_missing_pytest(self) -> None:
        temporary = self.make_root("3.12\n")
        with temporary:
            problem = python_environment_problem(
                root=Path(temporary.name), current_version=(3, 12, 14), pytest_available=False
            )
        self.assertIn("未安装 pytest", problem)

    def test_accepts_matching_python_with_pytest(self) -> None:
        temporary = self.make_root("3.12\n")
        with temporary:
            problem = python_environment_problem(
                root=Path(temporary.name), current_version=(3, 12, 14), pytest_available=True
            )
        self.assertIsNone(problem)

    def test_rejects_invalid_version_file(self) -> None:
        temporary = self.make_root("latest\n")
        with temporary:
            problem = python_environment_problem(
                root=Path(temporary.name), current_version=(3, 12, 14), pytest_available=True
            )
        self.assertIn("无法读取有效的 Python 版本要求", problem)


if __name__ == "__main__":
    unittest.main()
