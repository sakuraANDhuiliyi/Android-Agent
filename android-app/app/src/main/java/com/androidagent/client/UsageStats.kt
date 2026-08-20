package com.androidagent.client

import java.util.Calendar

data class UsageStats(
    val input: Int,
    val output: Int,
    val total: Int,
    val taskCount: Int,
) {
    companion object {
        fun forJobs(jobs: List<JobInfo>, periodDays: Int? = null): UsageStats {
            val cutoff = periodDays?.let {
                Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -it) }.timeInMillis / 1000.0
            }
            val filtered = jobs.filter { cutoff == null || (it.createdAt ?: 0.0) >= cutoff }
            return UsageStats(
                input = filtered.sumOf { it.inputTokens ?: 0 },
                output = filtered.sumOf { it.outputTokens ?: 0 },
                total = filtered.sumOf { it.totalTokens ?: ((it.inputTokens ?: 0) + (it.outputTokens ?: 0)) },
                taskCount = filtered.size,
            )
        }

        fun compact(value: Int): String = when {
            value >= 1_000_000 -> "%.1fM".format(value / 1_000_000f)
            value >= 1_000 -> "%.1fK".format(value / 1_000f)
            else -> value.toString()
        }
    }
}
