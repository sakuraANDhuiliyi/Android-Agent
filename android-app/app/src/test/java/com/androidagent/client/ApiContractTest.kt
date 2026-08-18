package com.androidagent.client

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.charset.StandardCharsets

class ApiContractTest {

    private fun loadFixture(name: String): JSONObject {
        val stream = javaClass.classLoader.getResourceAsStream("api_contract/$name")
            ?: error("missing fixture api_contract/$name")
        val text = stream.bufferedReader(StandardCharsets.UTF_8).use { it.readText() }
        return JSONObject(text)
    }

    @Test
    fun healthFixtureHasRequiredFields() {
        val payload = loadFixture("health_200.json")
        assertEquals("ok", payload.getString("status"))
        assertTrue(payload.has("user_id"))
        assertTrue(payload.has("provider"))
        assertTrue(payload.has("model"))
        assertTrue(payload.getBoolean("api_key_configured"))
        assertTrue(payload.has("port"))
    }

    @Test
    fun jobFixtureIncludesDisplayStatus() {
        val job = loadFixture("job_get_200.json").getJSONObject("job")
        assertEquals("running", job.getString("display_status"))
        assertEquals("运行中", job.getString("status_label"))
        assertFalse(job.getBoolean("cancel_requested"))
        assertTrue(job.has("apk_url"))
    }

    @Test
    fun jobMessageFixturePreservesMessageKey() {
        val message = loadFixture("job_message_201.json").getJSONObject("message")
        assertEquals("client-msg-001", message.getString("message_key"))
        assertEquals("steer", message.getString("type"))
    }

    @Test
    fun conversationEventsFixtureIncludesSchemaVersion() {
        val page = loadFixture("conversation_events_200.json")
        assertEquals(1, page.getInt("schema_version"))
        val events = page.getJSONArray("events")
        assertTrue(events.length() >= 1)
        assertEquals(1, events.getJSONObject(0).getInt("schema_version"))
    }

    @Test
    fun unauthorizedEnvelopeMatchesContract() {
        val payload = loadFixture("errors/unauthorized_401.json")
        val envelope = AgentApi.parseErrorEnvelope(payload.toString(), 401)
        val error = payload.getJSONObject("error")
        assertEquals(error.getString("code"), envelope.code)
        assertEquals(error.getBoolean("retryable"), envelope.retryable)
        assertEquals(error.getString("user_message"), envelope.userMessage)
        assertEquals(error.getInt("schema_version"), envelope.schemaVersion)
    }

    @Test
    fun validationEnvelopeIsNotRetryable() {
        val payload = loadFixture("errors/validation_422.json")
        val envelope = AgentApi.parseErrorEnvelope(payload.toString(), 422)
        assertEquals("validation_error", envelope.code)
        assertFalse(envelope.retryable)
        assertEquals("请求参数无效", envelope.userMessage)
    }

    @Test
    fun rateLimitedEnvelopeIsRetryable() {
        val payload = loadFixture("errors/rate_limited_429.json")
        val envelope = AgentApi.parseErrorEnvelope(payload.toString(), 429)
        assertEquals("rate_limited", envelope.code)
        assertTrue(envelope.retryable)
    }

    @Test
    fun payloadTooLargeAndInternalErrorFixtures() {
        val tooLarge = AgentApi.parseErrorEnvelope(
            loadFixture("errors/payload_too_large_413.json").toString(),
            413,
        )
        assertEquals("payload_too_large", tooLarge.code)
        assertFalse(tooLarge.retryable)

        val internal = AgentApi.parseErrorEnvelope(
            loadFixture("errors/internal_error_500.json").toString(),
            500,
        )
        assertEquals("internal_error", internal.code)
        assertFalse(internal.retryable)
    }

    @Test
    fun websocketDoneFixtureIncludesDisplayStatus() {
        val done = loadFixture("ws/job_done.json")
        assertEquals(1, done.getInt("schema_version"))
        assertEquals("done", done.getString("type"))
        assertEquals("succeeded", done.getString("display_status"))
        assertEquals("已完成", done.getString("status_label"))
    }
}
