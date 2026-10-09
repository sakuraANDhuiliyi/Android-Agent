package com.androidagent.client

import org.json.JSONObject
import java.util.Locale

/** Missing report fields are unknown, not zero or a successful test run. */
internal data class FeedbackPresentation(
    val duration: String, val tasks: String, val warnings: String,
    val tests: String, val artifact: String,
    val verification: DeliveryVerificationPresentation,
) {
    companion object {
        fun from(report: JSONObject): FeedbackPresentation {
            val build = report.optJSONObject("build")
            val tasks = build?.optJSONObject("tasks")
            val artifact = report.optJSONObject("artifact")
            val verification = DeliveryVerificationPresentation.from(
                DeliveryVerification.parse(report.optJSONObject("verification"), report.optString("job_id")),
                report.optString("job_status"),
            )
            fun count(obj: JSONObject?, key: String): String =
                if (obj == null || obj.isNull(key)) "—" else obj.optLong(key).toString()
            return FeedbackPresentation(
                duration = verification.buildDetails,
                tasks = if (tasks == null) "—" else "${count(tasks, "executed")} 已执行 · ${count(tasks, "cached")} 已缓存\n${count(tasks, "up_to_date")} 无需更新",
                warnings = count(build, "warnings"),
                tests = "${verification.tests}\n${verification.testDetails}",
                artifact = if (artifact == null) "—" else buildString {
                    append(artifact.optString("name").takeUnless { it.isBlank() || it == "null" } ?: "产物")
                    if (!artifact.isNull("size")) append(String.format(Locale.getDefault(), " · %.1f MB", artifact.optLong("size") / 1048576.0))
                },
                verification = verification,
            )
        }
    }
}
