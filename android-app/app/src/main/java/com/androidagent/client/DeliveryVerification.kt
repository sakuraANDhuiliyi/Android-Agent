package com.androidagent.client

import org.json.JSONObject

/** Execution evidence and source association are separate facts. Unknown data never implies success. */
data class DeliveryVerification(
    val build: VerificationStep = VerificationStep(),
    val tests: VerificationStep = VerificationStep(),
) {
    companion object {
        fun parse(json: JSONObject?, expectedJobId: String): DeliveryVerification {
            if (json == null || expectedJobId.isBlank() || json.opt("schema_version") !is Number ||
                (json.opt("schema_version") as Number).toDouble() != 1.0 ||
                json.opt("scope") != "job" || json.opt("job_id") != expectedJobId
            ) return DeliveryVerification()
            return DeliveryVerification(
                parseStep(json.optJSONObject("build"), "assembleDebug"),
                parseStep(json.optJSONObject("unit_tests"), "testDebugUnitTest"),
            )
        }

        private fun parseStep(json: JSONObject?, task: String): VerificationStep {
            if (json == null || json.opt("task") != task) return VerificationStep()
            val state = when (json.opt("state")) {
                "passed" -> VerificationState.PASSED
                "failed" -> VerificationState.FAILED
                "canceled" -> VerificationState.CANCELED
                "interrupted" -> VerificationState.INTERRUPTED
                "not_run" -> VerificationState.NOT_RUN
                "no_tests" -> VerificationState.NO_TESTS
                "skipped" -> VerificationState.SKIPPED
                "unknown" -> VerificationState.UNKNOWN
                else -> return VerificationStep()
            }
            val inputs = when (json.opt("source_match")) {
                "match" -> VerificationInputs.MATCH
                "changed" -> VerificationInputs.CHANGED
                "unknown" -> VerificationInputs.UNKNOWN
                else -> return VerificationStep()
            }
            val time = number(json, "evidence_time")
            if (!json.isNull("evidence_time") && (time == null || time <= 0 || time > 253_402_300_799.0)) return VerificationStep()
            val duration = number(json, "duration_ms")
            if (!json.isNull("duration_ms") && (duration == null || duration < 0)) return VerificationStep()
            val runId = json.opt("run_id")
            if (!json.isNull("run_id") && (runId !is String || runId.isBlank())) return VerificationStep()
            if (state !in setOf(VerificationState.NOT_RUN, VerificationState.UNKNOWN) &&
                (runId !is String || runId.isBlank() || time == null)
            ) return VerificationStep()
            val rawCounts = json.optJSONObject("counts")
            val counts = rawCounts?.let { parseCounts(it) }
            if (!json.isNull("counts") && counts == null) return VerificationStep()
            if (task == "testDebugUnitTest") {
                when (state) {
                    VerificationState.PASSED -> if (counts == null || counts.total == 0L || counts.passed == 0L || counts.failed != 0L) return VerificationStep()
                    VerificationState.NO_TESTS -> if (counts == null || counts.total != 0L) return VerificationStep()
                    VerificationState.SKIPPED -> if (counts == null || counts.total == 0L || counts.skipped != counts.total) return VerificationStep()
                    else -> Unit
                }
            } else if (state in setOf(VerificationState.NO_TESTS, VerificationState.SKIPPED)) return VerificationStep()
            val reason = (json.opt("reason") as? String)?.takeIf { it in REASONS }
            return VerificationStep(state, inputs, time, counts, duration, reason)
        }

        private val REASONS = setOf("not_run", "legacy_evidence", "no_fresh_report", "invalid_report",
            "no_tests", "all_skipped", "command_failed", "canceled", "interrupted", "inputs_changed", "inputs_unknown")

        private fun number(json: JSONObject, key: String): Double? =
            (json.opt(key) as? Number)?.toDouble()?.takeIf { it.isFinite() }

        private fun parseCounts(json: JSONObject): VerificationTestCounts? {
            fun count(key: String): Long? = number(json, key)?.takeIf {
                it >= 0 && it <= Int.MAX_VALUE && it % 1.0 == 0.0
            }?.toLong()
            val total = count("total") ?: return null
            val passed = count("passed") ?: return null
            val failed = count("failed") ?: return null
            val skipped = count("skipped") ?: return null
            if (total != passed + failed + skipped) return null
            return VerificationTestCounts(total, passed, failed, skipped)
        }
    }
}

enum class VerificationState { PASSED, FAILED, CANCELED, INTERRUPTED, NOT_RUN, NO_TESTS, SKIPPED, UNKNOWN }
enum class VerificationInputs { MATCH, CHANGED, UNKNOWN }

data class VerificationTestCounts(val total: Long, val passed: Long, val failed: Long, val skipped: Long)

data class VerificationStep(
    val state: VerificationState = VerificationState.UNKNOWN,
    val inputs: VerificationInputs = VerificationInputs.UNKNOWN,
    val finishedAt: Double? = null,
    val counts: VerificationTestCounts? = null,
    val durationMs: Double? = null,
    val reason: String? = null,
) {
    val verified: Boolean get() = state == VerificationState.PASSED && inputs == VerificationInputs.MATCH
}
