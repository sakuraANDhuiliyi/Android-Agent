package com.androidagent.client

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class AgentApiTest {

    private lateinit var server: MockWebServer
    private lateinit var api: AgentApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = AgentApi(server.url("/").toString().trimEnd('/'), "tok-1")
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun parsesHealthAndProjects() {
        server.enqueue(
            MockResponse().setBody(
                """{"status":"ok","user_id":"u1","provider":"p","model":"m","api_key_configured":true,"lan_ip":"1.2.3.4","port":8000}""",
            ),
        )
        val health = api.health()
        assertEquals("u1", health.userId)
        assertTrue(health.apiKeyConfigured)

        server.enqueue(
            MockResponse().setBody(
                """{"projects":[{"id":"p1","name":"Demo","package":"com.demo","has_apk":true,"latest_status":"succeeded","latest_task_id":"j1"}]}""",
            ),
        )
        val projects = api.listProjects()
        assertEquals(1, projects.size)
        assertEquals("p1", projects[0].id)
        assertTrue(projects[0].hasApk)
    }

    @Test
    fun buildLogPageSendsOffsetLimitAndParsesEnvelope() {
        server.enqueue(
            MockResponse().setBody(
                """{"job_id":"j1","content":"cdef","offset":2,"limit":4,"total_size":10,"has_more":true}""",
            ),
        )
        val page = api.getTaskBuildLogPage("j1", offset = 2, limit = 4)
        assertEquals("cdef", page.content)
        assertEquals(2, page.offset)
        assertEquals(10L, page.totalSize)
        assertTrue(page.hasMore)
        val path = server.takeRequest().path.orEmpty()
        assertTrue(path.startsWith("/api/jobs/j1/log"))
        assertTrue(path.contains("offset=2"))
        assertTrue(path.contains("limit=4"))
    }

    @Test
    fun accountAndDeviceSessionFlowUsesCloudEndpoints() {
        server.enqueue(MockResponse().setBody(
            """{"account":{"user_id":"u1","email":"linchu@example.com","display_name":"林初","email_verified":true},"token":"new-token","session_id":"ses_phone","requires_verification":false}""",
        ))
        val auth = api.login(
            "linchu@example.com",
            "secure-123",
            DeviceDescriptor("phone", "Pixel 8 Pro", platform = "Android 15"),
        )
        assertEquals("u1", auth.account.userId)
        assertEquals("new-token", auth.token)
        val loginRequest = server.takeRequest()
        assertEquals("/api/auth/login", loginRequest.path)
        assertTrue(loginRequest.body.readUtf8().contains("Pixel 8 Pro"))

        server.enqueue(MockResponse().setBody(
            """{"devices":[{"session_id":"ses_phone","device_id":"phone","device_name":"Pixel 8 Pro","device_type":"android","platform":"Android 15","app_version":"1.0","created_at":"now","last_seen_at":"now","current":true},{"session_id":"ses_web","device_id":"web","device_name":"MacBook Pro","device_type":"desktop","platform":"macOS","app_version":"1.0","created_at":"before","last_seen_at":"before","current":false}]}""",
        ))
        val devices = api.listDevices()
        assertEquals(2, devices.size)
        assertTrue(devices.first().current)
        assertEquals("MacBook Pro", devices.last().deviceName)
        assertEquals("/api/devices", server.takeRequest().path)

        server.enqueue(MockResponse().setResponseCode(204))
        api.revokeDevice("ses_web")
        assertEquals("DELETE", server.takeRequest().method)
    }

    @Test
    fun accountLoginErrorKeepsServerCodeAndMessage() {
        server.enqueue(MockResponse().setResponseCode(401).setBody(
            """{"detail":{"message":"密码错误","code":"invalid_password"},"error":{"schema_version":1,"code":"invalid_password","retryable":false,"user_message":"密码错误"}}""",
        ))
        try {
            api.login("user@example.com", "bad", DeviceDescriptor("phone", "Phone"))
            fail()
        } catch (e: ApiException) {
            assertEquals("invalid_password", e.errorCode)
            assertEquals("密码错误", e.message)
        }
    }

    @Test
    fun conversationPaginationAndRename() {
        server.enqueue(
            MockResponse().setBody(
                """{"conversations":[{"id":"c1","project_id":"p1","title":"A","status":"active","created_at":1.0,"updated_at":2.0}]}""",
            ),
        )
        val list = api.listConversations("p1")
        assertEquals("c1", list[0].id)
        assertEquals("GET", server.takeRequest().method)

        server.enqueue(
            MockResponse().setBody(
                """{"conversation_id":"c1","events":[{"seq":1,"type":"user_message"},{"seq":2,"type":"assistant_message"}],"next_after_seq":2,"has_more":false}""",
            ),
        )
        val page = api.listConversationEvents("c1", afterSeq = 0, limit = 50)
        assertEquals(2, page.events.size)
        assertEquals(2, page.nextAfterSeq)
        assertFalse(page.hasMore)
        server.takeRequest()

        server.enqueue(MockResponse().setBody("""{"id":"c1","project_id":"p1","title":"Renamed","status":"active"}"""))
        val renamed = api.renameConversation("c1", "Renamed")
        assertEquals("Renamed", renamed.title)
        assertEquals("PATCH", server.takeRequest().method)
    }

    @Test
    fun mapsIsolationErrorsToUnavailable() {
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"对话不存在: c-other"}"""))
        try {
            api.getConversation("c-other")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertTrue(e.isNotFound)
            assertEquals("资源不存在或无权访问", e.message)
        }

        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"forbidden"}"""))
        try {
            api.getJob("j-other")
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertTrue(e.isForbidden)
            assertEquals("无权访问该资源", e.message)
        }
    }

    @Test
    fun approvalResolveFourOutcomes() {
        // pending list：真实契约无顶层 status/risk（risk 在 payload 内）
        server.enqueue(
            MockResponse().setBody(
                """{"job_id":"j1","approvals":[{"id":"a1","kind":"download","payload":{"url":"https://x","risk":"network"}}]}""",
            ),
        )
        val pending = api.listApprovals("j1")
        assertEquals(1, pending.size)
        assertEquals("pending", pending[0].status)
        assertEquals("network", pending[0].risk)

        // approved：resolve 端点返回 decision 字段
        server.enqueue(MockResponse().setBody("""{"approval":{"id":"a1","kind":"download","decision":"approved","payload":{}}}"""))
        assertEquals("approved", api.resolveApproval("j1", "a1", true).status)

        // rejected
        server.enqueue(MockResponse().setBody("""{"approval":{"id":"a1","kind":"download","decision":"rejected","payload":{}}}"""))
        assertEquals("rejected", api.resolveApproval("j1", "a1", false).status)

        // timeout-like conflict
        server.enqueue(MockResponse().setResponseCode(409).setBody("""{"detail":"timeout"}"""))
        try {
            api.resolveApproval("j1", "a1", true)
            fail()
        } catch (e: ApiException) {
            assertTrue(e.isConflict)
        }

        // canceled / missing
        server.enqueue(MockResponse().setResponseCode(404).setBody("""{"detail":"gone"}"""))
        try {
            api.resolveApproval("j1", "a1", false)
            fail()
        } catch (e: ApiException) {
            assertTrue(e.isNotFound)
        }
    }

    @Test
    fun largeDiffIsTruncated() {
        val big = "x".repeat(30_000)
        server.enqueue(
            MockResponse().setBody(
                JSONObject()
                    .put(
                        "files",
                        org.json.JSONArray().put(
                            JSONObject().put("path", "a.kt").put("change", "modified").put("patch", big),
                        ),
                    )
                    .toString(),
            ),
        )
        val diff = api.getDiff("p1")
        assertEquals(1, diff.files.size)
        assertTrue(diff.files[0].truncated)
        assertTrue((diff.files[0].patch?.length ?: 0) <= 20_000)
    }

    @Test
    fun checkpointRestoreConflictMapped() {
        server.enqueue(
            MockResponse().setResponseCode(409).setBody(
                """{"detail":{"ok":false,"conflicts":["app/src/Main.kt"],"message":"manual changes"}}""",
            ),
        )
        try {
            api.restoreCheckpoint("p1", "cp1")
            fail()
        } catch (e: ApiException) {
            assertTrue(e.isConflict)
        }
    }

    @Test
    fun jobMessageActions() {
        server.enqueue(MockResponse().setBody("""{"job":{"id":"j1","project_id":"p1","status":"paused","events":[],"changed_files":[]}}"""))
        assertEquals("paused", api.pauseJob("j1").status)
        val pauseReq = server.takeRequest()
        assertEquals("Bearer tok-1", pauseReq.getHeader("Authorization"))
        assertTrue(pauseReq.path!!.endsWith("/pause"))

        server.enqueue(MockResponse().setBody("""{"job":{"id":"j1","project_id":"p1","status":"running","events":[],"changed_files":[]}}"""))
        assertEquals("running", api.resumeJob("j1").status)
        assertTrue(server.takeRequest().path!!.endsWith("/resume"))

        server.enqueue(MockResponse().setBody("""{"job_id":"j1","message":{"id":1,"type":"steer"}}"""))
        api.steerJob("j1", "focus on login")
        val steerReq = server.takeRequest()
        assertTrue(steerReq.path!!.contains("/messages"))
    }

    @Test
    fun parsesDisplayStatusFromJobDto() {
        server.enqueue(
            MockResponse().setBody(
                """{"job":{"id":"j1","project_id":"p1","status":"paused","cancel_requested":true,"display_status":"cancel_requested","status_label":"正在停止","events":[],"changed_files":[]}}""",
            ),
        )
        val job = api.getJob("j1")
        assertEquals("paused", job.status)
        assertEquals("cancel_requested", job.displayStatus)
        assertEquals("正在停止", job.statusLabel)
        assertEquals("cancel_requested", job.resolvedStatus())
        assertTrue(job.cancelRequested)
    }

    @Test
    fun parseErrorMessageHandlesObjectDetail() {
        val msg = AgentApi.parseErrorMessage("""{"detail":{"message":"conflict"}}""", 409)
        assertTrue(msg.contains("conflict") || msg.contains("message"))
    }

    @Test
    fun parseErrorEnvelopePrefersStructuredUserMessage() {
        val envelope = AgentApi.parseErrorEnvelope(
            """{"detail":"任务已结束","error":{"schema_version":1,"code":"conflict","retryable":false,"user_message":"任务已结束"}}""",
            409,
        )
        assertEquals("conflict", envelope.code)
        assertFalse(envelope.retryable)
        assertEquals("任务已结束", envelope.userMessage)
    }
}

class JobWatcherFallbackTest {

    @Test
    fun fallsBackToPollingWhenWebsocketFails() {
        val server = MockWebServer()
        server.start()
        // Provide enough poll responses; WebSocket upgrade may consume one request.
        repeat(6) {
            val status = if (it < 3) "running" else "succeeded"
            server.enqueue(
                MockResponse().setBody(
                    """{"job":{"id":"j1","project_id":"p1","status":"$status","events":[{"id":1,"type":"text","content":"hi"},{"id":2,"type":"completed"}],"changed_files":[]}}""",
                ),
            )
        }
        val api = AgentApi(server.url("/").toString().trimEnd('/'), "tok")
        val done = CountDownLatch(1)
        val events = mutableListOf<String>()
        val scope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO)
        val watcher = JobWatcher(
            api = api,
            scope = scope,
            onEvent = { events.add(it.optString("type")) },
            onJob = {},
            onDone = { done.countDown() },
            onError = {},
        )
        watcher.start("j1", afterEventId = 0)
        assertTrue("watcher should finish via polling fallback", done.await(15, TimeUnit.SECONDS))
        watcher.stop()
        server.shutdown()
        assertTrue(events.isNotEmpty())
    }
}
