package com.androidagent.client

import com.androidagent.client.core.database.CachedConversationEventEntity
import com.androidagent.client.core.database.ConversationEntity
import com.androidagent.client.feature.conversation.ConversationRepository
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * ConversationRepository：REST 拉取 + Room 落库 + 缓存回放的编排逻辑。
 * DAO 用内存假实现，网络用 MockWebServer，不依赖 Room 运行时。
 */
class ConversationRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var api: AgentApi
    private lateinit var eventDao: FakeConversationEventDao
    private lateinit var conversationDao: FakeConversationDao
    private lateinit var jobDao: FakeJobDao
    private lateinit var approvalDao: FakeApprovalDao
    private lateinit var repository: ConversationRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        api = AgentApi(server.url("/").toString().trimEnd('/'), "tok-1")
        eventDao = FakeConversationEventDao()
        conversationDao = FakeConversationDao()
        jobDao = FakeJobDao()
        approvalDao = FakeApprovalDao()
        repository = ConversationRepository(api, eventDao, conversationDao, jobDao, approvalDao)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun conversationEvent(
        id: String,
        seq: Long,
        type: String,
        turnId: String? = "t-1",
        payload: JSONObject = JSONObject(),
        createdAt: Double = 1700000000.0,
    ): JSONObject = JSONObject()
        .put("id", id)
        .put("conversation_id", "conv-1")
        .put("event_type", type)
        .put("seq", seq)
        .putOpt("turn_id", turnId ?: JSONObject.NULL)
        .putOpt("task_id", JSONObject.NULL)
        .put("payload", payload)
        .put("created_at", createdAt)

    private fun eventsPageResponse(events: List<JSONObject>, hasMore: Boolean = false): MockResponse {
        val body = JSONObject()
            .put("conversation_id", "conv-1")
            .put("events", JSONArray(events))
            .put("has_more", hasMore)
            .put("next_after_seq", JSONObject.NULL)
            .put("next_before_seq", if (events.isEmpty()) JSONObject.NULL else events.first().optLong("seq") - 1)
        return MockResponse().setBody(body.toString()).setHeader("Content-Type", "application/json")
    }

    @Test
    fun `fetchEvents persists canonical events and cachedEvents replays them`() = runBlocking {
        val user = conversationEvent("ev-1", 1, "user_message", payload = JSONObject().put("content", "加个夜间模式"))
        val assistant = conversationEvent(
            "ev-2", 2, "assistant_message",
            payload = JSONObject().put("content", "好的，正在处理"),
        )
        server.enqueue(eventsPageResponse(listOf(user, assistant)))

        val page = repository.fetchEvents("conv-1")

        assertEquals(2, page.events.size)
        assertEquals(2, eventDao.rows.size)

        val cached = repository.cachedEvents("conv-1")
        assertEquals(2, cached.size)
        // 回放的 JSON 必须能被 Normalizer 重新消费
        val normalized = cached.mapNotNull { ConversationEventNormalizer.fromConversationEvent(it) }
        assertEquals(2, normalized.size)
        assertEquals("加个夜间模式", ConversationEventNormalizer.userText(normalized[0].payload))
        assertEquals("conv:ev-1", normalized[0].stableId)
        assertEquals(1L, normalized[0].seq)
    }

    @Test
    fun `events without id or seq are not cached`() = runBlocking {
        val canonical = conversationEvent("ev-1", 1, "user_message")
        val synthetic = JSONObject()
            .put("event_type", "approval_required")
            .put("task_id", "job-1")
            .put("payload", JSONObject().put("approval_id", "ap-1"))
        server.enqueue(eventsPageResponse(listOf(canonical, synthetic)))

        repository.fetchEvents("conv-1")

        assertEquals(setOf("ev-1"), eventDao.rows.keys)
    }

    @Test
    fun `pruneOlder keeps the newest events per conversation`() = runBlocking {
        val events = (1..10).map { conversationEvent("ev-$it", it.toLong(), "user_message") }
        server.enqueue(eventsPageResponse(events))

        repository.fetchEvents("conv-1")

        assertEquals(10, eventDao.rows.size)
        eventDao.pruneOlder("conv-1", keep = 3)
        assertEquals(setOf("ev-8", "ev-9", "ev-10"), eventDao.rows.keys)
    }

    @Test
    fun `replaceConversations upserts and removes stale entries`() = runBlocking {
        val old = ConversationInfo("c-old", "p-1", "旧会话", "active", 1.0, 1.0)
        val fresh = ConversationInfo("c-new", "p-1", "新会话", "active", 2.0, 2.0)
        conversationDao.upsertAll(listOf(ConversationEntity.from(old, 100L)))

        repository.replaceConversations(listOf(fresh))

        assertEquals(setOf("c-new"), conversationDao.rows.keys)
        assertEquals("新会话", conversationDao.rows["c-new"]?.title)
    }

    @Test
    fun `cachedConversations maps back to ConversationInfo`() = runBlocking {
        val info = ConversationInfo("c-1", "p-1", "标题", "active", 1.0, 1700000000.0, summary = "摘要", lastTurnStatus = "succeeded")
        repository.saveConversations(listOf(info))

        val cached = repository.cachedConversations(10)
        assertEquals(1, cached.size)
        val mapped = repository.cachedConversationInfo(cached.first())
        assertEquals("c-1", mapped.id)
        assertEquals("p-1", mapped.projectId)
        assertEquals("标题", mapped.title)
        assertEquals("摘要", mapped.summary)
        assertEquals(1700000000.0, mapped.updatedAt!!, 0.001)
    }

    private fun jobJson(id: String, status: String = "running", conversationId: String = "conv-1"): JSONObject =
        JSONObject()
            .put("id", id)
            .put("project_id", "p-1")
            .put("conversation_id", conversationId)
            .put("prompt", "构建一下")
            .put("status", status)
            .put("created_at", 1700000000.0)

    @Test
    fun `saveJobs snapshots status and metadata`() = runBlocking {
        server.enqueue(
            MockResponse().setBody(JSONObject().put("job", jobJson("job-1", status = "awaiting_approval")).toString())
                .setHeader("Content-Type", "application/json"),
        )
        val job = api.getJob("job-1")
        repository.saveJobs(listOf(job))

        val cached = jobDao.rows["job-1"]
        assertNotNull(cached)
        assertEquals("awaiting_approval", cached!!.status)
        assertEquals("conv-1", cached.conversationId)
        assertEquals("构建一下", cached.prompt)
        assertEquals(1, repository.cachedJobs("conv-1").size)
    }

    @Test
    fun `approval snapshot status is updated after decision`() = runBlocking {
        val approval = ApprovalInfo(
            id = "ap-1", kind = "run_command", status = "pending",
            risk = null, toolCallId = null, payload = JSONObject().put("command", "./gradlew test"),
            createdAt = 1700000000.0,
        )
        repository.saveApprovals("job-1", listOf(approval))
        assertEquals("pending", approvalDao.rows["ap-1"]?.status)

        repository.setApprovalStatus("ap-1", "approved")

        assertEquals("approved", approvalDao.rows["ap-1"]?.status)
        assertTrue(approvalDao.listPending().isEmpty())
    }

    @Test
    fun `incremental afterSeq fetch keeps history consistent`() = runBlocking {
        server.enqueue(eventsPageResponse(listOf(conversationEvent("ev-1", 1, "user_message"))))
        repository.fetchEvents("conv-1")
        server.enqueue(
            eventsPageResponse(
                listOf(conversationEvent("ev-2", 2, "assistant_message", payload = JSONObject().put("content", "完成"))),
            ),
        )
        repository.fetchEvents("conv-1", afterSeq = 1)

        val cached = repository.cachedEvents("conv-1")
        assertEquals(2, cached.size)
        assertEquals(2L, eventDao.maxSeq("conv-1"))
    }
}
