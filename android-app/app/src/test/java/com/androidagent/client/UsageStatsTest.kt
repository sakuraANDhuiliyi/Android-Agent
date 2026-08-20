package com.androidagent.client

import org.junit.Assert.assertEquals
import org.junit.Test

class UsageStatsTest {
    @Test
    fun aggregatesMissingTokenFieldsWithoutThrowing() {
        val jobs = listOf(
            JobInfo(
                id = "1", projectId = "p", conversationId = null, prompt = "one", status = "succeeded",
                result = null, error = null, events = emptyList(), changedFiles = emptyList(), cancelRequested = false,
                inputTokens = 100, outputTokens = 40, totalTokens = null, provider = null, model = null,
                createdAt = null, startedAt = null, finishedAt = null, hasBuildLog = false, hasApk = false,
                plan = emptyList(), durationMs = null,
            ),
        )

        val stats = UsageStats.forJobs(jobs)

        assertEquals(100, stats.input)
        assertEquals(40, stats.output)
        assertEquals(140, stats.total)
        assertEquals(1, stats.taskCount)
    }
}
