package com.androidagent.client

import org.json.JSONObject
import java.util.Locale

/** Missing report fields are unknown, not zero or a successful test run. */
internal data class FeedbackPresentation(
    val duration: String, val tasks: String, val warnings: String,
    val tests: String, val artifact: String,
) {
    companion object {
        fun from(report: JSONObject): FeedbackPresentation {
            val build = report.optJSONObject("build")
            val test = report.optJSONObject("tests")
            val tasks = build?.optJSONObject("tasks")
            val counts = test?.optJSONObject("tests")
            val artifact = report.optJSONObject("artifact")
            fun count(obj: JSONObject?, key: String): String =
                if (obj == null || obj.isNull(key)) "—" else obj.optLong(key).toString()
            return FeedbackPresentation(
                duration = if (build == null || build.isNull("duration_ms")) "—"
                    else String.format(Locale.getDefault(), "%.1f 秒", build.optLong("duration_ms") / 1000.0),
                tasks = if (tasks == null) "—" else "${count(tasks, "executed")} 已执行 · ${count(tasks, "cached")} 已缓存\n${count(tasks, "up_to_date")} 无需更新",
                warnings = count(build, "warnings"),
                tests = when {
                    test == null -> "尚未运行"
                    counts?.optBoolean("reported") == true -> "${count(counts, "passed")} 通过 · ${count(counts, "failed")} 失败 · ${count(counts, "skipped")} 跳过"
                    else -> "未收到测试统计 · ${test.optString("status", "—")}"
                },
                artifact = if (artifact == null) "—" else buildString {
                    append(artifact.optString("name").takeUnless { it.isBlank() || it == "null" } ?: "产物")
                    if (!artifact.isNull("size")) append(String.format(Locale.getDefault(), " · %.1f MB", artifact.optLong("size") / 1048576.0))
                },
            )
        }
    }
}
