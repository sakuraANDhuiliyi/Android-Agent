package com.androidagent.client

import com.androidagent.client.ConversationEventNormalizer.Kind
import com.androidagent.client.ConversationEventNormalizer.NormalizedEvent
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * 事件标准化 + 时间线聚合 18 场景覆盖（对应任务文档第十五节）。
 */
class TimelineNormalizationTest {

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

    // ---- 事件构造辅助 ----

    private fun convEvent(
        id: String,
        type: String,
        turnId: String? = "t1",
        seq: Long,
        taskId: String? = null,
        payload: JSONObject = JSONObject(),
        createdAt: Double = 1_700_000_000.0,
    ): NormalizedEvent {
        val ev = JSONObject()
            .put("id", id)
            .put("conversation_id", "c1")
            .put("event_type", type)
            .put("seq", seq)
            .put("created_at", createdAt)
            .put("payload", payload)
        turnId?.let { ev.put("turn_id", it) }
        taskId?.let { ev.put("task_id", it) }
        return ConversationEventNormalizer.fromConversationEvent(ev)!!
    }

    private fun jobEvent(id: Long, type: String, vararg extra: Pair<String, out Any>): NormalizedEvent {
        val ev = JSONObject().put("id", id).put("type", type).put("ts", 1_700_000_000.5).put("task_id", "j1")
        extra.forEach { (k, v) -> ev.put(k, v) }
        return ConversationEventNormalizer.fromTaskEvent(ev)!!
    }

    private fun ingestOne(store: TimelineStore, ev: NormalizedEvent): Boolean = store.ingest(listOf(ev))

    private fun toolItems(store: TimelineStore) = store.sortedItems().filter { it.type == TimelineStore.ItemType.TOOL }
    private fun assistantItems(store: TimelineStore) = store.sortedItems().filter { it.type == TimelineStore.ItemType.ASSISTANT }
    private fun userItems(store: TimelineStore) = store.sortedItems().filter { it.type == TimelineStore.ItemType.USER }
    private fun approvalItems(store: TimelineStore) = store.sortedItems().filter { it.type == TimelineStore.ItemType.APPROVAL }

    // ---------- 场景 1：Conversation event_type + payload ----------

    @Test
    fun `1 conversation event maps kind stableId and payload`() {
        val ev = convEvent("id-1", "user_message", seq = 5, payload = JSONObject().put("content", "hi"))
        assertEquals(Kind.USER_MESSAGE, ev.kind)
        assertEquals("conv:id-1", ev.stableId)
        assertEquals("t1", ev.turnId)
        assertEquals(5L, ev.seq)
        assertTrue(ev.authoritative)
        assertEquals(1_700_000_000_000L, ev.timestampMs)
        assertEquals("hi", ev.payload.optString("content"))

        val tool = convEvent("id-2", "tool_call", seq = 6, payload = JSONObject().put("tool_call_id", "c1"))
        assertEquals(Kind.TOOL_CALL, tool.kind)
        val approval = convEvent("id-3", "approval_required", seq = 7)
        assertEquals(Kind.APPROVAL_REQUIRED, approval.kind)
        val turnDone = convEvent("id-4", "turn_completed", seq = 8)
        assertEquals(Kind.TURN_COMPLETED, turnDone.kind)
    }

    // ---------- 场景 2：Job type 扁平事件 ----------

    @Test
    fun `2 job flat event strips envelope and flattens payload`() {
        val ev = jobEvent(42, "tool_call", "turn_id" to "t1", "tool_call_id" to "c9", "name" to "read_file")
        assertEquals(Kind.TOOL_CALL, ev.kind)
        assertEquals("task:42", ev.stableId)
        assertEquals(42L, ev.taskEventId)
        assertEquals("j1", ev.jobId)
        assertEquals("t1", ev.turnId)
        assertFalse(ev.authoritative)
        // 信封字段剥离，业务字段平铺
        assertEquals("c9", ev.payload.optString("tool_call_id"))
        assertEquals("read_file", ev.payload.optString("name"))
        assertFalse(ev.payload.has("task_id"))
        assertFalse(ev.payload.has("type"))
    }

    // ---------- 场景 3：user_message content block ----------

    @Test
    fun `3 user message content block array is joined`() {
        val blocks = JSONArray()
            .put(JSONObject().put("type", "text").put("text", "第一段"))
            .put(JSONObject().put("type", "text").put("text", "第二段"))
        val text = ConversationEventNormalizer.userText(JSONObject().put("content", blocks))
        assertEquals("第一段\n第二段", text)

        // 字符串形态
        assertEquals("直接文本", ConversationEventNormalizer.userText(JSONObject().put("content", " 直接文本 ")))
        // 扁平 prompt 回退
        assertEquals("回退", ConversationEventNormalizer.userText(JSONObject().put("prompt", "回退")))
    }

    // ---------- 场景 4：text_delta 连续追加 ----------

    @Test
    fun `4 consecutive text deltas append into one stream buffer`() {
        val store = TimelineStore()
        assertTrue(ingestOne(store, jobEvent(1, "text_delta", "turn_id" to "t1", "delta" to "你好")))
        assertTrue(ingestOne(store, jobEvent(2, "text_delta", "turn_id" to "t1", "delta" to "，世界")))
        assertTrue(ingestOne(store, jobEvent(3, "text_delta", "turn_id" to "t1", "delta" to "!")))

        val streams = assistantItems(store)
        assertEquals(1, streams.size)
        assertEquals("你好，世界!", streams[0].content.optString("text"))
        assertTrue(streams[0].streaming)
        assertEquals("streaming", streams[0].status)
    }

    // ---------- 场景 5：text 快照替换 ----------

    @Test
    fun `5 text snapshot replaces stream content when it is a superset`() {
        val store = TimelineStore()
        ingestOne(store, jobEvent(1, "text_delta", "turn_id" to "t1", "delta" to("x".repeat(10))))
        ingestOne(store, jobEvent(2, "text", "turn_id" to "t1", "text" to ("x".repeat(10) + "尾部新增")))

        val streams = assistantItems(store)
        assertEquals(1, streams.size)
        assertEquals("x".repeat(10) + "尾部新增", streams[0].content.optString("text"))

        // 陈旧前缀（比现有内容短且是前缀）被忽略
        ingestOne(store, jobEvent(3, "text", "turn_id" to "t1", "text" to("x".repeat(5))))
        assertEquals("x".repeat(10) + "尾部新增", streams[0].content.optString("text"))
    }

    // ---------- 场景 6：assistant_message text_blocks ----------

    @Test
    fun `6 assistant text_blocks joined with newlines`() {
        val blocks = JSONArray()
            .put(JSONObject().put("type", "text").put("text", "回答第一段"))
            .put(JSONObject().put("type", "text").put("text", "回答第二段"))
        val text = ConversationEventNormalizer.assistantText(JSONObject().put("text_blocks", blocks))
        assertEquals("回答第一段\n回答第二段", text)
    }

    // ---------- 场景 7：assistant_message is_final ----------

    @Test
    fun `7 assistant is_final flag parsed`() {
        assertTrue(ConversationEventNormalizer.isFinalAssistant(JSONObject().put("is_final", true)))
        assertFalse(ConversationEventNormalizer.isFinalAssistant(JSONObject()))
        assertFalse(ConversationEventNormalizer.isFinalAssistant(JSONObject().put("is_final", false)))
    }

    // ---------- 场景 8：job.result 兼容回退 ----------

    @Test
    fun `8 job result falls back to final_message and error to error_message`() {
        server.enqueue(
            MockResponse().setBody(
                """{"job":{"id":"j9","project_id":"p1","prompt":"hi","status":"succeeded",
                   "final_message":"最终回答","error_message":"旧错误字段"}}""",
            ),
        )
        val job = api.getJob("j9")
        assertEquals("最终回答", job.result)
        assertEquals("旧错误字段", job.error)

        server.enqueue(
            MockResponse().setBody(
                """{"job":{"id":"j10","project_id":"p1","prompt":"hi","status":"failed",
                   "result":"显式结果","error":"显式错误"}}""",
            ),
        )
        val job2 = api.getJob("j10")
        assertEquals("显式结果", job2.result)
        assertEquals("显式错误", job2.error)
    }

    // ---------- 场景 9：最终回答不重复 ----------

    @Test
    fun `9 final assistant message adopts stream without duplication`() {
        val store = TimelineStore()
        ingestOne(store, jobEvent(1, "text_delta", "turn_id" to "t1", "message_id" to "m1", "delta" to "流式片"))
        ingestOne(store, jobEvent(2, "text_delta", "turn_id" to "t1", "message_id" to "m1", "delta" to "段内容"))
        ingestOne(
            store,
            convEvent(
                "id-f", "assistant_message", seq = 10,
                payload = JSONObject().put("message_id", "m1").put("text", "完整最终回答").put("is_final", true),
            ),
        )

        val answers = assistantItems(store)
        assertEquals(1, answers.size)
        assertEquals("完整最终回答", answers[0].content.optString("text"))
        assertFalse(answers[0].streaming)
        assertTrue(answers[0].isFinal)
        assertEquals("done", answers[0].status)
    }

    // ---------- 场景 10：tool_call/tool_result 按 tool_call_id 合并 ----------

    @Test
    fun `10 tool call and result merge by tool_call_id`() {
        val store = TimelineStore()
        ingestOne(
            store,
            convEvent(
                "id-tc", "tool_call", seq = 11,
                payload = JSONObject().put("tool_call_id", "call-1")
                    .put("name", "read_file")
                    .put("input", JSONObject().put("path", "a.kt")),
            ),
        )
        ingestOne(
            store,
            convEvent(
                "id-tr", "tool_result", seq = 12,
                payload = JSONObject().put("tool_call_id", "call-1")
                    .put("output", "文件内容")
                    .put("ok", true)
                    .put("duration_ms", 120),
            ),
        )

        val tools = toolItems(store)
        assertEquals(1, tools.size)
        val tool = tools[0]
        assertEquals("read_file", tool.content.optString("name"))
        assertEquals("文件内容", tool.content.optString("output"))
        assertEquals("success", tool.status)
        assertEquals(120L, tool.content.optLong("duration_ms"))
        assertEquals("call-1", tool.toolCallId)
    }

    // ---------- 场景 11：approval_required/approval_resolved 合并 ----------

    @Test
    fun `11 approval required and resolved merge into one card`() {
        val store = TimelineStore()
        ingestOne(
            store,
            convEvent(
                "id-ar", "approval_required", seq = 13,
                payload = JSONObject().put("approval_id", "ap-1")
                    .put("tool_call_id", "call-9")
                    .put("kind", "run_command")
                    .put("summary", "rm -rf build"),
            ),
        )
        // 审批与工具联动：工具转 waiting_approval
        ingestOne(
            store,
            convEvent(
                "id-tc9", "tool_call", seq = 14,
                payload = JSONObject().put("tool_call_id", "call-9").put("name", "run_command"),
            ),
        )

        val pending = store.pendingApprovals()
        assertEquals(1, pending.size)
        assertEquals("pending", approvalItems(store)[0].status)

        ingestOne(
            store,
            convEvent(
                "id-av", "approval_resolved", seq = 15,
                payload = JSONObject().put("approval_id", "ap-1").put("decision", "approved"),
            ),
        )

        assertEquals(0, store.pendingApprovals().size)
        val approval = approvalItems(store)[0]
        assertEquals("approved", approval.status)
        assertEquals("approved", approval.content.optString("decision"))
        // 本地乐观决议与事件决议一致
        store.setApprovalDecision("ap-1", "rejected")
        assertEquals("rejected", approvalItems(store)[0].status)
    }

    // ---------- 场景 12：多个 turn_id 正确分轮 ----------

    @Test
    fun `12 multiple turn ids keep turns separated`() {
        val store = TimelineStore()
        ingestOne(store, convEvent("id-u1", "user_message", turnId = "t1", seq = 1, payload = JSONObject().put("content", "问题一")))
        ingestOne(store, convEvent("id-a1", "assistant_message", turnId = "t1", seq = 2, payload = JSONObject().put("text", "回答一")))
        ingestOne(store, convEvent("id-u2", "user_message", turnId = "t2", seq = 3, payload = JSONObject().put("content", "问题二")))
        ingestOne(store, convEvent("id-a2", "assistant_message", turnId = "t2", seq = 4, payload = JSONObject().put("text", "回答二")))

        val users = userItems(store)
        val answers = assistantItems(store)
        assertEquals(2, users.size)
        assertEquals(2, answers.size)
        assertEquals(setOf("t1", "t2"), users.map { it.turnId }.toSet())
        assertEquals(setOf("t1", "t2"), answers.map { it.turnId }.toSet())
        // 排序保持时间线顺序
        assertEquals("问题一", users[0].content.optString("text"))
        assertEquals("问题二", users[1].content.optString("text"))
        assertEquals("回答一", answers[0].content.optString("text"))
        assertEquals("回答二", answers[1].content.optString("text"))
    }

    // ---------- 场景 13：缺少 turn_id 时的兼容分组 ----------

    @Test
    fun `13 missing turn id falls back to job id grouping`() {
        val store = TimelineStore()
        // job 事件无 turn_id，用 fallbackTurnId 兜底
        val raw = JSONObject().put("id", 7L).put("type", "text_delta").put("ts", 1.5).put("task_id", "j1").put("delta", "无轮次流")
        val ev = ConversationEventNormalizer.fromTaskEvent(raw, fallbackJobId = "j1", fallbackTurnId = "t-fallback")!!
        assertEquals("t-fallback", ev.turnId)
        ingestOne(store, ev)
        assertEquals(1, assistantItems(store).size)

        // 完全无身份也不崩溃：通过 job↔turn 链接归入该 job 的轮次流
        assertTrue(ingestOne(store, jobEvent(8, "text_delta", "delta" to "裸流")))
        val merged = assistantItems(store)
        assertEquals(1, merged.size)
        assertEquals("无轮次流裸流", merged[0].content.optString("text"))
        assertEquals("t-fallback", merged[0].turnId)
    }

    // ---------- 场景 14：重复事件去重 ----------

    @Test
    fun `14 duplicate events are deduplicated by stable id and seq`() {
        val store = TimelineStore()
        val first = convEvent("id-dup", "user_message", seq = 20, payload = JSONObject().put("content", "重复问题"))
        assertTrue(ingestOne(store, first))
        // 同 stableId / 同 seq 重复流入被拒
        assertFalse(ingestOne(store, first.copyForResend()))
        assertEquals(1, userItems(store).size)
        assertTrue(store.hasConversationSeq(20))
        assertFalse(store.hasConversationSeq(99))
    }

    private fun NormalizedEvent.copyForResend(): NormalizedEvent =
        NormalizedEvent(
            stableId = stableId,
            kind = kind,
            rawType = rawType,
            turnId = turnId,
            jobId = jobId,
            seq = seq,
            taskEventId = taskEventId,
            timestampMs = timestampMs,
            role = role,
            payload = payload,
            authoritative = authoritative,
        )

    // ---------- 场景 15：历史事件和实时事件合并 ----------

    @Test
    fun `15 history conversation event and live job event merge`() {
        val store = TimelineStore()
        // 历史持久化 tool_call（authoritative）
        ingestOne(
            store,
            convEvent(
                "id-hist", "tool_call", turnId = "t1", seq = 30, taskId = "j1",
                payload = JSONObject().put("tool_call_id", "call-x").put("name", "search_code"),
            ),
        )
        // 实时 job 扁平 tool_result 补充输出
        ingestOne(store, jobEvent(50, "tool_result", "turn_id" to "t1", "tool_call_id" to "call-x", "output" to "命中 3 处", "ok" to true))

        val tools = toolItems(store)
        assertEquals(1, tools.size)
        assertEquals("search_code", tools[0].content.optString("name"))
        assertEquals("命中 3 处", tools[0].content.optString("output"))
        assertEquals("success", tools[0].status)
    }

    // ---------- 场景 16：未知事件安全降级 ----------

    @Test
    fun `16 unknown event degrades to status and private events are hidden`() {
        val store = TimelineStore()
        // 未知 conversation 事件 → STATUS，不崩溃
        val unknown = convEvent("id-unk", "totally_new_event", seq = 40, payload = JSONObject().put("message", "新事件说明"))
        assertEquals(Kind.STATUS, unknown.kind)

        // 私有推理事件绝不进入 UI
        val reasoning = jobEvent(60, "reasoning_delta", "delta" to "思考过程")
        assertEquals(Kind.PRIVATE, reasoning.kind)
        assertFalse(ingestOne(store, reasoning))
        assertTrue(ConversationEventNormalizer.isPrivate("reasoning"))
        assertTrue(ConversationEventNormalizer.isPrivate("thinking"))
        assertFalse(ConversationEventNormalizer.isPrivate("tool_call"))

        // 空事件被拒
        assertNull(ConversationEventNormalizer.fromTaskEvent(JSONObject().put("type", "")))
        assertNull(ConversationEventNormalizer.fromConversationEvent(JSONObject()))
    }

    // ---------- 场景 17：超长工具输出截断 ----------

    @Test
    fun `17 long tool output is truncated for display but kept in store`() {
        val longOutput = "行数据\n".repeat(600)
        val store = TimelineStore()
        ingestOne(
            store,
            convEvent(
                "id-long", "tool_result", seq = 50,
                payload = JSONObject().put("tool_call_id", "call-long").put("output", longOutput).put("ok", true),
            ),
        )
        // 数据层保留完整输出
        assertEquals(longOutput, toolItems(store)[0].content.optString("output"))

        // 展示层截断
        val display = ConversationTimelineAdapter.displayToolOutput(longOutput)
        assertTrue(display.length < longOutput.length)
        assertTrue(display.endsWith("…（输出已截断，复制可获取完整内容）"))
        // 短输出原样展示
        assertEquals("短输出", ConversationTimelineAdapter.displayToolOutput("短输出"))
    }

    // ---------- 场景 18：Conversation 切换不串数据 ----------

    @Test
    fun `18 switching conversation does not leak state across stores`() {
        val storeA = TimelineStore()
        val storeB = TimelineStore()
        ingestOne(storeA, convEvent("id-a-u", "user_message", seq = 61, payload = JSONObject().put("content", "A 会话问题")))
        ingestOne(storeB, convEvent("id-b-u", "user_message", seq = 62, payload = JSONObject().put("content", "B 会话问题")))

        assertEquals(1, userItems(storeA).size)
        assertEquals("A 会话问题", userItems(storeA)[0].content.optString("text"))
        assertEquals(1, userItems(storeB).size)
        assertEquals("B 会话问题", userItems(storeB)[0].content.optString("text"))

        // seq 隔离：A 的 seq 不影响 B
        assertTrue(storeA.hasConversationSeq(61))
        assertFalse(storeB.hasConversationSeq(61))
        assertNotEquals(storeA.conversationSeqMax, storeB.conversationSeqMax)

        // 本地乐观消息生命周期
        val localKey = storeB.addLocalUserMessage("待确认消息", "jB")
        assertNotNull(storeB.removeItem(localKey))
        assertFalse(storeB.removeItem(localKey))
    }

    // ---------- 场景 19：审批对账合成事件不共用 stableId ----------

    @Test
    fun `19 synthetic approval sync events with distinct approval ids both ingest`() {
        val store = TimelineStore()
        // 模拟 refreshApprovals 构造的合成事件：无 id、无 seq，仅 payload 带 approval_id
        fun synthetic(approvalId: String) = ConversationEventNormalizer.fromConversationEvent(
            JSONObject()
                .put("event_type", "approval_required")
                .put("task_id", "j1")
                .put(
                    "payload",
                    JSONObject().put("approval_id", approvalId)
                        .put("kind", "run_command")
                        .put("summary", "ls"),
                ),
        )!!

        assertTrue(store.ingest(listOf(synthetic("ap-a"))))
        assertTrue(store.ingest(listOf(synthetic("ap-b"))))
        assertEquals(2, approvalItems(store).size)
        assertEquals(2, store.pendingApprovals().size)

        // 同一审批重复同步被去重，不重置状态
        store.setApprovalDecision("ap-a", "approved")
        assertFalse(store.ingest(listOf(synthetic("ap-a"))))
        assertEquals("approved", approvalItems(store)[0].status)
        assertEquals(1, store.pendingApprovals().size)
    }

    // ---------- 场景 20：历史回放不得回翻已决议审批 ----------

    @Test
    fun `20 approval required replay after optimistic decision keeps resolved status`() {
        val store = TimelineStore()
        ingestOne(
            store,
            convEvent(
                "id-ar2", "approval_required", seq = 21,
                payload = JSONObject().put("approval_id", "ap-2").put("kind", "download"),
            ),
        )
        assertEquals("pending", approvalItems(store)[0].status)

        // 本地乐观决议
        store.setApprovalDecision("ap-2", "rejected")
        assertEquals("rejected", approvalItems(store)[0].status)

        // 带全新事件 id 的权威历史回放（晚到）不得回退为 pending
        ingestOne(
            store,
            convEvent(
                "id-ar3", "approval_required", seq = 22,
                payload = JSONObject().put("approval_id", "ap-2").put("kind", "download"),
            ),
        )
        assertEquals("rejected", approvalItems(store)[0].status)
        assertEquals(0, store.pendingApprovals().size)
    }

    // ---------- 场景 21：任务终态清理残留等待审批 ----------

    @Test
    fun `21 expire pending approvals clears stuck approval cards`() {
        val store = TimelineStore()
        ingestOne(
            store,
            convEvent(
                "id-ar4", "approval_required", seq = 31,
                payload = JSONObject().put("approval_id", "ap-3").put("kind", "run_command"),
            ),
        )
        assertEquals(1, store.pendingApprovals().size)

        assertTrue(store.expirePendingApprovals())
        assertEquals(0, store.pendingApprovals().size)
        assertEquals("canceled", approvalItems(store)[0].status)
        assertEquals("canceled", approvalItems(store)[0].content.optString("decision"))

        // 幂等：无残留时返回 false
        assertFalse(store.expirePendingApprovals())
    }
}
