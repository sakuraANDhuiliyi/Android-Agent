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
    fun conversationEventsFixtureCoversStructuredSummaries() {
        val events = loadFixture("conversation_events_200.json").getJSONArray("events")
        val byType = (0 until events.length()).associateBy(
            { events.getJSONObject(it).getString("event_type") },
            { events.getJSONObject(it).getJSONObject("payload") },
        )
        val build = byType.getValue("build_summary")
        assertEquals("build", build.getString("kind"))
        assertEquals("assembleDebug", build.getString("task"))
        assertTrue(build.getBoolean("success"))
        assertEquals(2048, build.getInt("apk_size_bytes"))
        assertTrue(build.has("tool_call_id"))
        assertTrue(build.has("duration_ms"))
        assertTrue(build.has("error_count"))

        val test = byType.getValue("test_summary")
        assertEquals("test", test.getString("kind"))
        val tests = test.getJSONObject("tests")
        assertEquals(10, tests.getInt("passed"))
        assertEquals(2, tests.getInt("failed"))

        val changes = byType.getValue("changes")
        assertEquals(12, changes.getInt("additions"))
        assertEquals(3, changes.getInt("deletions"))
        assertTrue(changes.getJSONArray("files").length() == 2)

        val artifact = byType.getValue("artifact")
        assertEquals("apk", artifact.getString("kind"))
        assertTrue(artifact.has("size_bytes"))
        assertTrue(artifact.has("url"))
    }

    @Test
    fun conversationTurnsFixtureCarriesTraceIdentity() {
        val payload = loadFixture("conversation_turns_200.json")
        assertEquals(1, payload.getInt("schema_version"))
        val turn = payload.getJSONArray("turns").getJSONObject(0)
        assertEquals("turn-001", turn.getString("id"))
        assertEquals("job-001", turn.getString("task_id"))
        assertEquals("succeeded", turn.getString("status"))
        assertTrue(turn.getString("trace_id").isNotEmpty())
        assertEquals("Add dark mode toggle", turn.getString("user_preview"))
        val counts = turn.getJSONObject("event_counts")
        assertEquals(1, counts.getInt("build_summary"))
        assertEquals(1, counts.getInt("test_summary"))
    }

    @Test
    fun turnTraceFixtureMatchesStepContract() {
        val trace = loadFixture("turn_trace_200.json")
        assertEquals(1, trace.getInt("schema_version"))
        assertEquals("turn-001", trace.getString("turn_id"))
        assertEquals("job-001", trace.getString("job_id"))
        assertEquals("trace-001", trace.getString("trace_id"))
        assertEquals(500, trace.getInt("queue_ms"))
        assertEquals(24000, trace.getInt("total_ms"))
        val steps = trace.getJSONArray("steps")
        assertTrue(steps.length() >= 2)
        val first = steps.getJSONObject(0)
        assertEquals("user_message", first.getString("type"))
        assertTrue(first.has("seq"))
        assertTrue(first.has("at"))
        assertTrue(first.has("label"))
        assertTrue(first.has("detail"))
        assertTrue(first.has("event_id"))
        assertTrue(first.has("duration_ms"))
        val labels = (0 until steps.length()).map { steps.getJSONObject(it).getString("label") }
        assertTrue(labels.contains("用户消息"))
        assertTrue(labels.contains("Turn 完成"))
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

    @Test
    fun projectSettingsFixtureIncludesPermissionProfiles() {
        val payload = loadFixture("project_settings_200.json")
        val settings = payload.getJSONObject("settings")
        assertEquals("standard", settings.getString("permission_profile"))
        assertTrue(settings.has("disabled_rules"))
        assertTrue(settings.has("disabled_skills"))
        val profiles = payload.getJSONArray("permission_profiles")
        assertEquals(3, profiles.length())
        val names = (0 until profiles.length()).map { profiles.getJSONObject(it).getString("profile") }
        assertEquals(listOf("safe", "standard", "full_access"), names)
    }

    @Test
    fun rulesFixtureCarriesEnabledFlags() {
        val payload = loadFixture("rules_200.json")
        val candidates = payload.getJSONArray("candidates")
        val byId = (0 until candidates.length()).associateBy(
            { candidates.getJSONObject(it).getString("id") },
            { candidates.getJSONObject(it) },
        )
        assertTrue(byId.getValue("rules:kotlin-style.md").getBoolean("enabled"))
        assertFalse(byId.getValue("rules:legacy-java.md").getBoolean("enabled"))
        val skipped = payload.getJSONArray("skipped").getJSONObject(0)
        assertEquals("disabled_by_user", skipped.getString("reason"))
    }

    @Test
    fun skillsFixtureCarriesEnabledFlags() {
        val payload = loadFixture("skills_200.json")
        val skills = payload.getJSONArray("skills")
        val androidBuild = skills.getJSONObject(0)
        assertEquals("android-build", androidBuild.getString("name"))
        assertEquals("project", androidBuild.getString("scope"))
        assertTrue(androidBuild.getBoolean("enabled"))
        assertFalse(skills.getJSONObject(1).getBoolean("enabled"))
    }

    @Test
    fun conversationUsageFixtureIncludesTokenTotals() {
        val payload = loadFixture("usage_conversation_200.json")
        val totals = payload.getJSONObject("totals")
        assertEquals(2, totals.getInt("turns"))
        assertEquals(22400, totals.getInt("input_tokens"))
        assertEquals(6800, totals.getInt("output_tokens"))
        assertEquals(18, totals.getInt("tool_calls"))
        assertEquals(0.21, totals.getDouble("cost_usd"), 1e-9)
        assertTrue(totals.getBoolean("cost_available"))
        val turn = payload.getJSONArray("turns").getJSONObject(0)
        assertEquals(0.7097, turn.getDouble("cached_ratio"), 1e-9)
        assertTrue(turn.has("duration_seconds"))
    }

    @Test
    fun usageSummaryFixtureGroupsByModelAndDay() {
        val payload = loadFixture("usage_summary_200.json")
        assertEquals("demo-app", payload.getString("project_id"))
        assertEquals(30, payload.getInt("days"))
        assertEquals(14.8, payload.getJSONObject("totals").getDouble("cost_usd"), 1e-9)
        val byModel = payload.getJSONArray("by_model")
        assertEquals(1, byModel.length())
        assertEquals("gpt-4o", byModel.getJSONObject(0).getString("model"))
        val byDay = payload.getJSONArray("by_day")
        assertTrue(byDay.length() >= 1)
        assertTrue(byDay.getJSONObject(0).has("date"))
    }
}
