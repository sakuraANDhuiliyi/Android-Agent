package com.androidagent.client

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

internal fun verificationFixture(jobId: String = "j"): JSONObject = JSONObject("""{
  "schema_version":1,"scope":"job","job_id":"$jobId",
  "build":{"task":"assembleDebug","state":"passed","run_id":"build-1","evidence_time":1700000000,
    "duration_ms":1200,"source_match":"match","reason":null},
  "unit_tests":{"task":"testDebugUnitTest","state":"passed","run_id":"test-1","evidence_time":1700000002,
    "duration_ms":500,"source_match":"match","reason":null,"counts":{"total":8,"passed":7,"failed":0,"skipped":1}},
  "installation":{"state":"unknown","evidence_time":null,"source_match":"unknown","reason":"no_device_receipt"}
}""")

class DeliveryVerificationApiTest {
    private fun job(verification: JSONObject? = verificationFixture()) = JSONObject()
        .put("id", "j").put("project_id", "p").put("conversation_id", "c").put("status", "succeeded")
        .put("verification", verification ?: JSONObject.NULL)
    private fun api(body: JSONObject, status: Int = 200): AgentApi = AgentApi(
        "https://verification.test", "synthetic-token", OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status)
                .message("Synthetic response").body(body.toString().toResponseBody("application/json".toMediaType())).build()
        }.build(),
    )
    private fun parse(value: JSONObject?): DeliveryVerification = api(JSONObject().put("job", job(value))).getJob("j").verification

    @Test fun `list detail recovery and cached JSON round trips expose the same job bound evidence`() {
        val payload = job()
        val expected = parse(verificationFixture())
        assertTrue(expected.build.verified)
        assertTrue(expected.tests.verified)
        assertEquals(VerificationTestCounts(8, 7, 0, 1), expected.tests.counts)
        val snapshot = JSONObject().put("jobs", JSONArray().put(payload))
        assertEquals(expected, api(snapshot).listJobs("p", "c").single().verification)
        assertEquals(expected, api(snapshot).decodeTasks(JSONObject(snapshot.toString())).single().verification)
        assertEquals(expected, api(JSONObject().put("job", payload), 201).recoverJob("source").verification)
    }

    @Test fun `missing legacy foreign and future schemas never infer passed from raw feedback or artifacts`() {
        val invalid = listOf(null, JSONObject(), verificationFixture().put("schema_version", 2),
            verificationFixture().put("schema_version", "1"), verificationFixture("another-job"),
            verificationFixture().put("scope", "project"))
        for (fixture in invalid) {
            val value = job(fixture).put("has_apk", true).put("context", JSONObject("""{"feedback_runs":[{"task":"assembleDebug","status":"success"}]}"""))
            val parsed = api(JSONObject().put("job", value)).getJob("j")
            assertTrue(parsed.hasApk)
            assertEquals(DeliveryVerification(), parsed.verification)
        }
    }

    @Test fun `successful execution with changed or unknown sources retains the fact without verification`() {
        for (match in listOf("changed", "unknown")) {
            val fixture = verificationFixture()
            fixture.getJSONObject("build").put("source_match", match)
            fixture.getJSONObject("unit_tests").put("source_match", match)
            val evidence = parse(fixture)
            assertEquals(VerificationState.PASSED, evidence.build.state)
            assertEquals(VerificationState.PASSED, evidence.tests.state)
            assertFalse(evidence.build.verified)
            assertFalse(evidence.tests.verified)
        }
    }

    @Test fun `missing identity bad timestamps and unrecognized enums invalidate a claimed success`() {
        val corruptions = listOf<Pair<String, Any>>(
            "run_id" to JSONObject.NULL, "run_id" to "", "run_id" to 7,
            "evidence_time" to JSONObject.NULL, "evidence_time" to -1, "evidence_time" to "1700000000",
            "duration_ms" to -1, "duration_ms" to "500", "state" to "success",
            "source_match" to "current", "task" to "lintDebug",
        )
        for ((key, value) in corruptions) {
            val fixture = verificationFixture()
            fixture.getJSONObject("build").put(key, value)
            assertEquals("$key=$value", VerificationState.UNKNOWN, parse(fixture).build.state)
        }
    }

    @Test fun `zero tests all skipped failures malformed or incomplete counts cannot claim passing tests`() {
        val counts = listOf(
            """{"total":0,"passed":0,"failed":0,"skipped":0}""",
            """{"total":2,"passed":0,"failed":0,"skipped":2}""",
            """{"total":2,"passed":1,"failed":1,"skipped":0}""",
            """{"total":2,"passed":2,"failed":0,"skipped":1}""",
            """{"total":2,"passed":2,"failed":-1,"skipped":1}""",
            """{"total":2,"passed":"2","failed":0,"skipped":0}""",
            """{"total":2,"passed":1.5,"failed":0,"skipped":0.5}""",
            """{"total":2,"passed":2,"skipped":0}""",
        )
        for (value in counts) {
            val fixture = verificationFixture()
            fixture.getJSONObject("unit_tests").put("counts", JSONObject(value))
            assertEquals(value, VerificationState.UNKNOWN, parse(fixture).tests.state)
        }
        val fixture = verificationFixture()
        fixture.getJSONObject("unit_tests").remove("counts")
        assertEquals(VerificationState.UNKNOWN, parse(fixture).tests.state)
    }

    @Test fun `no tests skipped failed canceled and interrupted remain distinct nonpassing results`() {
        for ((wire, expected) in mapOf("no_tests" to VerificationState.NO_TESTS, "skipped" to VerificationState.SKIPPED,
            "failed" to VerificationState.FAILED, "canceled" to VerificationState.CANCELED, "interrupted" to VerificationState.INTERRUPTED)) {
            val fixture = verificationFixture()
            val step = fixture.getJSONObject("unit_tests").put("state", wire)
            if (wire == "no_tests") step.put("counts", JSONObject("""{"total":0,"passed":0,"failed":0,"skipped":0}"""))
            if (wire == "skipped") step.put("counts", JSONObject("""{"total":2,"passed":0,"failed":0,"skipped":2}"""))
            assertEquals(expected, parse(fixture).tests.state)
            assertFalse(parse(fixture).tests.verified)
        }
    }

    @Test fun `every completed execution state needs a run identity and completion time`() {
        for (state in listOf("passed", "failed", "canceled", "interrupted", "no_tests", "skipped")) {
            for (missing in listOf("run_id", "evidence_time")) {
                val fixture = verificationFixture()
                fixture.getJSONObject("unit_tests").put("state", state).put(missing, JSONObject.NULL)
                assertEquals("$state missing $missing", VerificationStep(), parse(fixture).tests)
            }
        }
    }

    @Test fun `details show each command time and duration rather than the other command metrics`() {
        val report = JSONObject().put("project_id", "p").put("job_id", "j").put("job_status", "succeeded")
            .put("verification", verificationFixture()).put("build", JSONObject().put("duration_ms", 999999))
        val view = FeedbackPresentation.from(api(report).feedback("p", "j"))
        assertTrue(view.duration.endsWith("1.2 秒"))
        assertTrue(view.tests.endsWith("0.5 秒"))
        assertFalse(view.duration.contains("时间未知"))
        assertNotEquals(view.verification.buildDetails, view.verification.testDetails)
    }

    @Test fun `feedback keeps explicit job binding and rejects cross task and project fallback`() {
        val report = JSONObject().put("project_id", "p").put("job_id", "j").put("verification", verificationFixture())
        assertTrue(FeedbackPresentation.from(api(report).feedback("p", "j")).verification.buildPassed)
        for ((key, value) in listOf("project_id" to "other", "job_id" to "old")) {
            val foreign = JSONObject(report.toString()).put(key, value)
            try {
                api(foreign).feedback("p", "j")
                fail("Expected bound report rejection")
            } catch (error: IllegalStateException) { assertTrue(error.message!!.contains("当前任务或项目")) }
        }
        report.put("artifact", JSONObject().put("job_id", "old"))
        assertFalse(api(report).feedback("p", "j").has("artifact"))
    }

    @Test fun `feedback rejects a foreign verification even if the outer report belongs to this job`() {
        val report = JSONObject().put("project_id", "p").put("job_id", "j").put("verification", verificationFixture("old"))
        val view = FeedbackPresentation.from(api(report).feedback("p", "j"))
        assertFalse(view.verification.buildPassed)
        assertTrue(view.tests.contains("结果未知"))
    }

    @Test fun `feedback cancellation keeps prior evidence but does not label the task verified`() {
        val report = JSONObject().put("project_id", "p").put("job_id", "j").put("job_status", "canceled")
            .put("verification", verificationFixture())
        val view = FeedbackPresentation.from(api(report).feedback("p", "j"))
        assertFalse(view.verification.buildPassed)
        assertTrue(view.verification.build.contains("此前执行通过"))
        assertTrue(view.tests.contains("任务未完成"))
    }

    @Test fun `known unknown evidence reasons explain the gap without rendering arbitrary server text`() {
        val reasons = mapOf("legacy_evidence" to "旧版记录缺少验证凭据", "no_fresh_report" to "未收到本次测试报告",
            "invalid_report" to "报告不完整或格式异常")
        for ((reason, label) in reasons) {
            val fixture = verificationFixture()
            fixture.getJSONObject("unit_tests").put("state", "unknown").put("reason", reason).put("counts", JSONObject.NULL)
            val evidence = parse(fixture)
            assertEquals(reason, evidence.tests.reason)
            assertTrue(DeliveryVerificationPresentation.from(evidence).tests.contains(label))
        }
        val fixture = verificationFixture()
        fixture.getJSONObject("unit_tests").put("state", "unknown").put("reason", "arbitrary server text")
        assertNull(parse(fixture).tests.reason)
        assertFalse(DeliveryVerificationPresentation.from(parse(fixture)).tests.contains("arbitrary server text"))
    }
}
