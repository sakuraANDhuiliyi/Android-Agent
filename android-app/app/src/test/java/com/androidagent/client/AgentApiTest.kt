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
    fun parseErrorMessageHandlesObjectDetail() {
        val msg = AgentApi.parseErrorMessage("""{"detail":{"message":"conflict"}}""", 409)
        assertTrue(msg.contains("conflict") || msg.contains("message"))
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
