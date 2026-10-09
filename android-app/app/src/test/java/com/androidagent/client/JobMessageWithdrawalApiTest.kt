package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class JobMessageWithdrawalApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: AgentApi
    @Before fun setup() {
        server = MockWebServer().also { it.start() }
        api = AgentApi(server.url("/").toString().trimEnd('/'), "synthetic")
    }
    @After fun cleanup() { server.shutdown() }
    private fun receipt(state: String = "pending") = JSONObject().put("schema_version", 1)
        .put("id", 42).put("task_id", "j").put("message_key", "k").put("type", "follow_up")
        .put("payload", JSONObject().put("text", "later")).put("created_at", 1700000000)
        .put("consumed_at", JSONObject.NULL).put("context_message_id", JSONObject.NULL)
        .put("follow_up_job_id", JSONObject.NULL).put("follow_up_turn_id", JSONObject.NULL)
        .put("delivery_state", state).put("can_withdraw", state == "pending")
        .put("withdrawn_at", if (state == "withdrawn") 1700000001 else JSONObject.NULL)
    private fun envelope(row: JSONObject) = JSONObject().put("schema_version", 1).put("job_id", "j").put("message", row)

    @Test fun `withdrawal first and repeated 200 use numeric original ID and no JSON body`() {
        repeat(2) { server.enqueue(MockResponse().setBody(envelope(receipt("withdrawn")).toString())) }
        val first = api.withdrawJobMessage("j", 42, "k")
        assertEquals(first, api.withdrawJobMessage("j", 42, "k"))
        repeat(2) {
            val req = server.takeRequest()
            assertEquals("POST", req.method)
            assertEquals("/api/jobs/j/messages/42/withdraw", req.path)
            assertEquals(0L, req.bodySize)
        }
        assertEquals(MessageDelivery.WITHDRAWN, first.delivery)
        assertEquals("追问已撤回", first.label)
        assertFalse(first.canWithdraw)
    }

    @Test fun `only explicit boolean authority on untouched followups enables withdrawal`() {
        assertTrue(JobMessageReceipt.parse(receipt(), "j", true).canWithdraw)
        assertTrue(JobMessageReceipt.parse(receipt("blocked").put("can_withdraw", true), "j", true).canWithdraw)
        val invalid = listOf(receipt().put("can_withdraw", "true"), receipt().put("can_withdraw", 1),
            receipt().apply { remove("can_withdraw") }, receipt().put("type", "steer"),
            receipt().put("context_message_id", "context"), receipt().put("follow_up_job_id", "child"),
            receipt().put("follow_up_turn_id", "turn"), receipt().put("consumed_at", 1700000001),
            receipt().put("withdrawn_at", 1700000001), receipt().put("schema_version", 2))
        invalid.forEach { assertFalse(it.toString(), JobMessageReceipt.parse(it, "j", true).canWithdraw) }
    }

    @Test fun `withdrawn needs time false authority and absence of every consumption link`() {
        val invalid = listOf(receipt("withdrawn").put("withdrawn_at", JSONObject.NULL),
            receipt("withdrawn").put("withdrawn_at", -1), receipt("withdrawn").put("withdrawn_at", "1700000001"),
            receipt("withdrawn").put("can_withdraw", true), receipt("withdrawn").put("can_withdraw", "false"),
            receipt("withdrawn").apply { remove("can_withdraw") }, receipt("withdrawn").put("type", "steer"),
            receipt("withdrawn").put("consumed_at", 1700000001), receipt("withdrawn").put("context_message_id", "m"),
            receipt("withdrawn").put("follow_up_job_id", "child"), receipt("withdrawn").put("follow_up_turn_id", "turn"))
        invalid.forEach {
            val parsed = JobMessageReceipt.parse(it, "j", true)
            assertEquals(it.toString(), MessageDelivery.UNKNOWN, parsed.delivery)
            assertFalse(parsed.canWithdraw)
            assertNull(parsed.withdrawnAt)
        }
    }

    @Test fun `wrong identity or nonwithdrawn success response cannot confirm withdrawal`() {
        val invalid = listOf(receipt("withdrawn").put("id", 43), receipt("withdrawn").put("message_key", "other"),
            receipt("withdrawn").put("task_id", "other"), receipt("withdrawn").put("schema_version", 2), receipt())
        invalid.forEach {
            server.enqueue(MockResponse().setBody(envelope(it).toString()))
            assertThrows(IllegalArgumentException::class.java) { api.withdrawJobMessage("j", 42, "k") }
        }
    }

    @Test fun `409 403 and 404 remain errors even if body contains a fabricated withdrawn receipt`() {
        for (code in listOf(409, 403, 404)) {
            server.enqueue(MockResponse().setResponseCode(code).setBody(envelope(receipt("withdrawn")).put("detail", "rejected").toString()))
            assertEquals(code, assertThrows(ApiException::class.java) { api.withdrawJobMessage("j", 42, "k") }.code)
        }
    }

    @Test fun `retry of original send can confirm immutable withdrawn message without reviving it`() {
        server.enqueue(MockResponse().setBody(envelope(receipt("withdrawn")).toString()))
        val parsed = api.sendJobMessage("j", "follow_up", "later", "k")
        assertEquals(MessageDelivery.WITHDRAWN, parsed.delivery)
        assertFalse(parsed.canWithdraw)
    }

    @Test fun `pending withdrawal serializes only its scoped server identity and rejects corrupt data`() {
        val pending = PendingMessageWithdrawal("p", "c", "j", 42, "k")
        assertEquals(pending, PendingMessageWithdrawal.parse(pending.toJson().toString()))
        for (bad in listOf(pending.toJson().put("message_id", "42"), pending.toJson().put("message_id", -1),
            pending.toJson().put("message_id", 2.5), pending.toJson().put("version", 2))) {
            assertNull(PendingMessageWithdrawal.parse(bad.toString()))
        }
    }
}
