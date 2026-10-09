package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class JobMessageBlockerTest {
    private fun fixture(file: String = "job_messages_blocked_parent_200.json") = JSONObject(javaClass.classLoader!!.getResourceAsStream("api_contract/$file")!!.bufferedReader().readText())
    private fun row() = fixture().getJSONArray("messages").getJSONObject(0)

    @Test fun `shared parent child and seven state fixtures use actual API parser`() {
        MockWebServer().use { server ->
            server.start(); val api = AgentApi(server.url("/").toString().trimEnd('/'), "synthetic")
            for ((file, target, turn) in listOf(Triple("job_messages_blocked_parent_200.json", "job-001", "turn-001"),
                Triple("job_messages_blocked_child_200.json", "job-002", "turn-002"))) {
                server.enqueue(MockResponse().setBody(fixture(file).toString()))
                val parsed = api.listJobMessages("job-001").messages.single()
                assertEquals(target, parsed.blockingJobId); assertEquals(turn, parsed.blockingTurnId)
                assertTrue(parsed.canWithdraw); assertFalse(parsed.canEdit)
                assertEquals("GET", server.takeRequest().method)
            }
            server.enqueue(MockResponse().setBody(fixture("job_messages_200.json").toString()))
            val matrix = api.listJobMessages("job-001").messages
            assertEquals(MessageDelivery.entries.toSet(), matrix.map { it.delivery }.toSet())
            assertTrue(matrix.filter { it.delivery != MessageDelivery.BLOCKED }.all { it.blockingJobId == null && it.blockingTurnId == null })
            assertEquals("job-001", matrix.single { it.delivery == MessageDelivery.BLOCKED }.blockingJobId)
        }
    }

    @Test fun `only complete trusted failed canceled or interrupted blocked followups offer inspection`() {
        for (reason in listOf("parent_failed", "parent_canceled", "parent_interrupted")) {
            val parsed = JobMessageReceipt.parse(row().put("reason", reason), "job-001", true)
            assertEquals("job-001", parsed.blockingJobId); assertEquals("turn-001", parsed.blockingTurnId)
        }
        val changes: List<(JSONObject) -> Unit> = listOf(
            { it.remove("blocking_job_id") }, { it.remove("blocking_turn_id") }, { it.put("blocking_job_id", JSONObject.NULL) },
            { it.put("blocking_turn_id", " ") }, { it.put("blocking_job_id", 123) }, { it.put("blocking_turn_id", JSONObject()) },
            { it.put("schema_version", 2) }, { it.remove("revision") }, { it.put("revision", -1) },
            { it.put("reason", "parent_paused") }, { it.put("reason", "legacy_missing_receipt") }, { it.put("reason", "anything") },
            { it.put("delivery_state", "pending") }, { it.put("delivery_state", "unknown") }, { it.put("delivery_state", "paused") },
            { it.put("type", "steer") }, { it.put("consumed_at", 1003) }, { it.put("follow_up_job_id", "child") },
            { it.put("withdrawn_at", 1004) })
        changes.forEach { change ->
            val parsed = JobMessageReceipt.parse(row().also(change), "job-001", true)
            assertNull(parsed.blockingJobId); assertNull(parsed.blockingTurnId)
        }
        assertNull(JobMessageReceipt.parse(row(), "job-001", false).blockingJobId)
    }

    @Test fun `stale version cannot retain or resurrect blocker pair while newer body stays intact`() {
        val current = JobMessageReceipt.parse(row().put("revision", 2).put("edited_at", 1008).put("payload", JSONObject().put("text", "new body")), "job-001", true)
        for (incoming in listOf(row().put("revision", 1).put("edited_at", 1007), row().apply { remove("revision") }, row().apply { remove("blocking_job_id") }.put("revision", 2).put("edited_at", 1008))) {
            val next = JobMessageReceipt.parse(incoming, "job-001", true)
            val merged = mergeJobMessageReceipts(listOf(current), listOf(next)).single()
            assertNull(merged.blockingJobId); assertNull(merged.blockingTurnId)
            if (next.revision != 2) { assertEquals("new body", merged.text); assertEquals(2, merged.revision) }
        }
    }
}
