package com.androidagent.client

import com.androidagent.client.core.agent.ConversationSessionPrefs
import com.androidagent.client.core.agent.JobEventWatcher
import com.androidagent.client.core.database.CachedConversationEventEntity
import com.androidagent.client.feature.conversation.ConversationRepository
import com.androidagent.client.feature.conversation.ConversationSignal
import com.androidagent.client.feature.conversation.ConversationUiState
import com.androidagent.client.feature.conversation.ConversationViewModel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicInteger

/**
 * ConversationViewModel：缓存优先加载、任务绑定、发送与审批自动通过。
 * Watcher / Session 用假实现，网络用 MockWebServer。
 */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class ConversationViewModelTest {

    private class FakeSession : ConversationSessionPrefs {
        override var selectedJobId: String? = null
        override var selectedProviderId: String = "auto"
        override var guestMode: Boolean = false
        override var guestRemaining: Int = 3
        val cursors = HashMap<String, Long>()
        override fun eventCursor(jobId: String): Long = cursors[jobId] ?: 0L
        override fun setEventCursor(jobId: String, cursor: Long) {
            cursors[jobId] = cursor
        }
    }

    private class RecordingWatcher : JobEventWatcher {
        val starts = mutableListOf<Pair<String, Long>>()
        override fun start(jobId: String, afterEventId: Long) {
            starts += jobId to afterEventId
        }

        override fun currentCursor(): Long = 7L
        override fun stop() {}
    }

    private lateinit var server: MockWebServer
    private lateinit var api: AgentApi
    private lateinit var eventDao: FakeConversationEventDao
    private lateinit var jobDao: FakeJobDao
    private lateinit var repository: ConversationRepository
    private val session = FakeSession()
    private val watcher = RecordingWatcher()
    private val trackedJobs = ConcurrentLinkedQueue<String>()
    private val syncCount = AtomicInteger()
    private val signals = ConcurrentLinkedQueue<ConversationSignal>()
    private val sources = ConcurrentLinkedQueue<ConversationUiState.Source>()
    private lateinit var collectorScope: CoroutineScope
    private lateinit var vm: ConversationViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server = MockWebServer()
        server.start()
        api = AgentApi(server.url("/").toString().trimEnd('/'), "tok-1")
        eventDao = FakeConversationEventDao()
        jobDao = FakeJobDao()
        repository = ConversationRepository(
            api,
            eventDao,
            FakeConversationDao(),
            jobDao,
            FakeApprovalDao(),
        )
        vm = buildViewModel(ApprovalAllowlist(mutableSetOf()))
        attachCollectors()
    }

    private fun attachCollectors() {
        collectorScope = CoroutineScope(Dispatchers.Unconfined)
        collectorScope.launch { vm.signals.collect { signals += it } }
        collectorScope.launch { vm.state.collect { sources += it.source } }
    }

    @After
    fun tearDown() {
        collectorScope.cancel()
        Dispatchers.resetMain()
        server.shutdown()
    }

    private fun buildViewModel(allowlist: ApprovalAllowlist): ConversationViewModel =
        ConversationViewModel(
            api = api,
            repository = repository,
            session = session,
            allowlist = allowlist,
            persistAllowlist = { },
            trackNewJob = { trackedJobs += it },
            scheduleTaskSync = { syncCount.incrementAndGet() },
            watcherFactory = { _, _, _, _, _, _ -> watcher },
        )

    private fun awaitTrue(timeoutMs: Long = 5000, condition: () -> Boolean) {
        val start = System.currentTimeMillis()
        while (!condition()) {
            if (System.currentTimeMillis() - start > timeoutMs) {
                throw AssertionError("condition not met within ${timeoutMs}ms")
            }
            Thread.sleep(20)
        }
    }

    // ---- 响应构造 ----

    private fun jsonResponse(body: JSONObject): MockResponse =
        MockResponse().setBody(body.toString()).setHeader("Content-Type", "application/json")

    private fun eventsPage(events: List<JSONObject>, hasMore: Boolean = false): MockResponse = jsonResponse(
        JSONObject()
            .put("conversation_id", "conv-1")
            .put("events", JSONArray(events))
            .put("has_more", hasMore)
            .put("next_after_seq", JSONObject.NULL)
            .put("next_before_seq", JSONObject.NULL),
    )

    private fun conversationEvent(id: String, seq: Long, type: String, payload: JSONObject = JSONObject()): JSONObject =
        JSONObject()
            .put("id", id)
            .put("conversation_id", "conv-1")
            .put("event_type", type)
            .put("seq", seq)
            .put("turn_id", "t-1")
            .put("task_id", JSONObject.NULL)
            .put("payload", payload)
            .put("created_at", 1700000000.0)

    private fun jobBody(id: String, status: String, prompt: String = "构建一下"): MockResponse =
        jsonResponse(
            JSONObject().put(
                "job",
                JSONObject()
                    .put("id", id)
                    .put("project_id", "p-1")
                    .put("conversation_id", "conv-1")
                    .put("prompt", prompt)
                    .put("status", status)
                    .put("created_at", 1700000000.0),
            ),
        )

    private fun jobsBody(vararg jobs: JSONObject): MockResponse = jsonResponse(
        JSONObject().put("jobs", JSONArray().apply { jobs.forEach { put(it) } }),
    )

    private fun jobRef(id: String, status: String): JSONObject =
        JSONObject().put("id", id).put("project_id", "p-1").put("conversation_id", "conv-1")
            .put("prompt", "构建一下").put("status", status)

    private fun approvalsBody(vararg approvals: JSONObject): MockResponse = jsonResponse(
        JSONObject().put("approvals", JSONArray().apply { approvals.forEach { put(it) } }),
    )

    /** start + 空事件页 + 空任务列表（无任务绑定）。 */
    private fun startWithEmptyServer() {
        server.enqueue(eventsPage(emptyList()))
        server.enqueue(jobsBody())
        vm.start("p-1", "conv-1")
        awaitTrue { vm.state.value.source == ConversationUiState.Source.FRESH }
        // 等任务列表请求也落地，避免后续"无网络调用"断言误把在途请求计进去
        awaitTrue { server.requestCount >= 2 }
    }

    /** 断言用路径（剥离 query 参数）。 */
    private fun okhttp3.mockwebserver.RecordedRequest.pathOnly(): String =
        path?.substringBefore('?') ?: ""

    // ---- 用例 ----

    @Test
    fun `start renders cache first then fresh server data`() {
        val cachedEvent = CachedConversationEventEntity.fromEvent(
            conversationEvent("ev-1", 1, "user_message", payload = JSONObject().put("content", "加个夜间模式")),
            "conv-1",
            System.currentTimeMillis(),
        )!!
        runBlocking { eventDao.upsertAll(listOf(cachedEvent)) }

        server.enqueue(eventsPage(listOf(conversationEvent("ev-2", 2, "assistant_message", payload = JSONObject().put("content", "完成")))))
        server.enqueue(jobsBody(jobRef("job-1", "running")))
        server.enqueue(jobBody("job-1", "running"))

        vm.start("p-1", "conv-1")

        awaitTrue { vm.state.value.source == ConversationUiState.Source.FRESH }

        // 顺序：CACHE 渲染必须先于 FRESH
        val sourceList = sources.toList()
        assertTrue(sourceList.contains(ConversationUiState.Source.CACHE))
        assertTrue(sourceList.indexOf(ConversationUiState.Source.CACHE) < sourceList.indexOf(ConversationUiState.Source.FRESH))

        // 缓存 + 服务端事件都进入时间线
        val userTexts = vm.store.sortedItems()
            .filter { it.type == TimelineStore.ItemType.USER }
            .map { it.content.optString("text") }
        assertTrue(userTexts.contains("加个夜间模式"))

        // 任务绑定：watcher 启动、会话选中、任务快照落库（等待异步链路完成再断言）
        awaitTrue { watcher.starts.isNotEmpty() && jobDao.rows.containsKey("job-1") }
        assertEquals(listOf("job-1" to 0L), watcher.starts)
        assertEquals("job-1", session.selectedJobId)
        assertEquals("job-1", vm.state.value.jobId)
    }

    @Test
    fun `guest quota exhaustion blocks send without any network call`() {
        startWithEmptyServer()
        session.guestMode = true
        session.guestRemaining = 0
        val requestsBefore = server.requestCount

        vm.send("你好", steer = false, contexts = emptyList())

        awaitTrue { signals.contains(ConversationSignal.GuestQuotaExhausted) }
        assertEquals(requestsBefore, server.requestCount)
    }

    @Test
    fun `sendAsk creates job tracks it and resets composer`() {
        startWithEmptyServer()

        server.enqueue(jobBody("job-9", "running"))
        server.enqueue(jobBody("job-9", "running"))

        vm.send("构建一下", steer = false, contexts = emptyList())

        awaitTrue { signals.contains(ConversationSignal.ComposerReset) }
        awaitTrue { vm.state.value.jobId == "job-9" }
        // 请求顺序：events, jobs, ask, job 详情（running 任务无需拉审批列表）
        awaitTrue { server.requestCount >= 4 }

        assertEquals(listOf("job-9"), trackedJobs.toList())
        assertEquals("job-9", watcher.starts.lastOrNull()?.first)
        // 乐观用户消息在服务端确认前就已进入时间线
        assertTrue(
            vm.store.sortedItems().any {
                it.type == TimelineStore.ItemType.USER && it.content.optString("text") == "构建一下"
            },
        )
        assertEquals("/api/conversations/conv-1/events", server.takeRequest().pathOnly())
        assertEquals("/api/jobs", server.takeRequest().pathOnly())
        val askRequest = server.takeRequest()
        assertEquals("/api/conversations/conv-1/ask", askRequest.pathOnly())
        assertEquals("/api/jobs/job-9", server.takeRequest().pathOnly())
    }

    @Test
    fun `allowlisted pending approval is auto approved`() {
        val payload = JSONObject().put("command", "./gradlew test")
        val allowlist = ApprovalAllowlist(mutableSetOf(ApprovalAllowlist.fingerprint("run_command", payload)))
        collectorScope.cancel()
        vm = buildViewModel(allowlist)
        attachCollectors()

        server.enqueue(eventsPage(emptyList()))
        server.enqueue(jobsBody(jobRef("job-1", "awaiting_approval")))
        server.enqueue(jobBody("job-1", "awaiting_approval"))
        server.enqueue(
            approvalsBody(
                JSONObject()
                    .put("id", "ap-1")
                    .put("kind", "run_command")
                    .put("status", "pending")
                    .put("payload", payload)
                    .put("created_at", 1700000000.0),
            ),
        )
        server.enqueue(
            jsonResponse(
                JSONObject().put(
                    "approval",
                    JSONObject().put("id", "ap-1").put("kind", "run_command")
                        .put("decision", "approved").put("payload", payload),
                ),
            ),
        )

        vm.start("p-1", "conv-1")

        // 审批缓存状态被更新为 approved，store 中不再 pending
        awaitTrue {
            runBlocking { repository.cachedApprovals("job-1") }
                .any { it.id == "ap-1" && it.status == "approved" }
        }
        awaitTrue { vm.store.pendingApprovals().none { it.approvalId == "ap-1" } }

        // 请求顺序：events, jobs, job 详情, approvals 列表, resolve
        assertEquals("/api/conversations/conv-1/events", server.takeRequest().pathOnly())
        assertEquals("/api/jobs", server.takeRequest().pathOnly())
        assertEquals("/api/jobs/job-1", server.takeRequest().pathOnly())
        assertEquals("/api/jobs/job-1/approvals", server.takeRequest().pathOnly())
        val resolve = server.takeRequest()
        assertEquals("/api/jobs/job-1/approvals/ap-1", resolve.pathOnly())
    }
}
