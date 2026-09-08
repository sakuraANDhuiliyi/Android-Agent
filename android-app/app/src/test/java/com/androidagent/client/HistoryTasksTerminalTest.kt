package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class HistoryTasksTerminalTest {
    @Test fun ansiSequencesCanSpanNetworkChunks() {
        val output = TerminalTranscript()
        output.append("hello\u001b[")
        output.append("31m red\u001b[0m\r\n\u001b]0;secret title")
        output.append("\u0007world")
        assertEquals("hello red\nworld", output.text)
        output.append("x\b!")
        assertTrue(output.text.endsWith("world!"))
    }
    @Test fun transcriptIsBoundedAndResettable() {
        val output = TerminalTranscript()
        output.append("x".repeat(90000))
        assertEquals(60000, output.text.length)
        output.clear()
        assertEquals("", output.text)
    }
    @Test fun subagentsCollapseIntoOneSummaryWithoutLeakingFullChats() {
        val store = TimelineStore()
        fun event(id: Long, type: String, child: String, role: String = "") = ConversationEventNormalizer.fromTaskEvent(
            JSONObject().put("id", id).put("type", type).put("task_id", "parent")
                .put("turn_id", "turn1").put("child_task_id", child).put("role", role).put("message", type)
        )!!
        store.ingest(listOf(event(1, "subagent_spawned", "a", "explorer"), event(2, "subagent_spawned", "b", "reviewer"),
            event(3, "subagent_completed", "a")))
        val rows = ConversationTimelineBuilder.buildRows(ConversationTimelineBuilder.buildTurns(store), ConversationTimelineBuilder.ExpansionPolicy())
        val agents = rows.filterIsInstance<ConversationTimelineBuilder.Row.Agents>()
        assertEquals(1, agents.size)
        assertTrue(agents.single().summary.contains("2 agents worked"))
        assertTrue(agents.single().summary.contains("explorer"))
        assertEquals(0, rows.filterIsInstance<ConversationTimelineBuilder.Row.Assistant>().size)
        val replay = ConversationEventNormalizer.fromConversationEvent(JSONObject()
            .put("id", "canonical-spawn").put("conversation_id", "c1").put("turn_id", "turn1").put("task_id", "parent")
            .put("event_type", "system_note").put("seq", 1).put("payload", JSONObject()
                .put("child_task_id", "a").put("agent_event", "subagent_spawned").put("role", "explorer")))!!
        store.ingest(listOf(replay))
        val afterReplay = ConversationTimelineBuilder.buildRows(ConversationTimelineBuilder.buildTurns(store), ConversationTimelineBuilder.ExpansionPolicy())
        assertTrue(afterReplay.filterIsInstance<ConversationTimelineBuilder.Row.Agents>().single().summary.contains("✓ explorer"))
    }
    @Test fun snapshotRestoreCarriesExpectedRevisionAndTerminalCarriesCursor() {
        MockWebServer().use { server ->
            val api = AgentApi(server.url("/").toString().trimEnd('/'), "test-token")
            server.enqueue(MockResponse().setBody("{\"ok\":true}"))
            api.restoreSnapshot("p1", "before:t1", "a".repeat(64))
            val restore = server.takeRequest()
            assertEquals("/api/projects/p1/checkpoints/before:t1/restore-snapshot", restore.path)
            assertEquals("a".repeat(64), JSONObject(restore.body.readUtf8()).getString("expected_revision"))
            server.enqueue(MockResponse().setBody("{\"chunks\":[],\"next_seq\":8}"))
            assertEquals(8, api.terminalOutput("t1", 8).getInt("next_seq"))
            assertEquals("/api/terminals/t1/output?after_seq=8", server.takeRequest().path)
        }
    }
    @Test fun taskSnapshotRetainsParentRoleAndTurn() {
        val api = AgentApi("https://example.test", "test")
        val jobs = api.decodeTasks(JSONObject("""{"jobs":[{"id":"child","project_id":"p1","parent_task_id":"main","role":"reviewer","turn_id":"t1","status":"running"}]}"""))
        assertEquals("main", jobs.single().parentTaskId)
        assertEquals("reviewer", jobs.single().role)
        assertEquals("t1", jobs.single().turnId)
    }
}
