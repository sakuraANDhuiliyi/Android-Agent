package com.androidagent.client

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class JobRecoveryApiTest {
    @Test fun `first recovery and idempotent repeat accept 201 and 200 with the same job`() {
        for (status in listOf(201, 200)) {
            var request: Request? = null
            val api = api(status, """{"job":{"id":"recovered","project_id":"p","conversation_id":"c","status":"paused","can_recover":false}}""") { request = it }
            val job = api.recoverJob("original")
            assertEquals("/api/jobs/original/recover", request!!.url.encodedPath)
            assertEquals("POST", request!!.method)
            val body = okio.Buffer().also { request!!.body!!.writeTo(it) }.readUtf8()
            assertEquals(0, JSONObject(body).length())
            assertEquals("recovered", job.id)
            assertEquals("c", job.conversationId)
            assertEquals("paused", job.status)
            assertFalse(job.canRecover)
        }
    }

    @Test fun `legacy cached snapshots keep recovery disabled and new snapshots retain the link`() {
        val api = api(200, "{}")
        val old = api.decodeTasks(JSONObject("""{"jobs":[{"id":"old","status":"interrupted"},{"id":"nulls","can_recover":null,"recovery_job_id":null}]}"""))
        assertTrue(old.all { !it.canRecover && it.recoveryJobId == null })
        // TaskRepository persists the API envelope verbatim. Test its serialization round trip.
        val snapshot = JSONObject("""{"jobs":[{"id":"original","can_recover":false,"recovery_job_id":"recovered"},{"id":"recoverable","can_recover":true}]}""")
        val restored = api.decodeTasks(JSONObject(snapshot.toString()))
        assertEquals("recovered", restored[0].recoveryJobId)
        assertFalse(restored[0].canRecover)
        assertTrue(restored[1].canRecover)
    }

    @Test fun `recovery conflict preserves the actionable server message`() {
        try {
            api(409, """{"detail":"任务已不再满足恢复条件"}""").recoverJob("old")
            fail("Expected conflict")
        } catch (error: ApiException) {
            assertTrue(error.isConflict)
            assertEquals("任务已不再满足恢复条件", error.message)
        }
    }

    private fun api(status: Int, body: String, inspect: (Request) -> Unit = {}): AgentApi {
        val client = OkHttpClient.Builder().addInterceptor { chain ->
            inspect(chain.request())
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1)
                .code(status).message("Synthetic response")
                .body(body.toResponseBody("application/json".toMediaType())).build()
        }.build()
        return AgentApi("https://recovery.test", "synthetic-token", client)
    }
}
