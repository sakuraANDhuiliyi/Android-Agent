package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class JobMessageQueueTest {
    private fun row(id: Long, state: String = "pending") = JSONObject().put("schema_version", 1).put("task_id", "j").put("id", id)
        .put("message_key", "key-$id").put("type", "follow_up").put("payload", JSONObject().put("text", "message $id"))
        .put("created_at", 1700000000).put("consumed_at", JSONObject.NULL).put("context_message_id", JSONObject.NULL)
        .put("follow_up_job_id", JSONObject.NULL).put("follow_up_turn_id", JSONObject.NULL).put("withdrawn_at", JSONObject.NULL)
        .put("delivery_state", state).put("revision", 0).put("edited_at", JSONObject.NULL).put("can_edit", true).put("can_withdraw", true)
    private fun queue() = JSONObject().put("schema_version", 1).put("task_id", "j").put("version", "q1:a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e")
        .put("order_revision", 0).put("message_ids", JSONArray(listOf(3, 1, 2))).put("pending_message_ids", JSONArray(listOf(3, 1, 2)))
        .put("can_reorder", true).put("reason", JSONObject.NULL)
    private fun page() = JSONObject().put("schema_version", 1).put("job_id", "j").put("messages", JSONArray(listOf(row(1), row(2), row(3)))).put("queue", queue())
    private val pending = PendingMessageReorder("p", "c", "j", "original-key", "q1:a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e", listOf(1, 3, 2))
    private fun ack() = JSONObject().put("schema_version", 1).put("task_id", "j").put("reorder_key", pending.reorderKey)
        .put("expected_version", pending.expectedVersion).put("message_ids", JSONArray(pending.messageIds)).put("order_revision", 1).put("created_at", 1700000002)
    private fun response() = page().put("reorder", ack())
    private fun api(server: MockWebServer) = AgentApi(server.url("/").toString().trimEnd('/'), "synthetic")

    @Test fun `real API preserves complete order and only moves adjacent pending slots`() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody(page().toString()))
            val page = api(server).listJobMessages("j"); val q = page.queue!!
            assertEquals(listOf(3L, 1L, 2L), q.messageIds)
            assertEquals(listOf(3L, 1L, 2L), q.ordered(page.messages).map { it.id })
            assertEquals(listOf(1L, 3L, 2L), q.moved(1, -1)); assertEquals(listOf(3L, 2L, 1L), q.moved(1, 1))
            assertNull(q.moved(3, -1)); assertNull(q.moved(2, 1)); assertNull(q.moved(88, 1)); assertNull(q.moved(1, 2))
            val steer = JobMessageReceipt.parse(row(4).put("type", "steer"), "j", true)
            assertEquals(listOf(3L, 4L, 1L, 2L), q.ordered(listOf(page.messages[0], steer, page.messages[1], page.messages[2])).map { it.id })
        }
    }

    @Test fun `queue parser rejects malformed legacy incomplete and nonprefix histories`() {
        val changes: List<(JSONObject) -> Unit> = listOf(
            { it.remove("queue") }, { it.getJSONObject("queue").put("schema_version", 2) }, { it.getJSONObject("queue").put("task_id", "other") },
            { it.getJSONObject("queue").put("version", JSONObject.NULL) }, { it.getJSONObject("queue").put("version", "q2:abc") },
            { it.getJSONObject("queue").put("version", "q1:short") }, { it.getJSONObject("queue").put("version", "q1:" + "A".repeat(64)) },
            { it.getJSONObject("queue").put("version", "q1:" + "a".repeat(65)) },
            { it.getJSONObject("queue").put("can_reorder", "true") }, { it.getJSONObject("queue").put("order_revision", true) },
            { it.getJSONObject("queue").put("order_revision", -1) }, { it.getJSONObject("queue").put("order_revision", 1.2) },
            { it.getJSONObject("queue").put("message_ids", JSONArray(listOf(3, 1, 1))) },
            { it.getJSONObject("queue").put("message_ids", JSONArray(listOf(3, 1, 2, 9))) },
            { it.getJSONObject("queue").put("pending_message_ids", JSONArray(listOf(1, 3, 2))) },
            { it.getJSONObject("queue").put("pending_message_ids", JSONArray(listOf(3, 1))) },
            { it.getJSONObject("queue").put("pending_message_ids", JSONArray(listOf(true, 1, 2))) },
            { it.getJSONObject("queue").remove("reason") }, { it.getJSONObject("queue").put("reason", "unknown_reason") },
            { it.getJSONArray("messages").getJSONObject(0).remove("revision") },
            { it.getJSONArray("messages").getJSONObject(0).put("schema_version", 9) },
            { it.getJSONArray("messages").getJSONObject(0).put("delivery_state", "blocked"); it.getJSONObject("queue").put("reason", "parent_failed") })
        MockWebServer().use { server ->
            val api = api(server)
            changes.forEach { change -> server.enqueue(MockResponse().setBody(page().also(change).toString())); assertNull(api.listJobMessages("j").queue) }
            val corrupt = page(); val rows = corrupt.getJSONArray("messages")
            rows.getJSONObject(0).put("delivery_state", "follow_up_created").put("consumed_at", 1700000002).put("follow_up_job_id", "child").put("follow_up_turn_id", "turn")
            corrupt.getJSONObject("queue").put("pending_message_ids", JSONArray(listOf(3, 2))) // created id 1 is after pending id 3
            server.enqueue(MockResponse().setBody(corrupt.toString())); assertNull(api.listJobMessages("j").queue)
        }
    }

    @Test fun `withdrawn slots and created prefix remain fixed and blocked waiting has no capability`() {
        val json = page(); val rows = json.getJSONArray("messages")
        rows.getJSONObject(0).put("delivery_state", "follow_up_created").put("consumed_at", 1700000002).put("follow_up_job_id", "child").put("follow_up_turn_id", "turn")
        rows.getJSONObject(1).put("delivery_state", "withdrawn").put("withdrawn_at", 1700000003).put("can_withdraw", false)
        rows.getJSONObject(2).put("delivery_state", "blocked").put("reason", "parent_failed")
        json.getJSONObject("queue").put("message_ids", JSONArray(listOf(1, 2, 3))).put("pending_message_ids", JSONArray(listOf(3)))
            .put("can_reorder", false).put("reason", "parent_failed")
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody(json.toString())); val q = api(server).listJobMessages("j").queue!!
            assertEquals(listOf(1L, 2L, 3L), q.messageIds); assertEquals(listOf(3L), q.pendingMessageIds); assertFalse(q.canReorder); assertNull(q.moved(3, -1))
        }
    }

    @Test fun `201 and historical 200 validate frozen body independently of current queue`() {
        MockWebServer().use { server ->
            val api = api(server)
            for (status in listOf(201, 200)) {
                val json = response(); json.getJSONObject("queue").put("order_revision", 3).put("version", "q1:804f51f71254c4081e37e7c887073560f4a6fa6cdad202e9ac67e032c43ed1e1")
                server.enqueue(MockResponse().setResponseCode(status).setBody(json.toString()))
                val ack = api.reorderJobMessages(pending)
                assertEquals(1L, ack.orderRevision); assertEquals(3L, ack.page.queue!!.orderRevision)
                assertEquals(pending.messageIds, ack.messageIds); assertEquals("q1:a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e", ack.expectedVersion)
                val request = server.takeRequest(); assertEquals("POST", request.method); assertEquals("/api/jobs/j/messages/reorders", request.path)
                assertEquals(pending.body().toString(), JSONObject(request.body.readUtf8()).toString())
            }
            server.enqueue(MockResponse().setBody(response().put("messages", "invalid projection").toString()))
            assertEquals(pending.reorderKey, api.reorderJobMessages(pending).reorderKey)
        }
    }

    @Test fun `wrong acknowledgements cannot clear intent and 409 remains explicit`() {
        val changes: List<(JSONObject) -> Unit> = listOf(
            { it.put("schema_version", 2) }, { it.put("job_id", "other") }, { it.getJSONObject("reorder").put("task_id", "other") },
            { it.getJSONObject("reorder").put("reorder_key", "different") }, { it.getJSONObject("reorder").put("expected_version", "q1:9d6f965ac832e40a5df6c06afe983e3b449c07b843ff51ce76204de05c690d11") },
            { it.getJSONObject("reorder").put("message_ids", JSONArray(listOf(3, 1, 2))) }, { it.getJSONObject("reorder").put("order_revision", 0) },
            { it.getJSONObject("reorder").put("created_at", "1700000002") })
        MockWebServer().use { server ->
            val api = api(server)
            changes.forEach { change -> server.enqueue(MockResponse().setBody(response().also(change).toString())); assertThrows(Exception::class.java) { api.reorderJobMessages(pending) } }
            server.enqueue(MockResponse().setResponseCode(409).setBody("{\"detail\":\"queue changed\"}"))
            assertEquals(409, assertThrows(ApiException::class.java) { api.reorderJobMessages(pending) }.code)
        }
    }

    @Test fun `durable intent preserves exact permutation and rejects corrupt or unbounded records`() {
        assertEquals(pending, PendingMessageReorder.parse(pending.toJson().toString()))
        val changes: List<(JSONObject) -> Unit> = listOf({ it.put("version", 2) }, { it.put("project_id", " ") },
            { it.put("reorder_key", "x".repeat(201)) }, { it.put("expected_version", "q2:bad") },
            { it.put("message_ids", JSONArray(listOf(1, 1))) }, { it.put("message_ids", JSONArray(listOf(1))) },
            { it.put("message_ids", JSONArray(listOf(1, false))) })
        changes.forEach { assertNull(PendingMessageReorder.parse(pending.toJson().also(it).toString())) }
    }
    @Test fun `shared real HTTP queue and historical reorder fixtures use production parser`() {
        fun fixture(name: String) = JSONObject(javaClass.classLoader!!.getResourceAsStream("api_contract/$name")!!.bufferedReader().readText())
        MockWebServer().use { server ->
            val api = api(server)
            server.enqueue(MockResponse().setBody(fixture("job_messages_queue_200.json").toString()))
            val initial = api.listJobMessages("job-001")
            assertEquals(listOf(1L, 2L, 3L), initial.queue!!.pendingMessageIds); assertTrue(initial.queue.canReorder)
            server.takeRequest()
            for ((file, code, currentRevision, order, canReorder) in listOf(
                listOf("job_message_reorder_201.json", 201, 1L, listOf(3L, 1L, 2L), true),
                listOf("job_message_reorder_200.json", 200, 2L, listOf(2L, 3L, 1L), true),
                listOf("job_message_reorder_blocked_200.json", 200, 2L, listOf(2L, 3L, 1L), false))) {
                val json = fixture(file as String); val raw = json.getJSONObject("reorder")
                val intent = PendingMessageReorder("p", "c", "job-001", raw.getString("reorder_key"), raw.getString("expected_version"), queueIds(raw.getJSONArray("message_ids")))
                server.enqueue(MockResponse().setResponseCode(code as Int).setBody(json.toString()))
                val ack = api.reorderJobMessages(intent)
                assertEquals(1L, ack.orderRevision); assertEquals(listOf(3L, 1L, 2L), ack.messageIds)
                assertEquals(currentRevision, ack.page.queue!!.orderRevision); assertEquals(order, ack.page.queue.messageIds); assertEquals(canReorder, ack.page.queue.canReorder)
                assertEquals(intent.body().toString(), JSONObject(server.takeRequest().body.readUtf8()).toString())
            }
            server.enqueue(MockResponse().setBody(fixture("job_messages_200.json").toString()))
            assertNull(api.listJobMessages("job-001").queue) // Existing no-queue server contract remains readable, with no reorder capability.
        }
    }

}
