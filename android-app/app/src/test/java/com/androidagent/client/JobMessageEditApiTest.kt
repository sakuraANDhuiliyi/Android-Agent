package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

class JobMessageEditApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: AgentApi
    private val pending = PendingMessageEdit(MessageEditDraft(PendingMessageWithdrawal("p", "c", "job-001", 49, "client-msg-008"), 0, "token=original"), "client-edit-001")
    @Before fun setup() { server = MockWebServer().also { it.start() }; api = AgentApi(server.url("/").toString().trimEnd('/'), "synthetic") }
    @After fun cleanup() { server.shutdown() }
    private fun fixture(name: String = "job_message_edit_201.json") = JSONObject(javaClass.classLoader!!.getResourceAsStream("api_contract/$name")!!.bufferedReader().readText())
    private fun enqueue(body: JSONObject, code: Int = 200) { server.enqueue(MockResponse().setResponseCode(code).setBody(body.toString())) }

    @Test fun `201 200 immutable ACKs keep old saved revision separate from current later message and withdrawal`() {
        for ((file, code, revision) in listOf(Triple("job_message_edit_201.json", 201, 1), Triple("job_message_edit_200.json", 200, 2), Triple("job_message_edit_withdrawn_200.json", 200, 2))) {
            enqueue(fixture(file), code)
            val ack = api.editJobMessage(pending)
            assertEquals(1, ack.revision); assertEquals(0, ack.expectedRevision)
            assertEquals(revision, ack.message.revision)
            assertEquals("client-edit-001", ack.editKey)
            assertNotEquals(pending.draft.text, ack.savedText) // Redaction/normalization never invalidates an authoritative acknowledgement.
            if (file.contains("withdrawn")) { assertEquals(MessageDelivery.WITHDRAWN, ack.message.delivery); assertFalse(ack.message.canEdit) }
            val request = server.takeRequest()
            assertEquals("POST", request.method); assertEquals("/api/jobs/job-001/messages/49/edits", request.path)
            assertEquals(pending.body().toString(), JSONObject(request.body.readUtf8()).toString())
        }
    }

    @Test fun `edit ACK wrong key task message version original revision or schema cannot clear pending intent`() {
        val changes: List<(JSONObject) -> Unit> = listOf(
            { it.put("schema_version", 2) }, { it.put("job_id", "other") },
            { it.getJSONObject("message").put("id", 50) }, { it.getJSONObject("message").put("revision", 0) },
            { it.getJSONObject("edit").put("schema_version", 2) }, { it.getJSONObject("edit").put("edit_key", "other") },
            { it.getJSONObject("edit").put("task_id", "other") }, { it.getJSONObject("edit").put("message_id", "49") },
            { it.getJSONObject("edit").put("expected_revision", 1) }, { it.getJSONObject("edit").put("revision", 2) },
            { it.getJSONObject("edit").put("created_at", "1007") }, { it.getJSONObject("edit").put("created_at", -1) })
        changes.forEach { change -> enqueue(fixture().also(change)); assertThrows(RuntimeException::class.java) { api.editJobMessage(pending) } }
    }

    @Test fun `CAS and permission errors cannot be mistaken for success despite a nested edit ACK`() {
        for (code in listOf(409, 403, 404, 422, 503)) {
            enqueue(fixture().put("detail", "rejected"), code)
            assertEquals(code, assertThrows(ApiException::class.java) { api.editJobMessage(pending) }.code)
        }
    }

    @Test fun `new edit requires explicit boolean authority consistent nonnegative revision and untouched pending followup`() {
        val original = fixture().getJSONObject("message")
        assertTrue(JobMessageReceipt.parse(original, "job-001", true).canEdit)
        val changes: List<(JSONObject) -> Unit> = listOf(
            { it.remove("can_edit") }, { it.put("can_edit", "true") }, { it.remove("revision") },
            { it.put("revision", -1) }, { it.put("revision", "1") }, { it.put("revision", 1.5) },
            { it.put("edited_at", JSONObject.NULL) }, { it.put("revision", 0) },
            { it.put("delivery_state", "blocked") }, { it.put("type", "steer") },
            { it.put("consumed_at", 1008) }, { it.put("follow_up_job_id", "child") }, { it.put("withdrawn_at", 1008) },
            { it.put("schema_version", 2) })
        changes.forEach { change -> assertFalse(JobMessageReceipt.parse(JSONObject(original.toString()).also(change), "job-001", true).canEdit) }
    }

    @Test fun `newer text and terminal receipt survive older GET or historical edit reply`() {
        val latest = JobMessageReceipt.parse(fixture("job_message_edit_200.json").getJSONObject("message"), "job-001", true)
        val old = JobMessageReceipt.parse(fixture().getJSONObject("message"), "job-001", true)
        assertEquals(latest, mergeJobMessageReceipts(listOf(latest), listOf(old)).single())
        val withdrawn = JobMessageReceipt.parse(fixture("job_message_edit_withdrawn_200.json").getJSONObject("message"), "job-001", true)
        assertEquals(withdrawn, mergeJobMessageReceipts(listOf(withdrawn), listOf(latest)).single())
        val child = latest.copy(delivery = MessageDelivery.FOLLOW_UP_CREATED, followUpJobId = "child", followUpTurnId = "turn", canEdit = false)
        assertEquals(child, mergeJobMessageReceipts(listOf(child), listOf(latest)).single())
    }

    @Test fun `saved edit freezes original body while preserving new draft and rejects corrupted scoped identity`() {
        val saved = SavedMessageEdit(pending.draft.copy(text = "new local draft"), pending)
        assertEquals(saved, SavedMessageEdit.parse(saved.toJson().toString()))
        assertEquals("token=original", SavedMessageEdit.parse(saved.toJson().toString())!!.pending!!.draft.text)
        val invalid = listOf(saved.toJson().put("version", 2), saved.toJson().apply { getJSONObject("draft").put("revision", "0") },
            saved.toJson().apply { getJSONObject("pending").getJSONObject("draft").getJSONObject("identity").put("job", "other") })
        invalid.forEach { assertNull(SavedMessageEdit.parse(it.toString())) }
    }
}
