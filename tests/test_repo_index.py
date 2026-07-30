import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import paths as paths_mod
from agent.repo_index import RepoIndex, get_repo_index
from agent.context_planner import ContextPlanner


def _make_android_fixture(root: Path) -> None:
    """Create a small Android workspace for indexing tests."""
    java = root / "app" / "src" / "main" / "java" / "com" / "example" / "demo"
    java.mkdir(parents=True)
    res = root / "app" / "src" / "main" / "res"
    layout = res / "layout"
    values = res / "values"
    layout.mkdir(parents=True)
    values.mkdir(parents=True)

    (java / "MainActivity.kt").write_text(
        """package com.example.demo

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.example.demo.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.titleText.text = getString(R.string.app_name)
        updateCounter()
    }

    private fun updateCounter() {
        binding.counter.text = "1"
    }
}
""",
        encoding="utf-8",
    )

    (java / "Counter.kt").write_text(
        """package com.example.demo

class Counter {
    var count: Int = 0
        private set

    fun increment(): Int {
        count += 1
        return count
    }

    fun reset() {
        count = 0
    }
}
""",
        encoding="utf-8",
    )

    (java / "Utils.java").write_text(
        """package com.example.demo;

public class Utils {
    public static final int MAX_COUNT = 100;

    public static String formatCount(int count) {
        return String.valueOf(count);
    }
}
""",
        encoding="utf-8",
    )

    (layout / "activity_main.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:id="@+id/root"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:orientation="vertical">

    <TextView
        android:id="@+id/titleText"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="@string/app_name" />

    <TextView
        android:id="@+id/counter"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="@string/counter_label" />
</LinearLayout>
""",
        encoding="utf-8",
    )

    (values / "strings.xml").write_text(
        """<resources>
    <string name="app_name">Demo App</string>
    <string name="counter_label">Counter</string>
</resources>
""",
        encoding="utf-8",
    )

    (root / "app" / "build.gradle.kts").write_text(
        """plugins {
    alias(libs.plugins.androidApplication)
    alias(libs.plugins.kotlinAndroid)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.constraintlayout)
}
""",
        encoding="utf-8",
    )

    (root / "build.gradle.kts").write_text(
        """plugins {
    id("com.android.application") version "8.2.0" apply false
    id("org.jetbrains.kotlin.android") version "1.9.20" apply false
}
""",
        encoding="utf-8",
    )

    (root / "settings.gradle.kts").write_text(
        """include(":app")
""",
        encoding="utf-8",
    )

    (root / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:label="@string/app_name">
        <activity android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
""",
        encoding="utf-8",
    )

    # Large file and binary.
    (root / "big.log").write_text("x" * 2_000_000, encoding="utf-8")
    binary_dir = root / "app" / "build"
    binary_dir.mkdir(parents=True, exist_ok=True)
    (binary_dir / "classes.dex").write_bytes(b"dex\x00")


class RepoIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._workspaces = temp / "workspaces"
        self._data = temp / "data"
        self._workspaces.mkdir()
        self._data.mkdir()
        self._patches = [
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
        ]
        for p in self._patches:
            p.start()
        self._workspace = paths_mod.workspace_path("user1", "project1")
        self._workspace.mkdir(parents=True)
        _make_android_fixture(self._workspace)
        self.index = get_repo_index("user1", "project1")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._temp.cleanup()

    def test_first_index_and_incremental_update(self) -> None:
        result = self.index.rebuild()
        self.assertEqual(result["status"], "ready")
        self.assertGreater(result["file_count"], 0)
        status = self.index.status()
        self.assertEqual(status["status"], "ready")

        # Update without changes should not update any file.
        update = self.index.update()
        self.assertEqual(update["status"], "ready")
        self.assertEqual(update["updated"], 0)

        # Modify a file.
        file_path = self._workspace / "app" / "src" / "main" / "java" / "com" / "example" / "demo" / "Counter.kt"
        content = file_path.read_text(encoding="utf-8")
        file_path.write_text(content + "\n// modified\n", encoding="utf-8")
        update = self.index.update()
        self.assertEqual(update["updated"], 1)

    def test_deleted_and_renamed_files(self) -> None:
        self.index.rebuild()
        file_path = self._workspace / "app" / "src" / "main" / "java" / "com" / "example" / "demo" / "Counter.kt"
        rel = file_path.relative_to(self._workspace).as_posix()
        self.assertIsNotNone(self.index.get_file(rel))
        file_path.unlink()
        self.index.update()
        self.assertIsNone(self.index.get_file(rel))

        # Rename MainActivity.kt to MainActivity2.kt.
        src = self._workspace / "app" / "src" / "main" / "java" / "com" / "example" / "demo" / "MainActivity.kt"
        dst = src.with_name("MainActivity2.kt")
        src.rename(dst)
        self.index.update()
        self.assertIsNone(self.index.get_file("app/src/main/java/com/example/demo/MainActivity.kt"))
        self.assertIsNotNone(self.index.get_file("app/src/main/java/com/example/demo/MainActivity2.kt"))

    def test_kotlin_and_java_symbols(self) -> None:
        self.index.rebuild()
        counter = self.index.find_symbol(name="Counter")
        self.assertTrue(any(s["symbol_type"] == "class" for s in counter))
        increment = self.index.find_symbol(name="increment")
        self.assertTrue(any(s["symbol_type"] == "function" for s in increment))
        main = self.index.find_symbol(name="MainActivity")
        self.assertTrue(any(s["symbol_type"] == "class" for s in main))
        utils = self.index.find_symbol(name="Utils")
        self.assertTrue(any(s["symbol_type"] == "class" and s["qualified_name"] == "com.example.demo.Utils" for s in utils))
        max_count = self.index.find_symbol(name="MAX_COUNT")
        self.assertTrue(any(s["symbol_type"] == "field" for s in max_count))

    def test_xml_resources_and_viewbinding(self) -> None:
        self.index.rebuild()
        title = self.index.find_symbol(name="titleText")
        self.assertTrue(any(s["symbol_type"] == "resource_id" for s in title))
        app_name = self.index.find_symbol(name="app_name")
        self.assertTrue(any(s["symbol_type"] == "resource_id" for s in app_name))
        binding = self.index.find_symbol(name="ActivityMainBinding")
        self.assertTrue(any(s["symbol_type"] == "view_binding_layout" for s in binding))

    def test_manifest_activity_association(self) -> None:
        self.index.rebuild()
        activities = self.index.find_symbol(symbol_type="manifest.activity")
        self.assertTrue(any("MainActivity" in a["qualified_name"] for a in activities))

    def test_gradle_dependencies(self) -> None:
        self.index.rebuild()
        deps = self.index.find_symbol(symbol_type="gradle_dependency")
        self.assertTrue(any("androidx" in d["qualified_name"] for d in deps))
        plugins = self.index.find_symbol(symbol_type="gradle_plugin")
        self.assertTrue(any(p["name"] for p in plugins))

    def test_fts5_search_ranking(self) -> None:
        self.index.rebuild()
        hits = self.index.search("Counter")
        paths = [h["rel_path"] for h in hits]
        self.assertIn("app/src/main/java/com/example/demo/Counter.kt", paths)
        # Rank should be present (lower is better).
        self.assertTrue(all("rank" in h for h in hits))

    def test_large_file_and_binary_ignored(self) -> None:
        self.index.rebuild()
        self.assertIsNone(self.index.get_file("big.log"))
        self.assertIsNone(self.index.get_file("app/build/classes.dex"))

    def test_corrupt_index_rebuild(self) -> None:
        self.index.rebuild()
        # Corrupt the SQLite DB by writing garbage at the start.
        db_path = self.index._db_path
        with open(db_path, "r+b") as f:
            f.write(b"NOT A DB")
        # Rebuild should recover by recreating the index.
        result = self.index.rebuild()
        self.assertEqual(result["status"], "ready")
        self.assertGreater(result["file_count"], 0)

    def test_user_isolation(self) -> None:
        self.index.rebuild()
        other_workspace = paths_mod.workspace_path("user2", "project2")
        other_workspace.mkdir(parents=True)
        _make_android_fixture(other_workspace)
        # Add a symbol that only exists in user2/project2.
        java = other_workspace / "app" / "src" / "main" / "java" / "com" / "example" / "demo"
        (java / "UserOnly.kt").write_text(
            "package com.example.demo\n\nclass UserOnlyFeature\n",
            encoding="utf-8",
        )
        other_index = get_repo_index("user2", "project2")
        other_index.rebuild()
        self.assertFalse(self.index.find_symbol(name="UserOnlyFeature"))
        self.assertTrue(other_index.find_symbol(name="UserOnlyFeature"))


class ContextPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        temp = Path(self._temp.name)
        self._workspaces = temp / "workspaces"
        self._data = temp / "data"
        self._workspaces.mkdir()
        self._data.mkdir()
        self._patches = [
            patch("agent.paths.DATA_DIR", self._data),
            patch("agent.paths.WORKSPACES_DIR", self._workspaces),
        ]
        for p in self._patches:
            p.start()
        self._workspace = paths_mod.workspace_path("user1", "project1")
        self._workspace.mkdir(parents=True)
        _make_android_fixture(self._workspace)
        self.index = get_repo_index("user1", "project1")
        self.index.rebuild()
        self.planner = ContextPlanner(self.index)

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._temp.cleanup()

    def test_budget_and_deduplication(self) -> None:
        plan = self.planner.plan(
            prompt="fix Counter increment",
            current_file="app/src/main/java/com/example/demo/Counter.kt",
            budget_chars=10_000,
        )
        self.assertLessEqual(plan["total_chars"], 10_000)
        # Current file should be selected.
        paths = [s["rel_path"] for s in plan["selected"] if s["kind"] == "file"]
        self.assertIn("app/src/main/java/com/example/demo/Counter.kt", paths)
        # No duplicate files.
        self.assertEqual(len(paths), len(set(paths)))
        # Prompt only appears once implicitly.
        self.assertEqual(sum(1 for s in plan["selected"] if s.get("kind") == "history_summary"), 0)

    def test_long_history_coexists_with_retrieval(self) -> None:
        history = [{"role": "user", "content": f"turn {i}"} for i in range(10)]
        plan = self.planner.plan(
            prompt="explain MainActivity",
            current_file="app/src/main/java/com/example/demo/MainActivity.kt",
            history=history,
            budget_chars=20_000,
        )
        self.assertLessEqual(plan["total_chars"], 20_000)
        # Should include both current file and some history summary.
        self.assertTrue(any(s["kind"] == "file" for s in plan["selected"]))
        self.assertTrue(any(s["kind"] == "history_summary" for s in plan["selected"]))

    def test_symbol_matches_increase_context(self) -> None:
        plan = self.planner.plan(
            prompt="how does updateCounter work",
            current_file="app/src/main/java/com/example/demo/MainActivity.kt",
            budget_chars=20_000,
        )
        self.assertTrue(
            any("updateCounter" in str(s.get("reason", "")) for s in plan["selected"])
        )
