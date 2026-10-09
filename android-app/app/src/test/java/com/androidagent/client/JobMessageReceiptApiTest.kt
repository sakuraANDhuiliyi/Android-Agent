package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class JobMessageReceiptApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: AgentApi
    @Before fun setup() {
        server = MockWebServer().also { it.start() }
        api = AgentApi(server.url("/").toString().trimEnd('/'), "synthetic")
    }
    @After fun cleanup() { server.shutdown() }

    private fun message(state: String = "pending", type: String = "steer") = JSONObject()
        .put("schema_version", 1).put("id", 1).put("task_id", "j").put("message_key", "stable-key")
        .put("type", type).put("payload", JSONObject().put("text", "原文"))
        .put("created_at", 1700000000.0).put("consumed_at", JSONObject.NULL).put("delivery_state", state)
        .put("context_message_id", JSONObject.NULL).put("follow_up_job_id", JSONObject.NULL)
        .put("follow_up_turn_id", JSONObject.NULL).put("reason", JSONObject.NULL)
    private fun post(value: JSONObject, status: Int = 200) {
        server.enqueue(MockResponse().setResponseCode(status).setBody(JSONObject().put("schema_version", 1)
            .put("job_id", "j").put("message", value).toString()))
    }

    @Test fun `201 creation and 200 retry carry exact same caller key and original body`() {
        post(message(), 201); post(message())
        val first = api.sendJobMessage("j", "steer", "原文", "stable-key")
        val retry = api.sendJobMessage("j", "steer", "原文", "stable-key")
        assertEquals(first, retry)
        val request = server.takeRequest()
        assertEquals(request.body.readUtf8(), server.takeRequest().body.readUtf8())
        assertEquals("Bearer synthetic", request.getHeader("Authorization"))
        assertTrue(first.verifiedIdentity)
        assertEquals("待加入本轮上下文", first.label)
    }

    @Test fun `GET requests consumed receipts and separates accepted context from created child`() {
        val consumed = message("consumed").put("consumed_at", 1700000001).put("context_message_id", "steer:j:1")
        val child = message("follow_up_created", "follow_up").put("id", 2).put("message_key", "child-key")
            .put("consumed_at", 1700000002).put("follow_up_job_id", "child").put("follow_up_turn_id", "turn-child")
        server.enqueue(MockResponse().setBody(JSONObject().put("schema_version", 1).put("job_id", "j")
            .put("messages", JSONArray().put(child).put(consumed)).toString()))
        val page = api.listJobMessages("j")
        assertEquals("/api/jobs/j/messages?include_consumed=true", server.takeRequest().path)
        assertEquals(listOf(1L, 2L), page.messages.map { it.id })
        assertEquals("已加入本轮上下文", page.messages[0].label)
        assertEquals("已创建后续任务", page.messages[1].label)
        assertEquals("child", page.messages[1].followUpJobId)
    }

    @Test fun `missing evidence malformed states and old schemas never claim consumption`() {
        val invalid = listOf(
            message("consumed"), message("consumed").put("context_message_id", "m"),
            message("follow_up_created", "follow_up").put("consumed_at", 1700000001).put("follow_up_job_id", "child"),
            message("consumed").put("consumed_at", 1700000001).put("context_message_id", "m").put("id", "1"),
            message("consumed").put("consumed_at", 1700000001).put("context_message_id", "m").put("created_at", -1),
            message("consumed").put("consumed_at", 1700000001).put("context_message_id", "m").put("schema_version", 2),
            message("some_new_state"), message().put("schema_version", JSONObject.NULL),
        )
        invalid.forEach { json ->
            post(json)
            val parsed = api.sendJobMessage("j", json.getString("type"), "原文", "stable-key")
            assertEquals(json.toString(), MessageDelivery.UNKNOWN, parsed.delivery)
            assertNull(parsed.followUpJobId)
        }
    }

    @Test fun `control messages cannot appear as blank user receipts or override the latest status`() {
        val user = message("blocked", "follow_up").put("reason", "parent_canceled")
        val cancel = JSONObject().put("schema_version", 1).put("id", 2).put("task_id", "j")
            .put("message_key", "cancel-2").put("type", "cancel").put("payload", JSONObject())
        server.enqueue(MockResponse().setBody(JSONObject().put("schema_version", 1).put("job_id", "j")
            .put("messages", JSONArray().put(user).put(cancel)).toString()))
        val parsed = api.listJobMessages("j").messages
        assertEquals(1, parsed.size)
        assertEquals(MessageDelivery.BLOCKED, parsed.single().delivery)
        assertEquals("前序任务已取消", parsed.single().reasonLabel)
    }

    @Test fun `wrong task key and type are rejected but authoritative acknowledgement accepts redaction`() {
        listOf("task_id" to "other", "message_key" to "other", "type" to "follow_up").forEach { (key, value) ->
            post(message().put(key, value))
            assertThrows(IllegalArgumentException::class.java) { api.sendJobMessage("j", "steer", "原文", "stable-key") }
        }
        post(message().put("payload", JSONObject().put("text", "[REDACTED]")))
        assertTrue(api.sendJobMessage("j", "steer", "原文", "stable-key").verifiedIdentity)
    }

    @Test fun `blocked unapplied and unknown reasons remain factual`() {
        val blocked = JobMessageReceipt.parse(message("blocked", "follow_up").put("reason", "parent_failed"), "j", true)
        assertEquals("后续任务未创建", blocked.label)
        assertEquals("前序任务失败", blocked.reasonLabel)
        assertEquals(MessageDelivery.UNAPPLIED, JobMessageReceipt.parse(message("unapplied"), "j", true).delivery)
        assertNull(JobMessageReceipt.parse(message().put("reason", "untrusted server string"), "j", true).reason)
    }

    @Test fun `conflict and guest rejection remain HTTP errors`() {
        for (code in listOf(409, 403)) {
            server.enqueue(MockResponse().setResponseCode(code).setBody("{\"detail\":\"rejected\"}"))
            val e = assertThrows(ApiException::class.java) { api.sendJobMessage("j", "steer", "原文", "stable-key") }
            assertEquals(code, e.code)
        }
    }

    @Test fun `pending serialization preserves original payload and draft acknowledgement protects edits`() {
        val attachments = listOf(ContextAttachment("file", "Main", path = "Main.kt", refId = "one"))
        val pending = PendingJobMessage("p", "c", "j", "k", "steer", "原文\ncontext", "原文", SubmittedComposer.signature(attachments))
        assertEquals(pending, PendingJobMessage.parse(pending.toJson().toString()))
        assertNull(PendingJobMessage.parse(pending.toJson().put("version", 9).toString()))
        val submitted = SubmittedComposer(pending.composerText, pending.contextSignature)
        assertTrue(submitted.matches("原文", attachments))
        assertFalse(submitted.matches("新草稿", attachments))
        assertFalse(submitted.matches("原文", listOf(attachments[0].copy(refId = "two"))))
        assertFalse(submitted.matches("原文", emptyList()))
    }
}
