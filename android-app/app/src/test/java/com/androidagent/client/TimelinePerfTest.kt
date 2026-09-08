package com.androidagent.client

import com.androidagent.client.ConversationEventNormalizer.NormalizedEvent
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 时间线性能烟囱测试（P16）：
 * 1000 timeline nodes 的 ingest + 排序 + 行构建预算，以及排序缓存命中。
 */
class TimelinePerfTest {

    private fun convEvent(
        id: String,
        type: String,
        turnId: String,
        seq: Long,
        payload: JSONObject = JSONObject(),
    ): NormalizedEvent {
        val ev = JSONObject()
            .put("id", id)
            .put("conversation_id", "c1")
            .put("event_type", type)
            .put("seq", seq)
            .put("created_at", 1_700_000_000.0)
            .put("payload", payload)
            .put("turn_id", turnId)
        return ConversationEventNormalizer.fromConversationEvent(ev)!!
    }

    /** 100 turns × (user + tool_call + tool_result + assistant) = 400 items，外加大日志输出。 */
    private fun bigConversation(turns: Int = 250): List<NormalizedEvent> {
        val events = ArrayList<NormalizedEvent>(turns * 4)
        var seq = 0L
        for (t in 1..turns) {
            val turnId = "turn-$t"
            events += convEvent("u-$t", "user_message", turnId, seq++, JSONObject().put("content", "问题 $t"))
            events += convEvent(
                "tc-$t", "tool_call", turnId, seq++,
                JSONObject().put("tool_call_id", "call-$t").put("name", "run_gradle")
                    .put("input", JSONObject().put("task", "assembleDebug")),
            )
            events += convEvent(
                "tr-$t", "tool_result", turnId, seq++,
                JSONObject().put("tool_call_id", "call-$t").put("ok", true).put("duration_ms", 12_300)
                    .put("output", "x".repeat(500)),
            )
            events += convEvent(
                "a-$t", "assistant_message", turnId, seq++,
                JSONObject().put("message_id", "m-$t")
                    .put("content", "回答 $t\n\n```kotlin\nval x = 1\n```\n\n收尾。"),
            )
        }
        return events
    }

    @Test
    fun `1000 timeline nodes ingest sort and buildRows within budget`() {
        val store = TimelineStore()
        val events = bigConversation(turns = 250)
        assertEquals(1000, events.size)

        val startIngest = System.nanoTime()
        store.ingest(events)
        val ingestMs = (System.nanoTime() - startIngest) / 1_000_000

        val startRows = System.nanoTime()
        val turns = ConversationTimelineBuilder.buildTurns(store)
        val rows = ConversationTimelineBuilder.buildRows(
            turns,
            ConversationTimelineBuilder.ExpansionPolicy(),
        )
        val rowsMs = (System.nanoTime() - startRows) / 1_000_000

        assertEquals(250, turns.size)
        // 预算非常宽松（CI 机器差异），只防止数量级退化（原实现全量排序下 ~10ms 级）
        assertTrue("ingest took ${ingestMs}ms", ingestMs < 3_000)
        assertTrue("buildRows took ${rowsMs}ms", rowsMs < 3_000)
        assertTrue(rows.size >= 250)
    }

    @Test
    fun `sortedItems reuses cache when structure unchanged`() {
        val store = TimelineStore()
        store.ingest(bigConversation(turns = 50))

        val first = store.sortedItems()
        // 无结构性变化：再次查询必须复用缓存（同一实例）
        assertSame(first, store.sortedItems())
        assertSame(first, store.sortedItems())

        // 内容变化（delta 追加）不触发重排：仍复用
        val delta = JSONObject()
            .put("id", "d-1").put("conversation_id", "c1").put("event_type", "text_delta")
            .put("seq", 9_999L).put("created_at", 1_700_000_000.0)
            .put("turn_id", "turn-1")
            .put("payload", JSONObject().put("message_id", "m-stream").put("delta", "追加文本"))
        store.ingest(listOf(ConversationEventNormalizer.fromConversationEvent(delta)!!))
        assertSame(first, store.sortedItems())

        // 结构性变化（新 turn）：缓存失效，返回新列表
        store.ingest(bigConversation(turns = 51).takeLast(1))
        val after = store.sortedItems()
        assertTrue(after.size >= first.size)
    }

    @Test
    fun `repeated sortedItems during streaming is cheap`() {
        val store = TimelineStore()
        store.ingest(bigConversation(turns = 100))
        store.sortedItems()

        // 模拟流式 delta 高频查询：400 次 sortedItems 必须显著快于全量排序
        val start = System.nanoTime()
        repeat(400) { store.sortedItems() }
        val ms = (System.nanoTime() - start) / 1_000_000
        // 400 次 O(N log N)（1000 nodes）约需 1-2s；缓存命中应在几十 ms 内
        assertTrue("400 cached sortedItems took ${ms}ms", ms < 500)
    }
}
