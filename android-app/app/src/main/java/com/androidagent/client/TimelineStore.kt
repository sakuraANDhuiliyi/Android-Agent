package com.androidagent.client

import com.androidagent.client.ConversationEventNormalizer.Kind
import com.androidagent.client.ConversationEventNormalizer.NormalizedEvent
import org.json.JSONArray
import org.json.JSONObject

/**
 * 会话时间线状态存储：Conversation 历史事件与 Job 实时事件流入同一条管道，
 * 按业务身份（tool_call_id / approval_id / message_id / turn_id）合并，
 * 持久化事件 authoritative。语义对齐桌面端 timeline.js。
 */
class TimelineStore {

    /** 渲染层条目种类。 */
    enum class ItemType { USER, ASSISTANT, STATUS, TOOL, APPROVAL, PLAN, CHANGES, CHECKPOINT, USAGE, LIFECYCLE, ERROR }

    class TimelineItem(
        val key: String,
        val type: ItemType,
        var pos: Int,
        var turnId: String? = null,
        var jobId: String? = null,
        var seq: Long? = null,
        var timestampMs: Long? = null,
        var status: String = "done",
        var messageId: String? = null,
        var toolCallId: String? = null,
        var approvalId: String? = null,
        /** 业务内容：name/input/output/decision/files/messages 等。 */
        var content: JSONObject = JSONObject(),
        var streaming: Boolean = false,
        var isFinal: Boolean = false,
        /** 每次内容变化 +1，供 DiffUtil 判断是否需要刷新。 */
        var version: Int = 1,
    ) {
        fun bump() {
            version += 1
        }
    }

    private val items = HashMap<String, TimelineItem>()
    private var posCounter = 0

    /** 审批终态决策集合，与服务端 Decision 字面量一致。 */
    private val RESOLVED_DECISIONS = setOf("approved", "rejected", "timeout", "canceled")

    /** 去重：conversation 事件按事件 id 与 seq；job 事件按全局自增 id。 */
    private val seenStableIds = HashSet<String>()
    private val seenSeqs = HashSet<Long>()

    /** turn 首次出现顺序。 */
    private val turnOrder = LinkedHashMap<String, Int>()
    private var nextTurnOrd = 0

    /** task_id → turn_id 映射（live 事件锚定）。 */
    private val turnByJob = HashMap<String, String>()
    private val jobIdsByTurn = HashMap<String, MutableSet<String>>()

    /** 每个 turn 当前打开的流式 item key（未定稿回答缓冲）。 */
    private val openStreams = HashMap<String, MutableList<String>>()

    /** turn 内打开中的 status 组 key。 */
    private var openStatusGroupKey: String? = null

    val conversationSeqMax: Long? get() = items.values.mapNotNull { it.seq }.maxOrNull()

    /** 批量流入，返回是否产生可见变化。 */
    fun ingest(events: List<NormalizedEvent>): Boolean {
        var changed = false
        for (ev in events) {
            if (ingestOne(ev)) changed = true
        }
        return changed
    }

    private fun ingestOne(ev: NormalizedEvent): Boolean {
        if (!seenStableIds.add(ev.stableId)) return false
        if (ev.seq != null && !seenSeqs.add(ev.seq)) return false
        if (ev.kind == Kind.PRIVATE) return false
        return try {
            dispatch(ev)
        } catch (_: Exception) {
            // 单个事件解析失败永不拖垮整条时间线
            false
        }
    }

    private fun dispatch(ev: NormalizedEvent): Boolean = when (ev.kind) {
        Kind.USER_MESSAGE -> handleUserMessage(ev)
        Kind.ASSISTANT_MESSAGE -> handleAssistantMessage(ev)
        Kind.TEXT_DELTA -> handleTextDelta(ev)
        Kind.TEXT -> handleTextSnapshot(ev)
        Kind.TOOL_CALL, Kind.TOOL_RESULT -> handleTool(ev)
        Kind.APPROVAL_REQUIRED -> handleApprovalRequired(ev)
        Kind.APPROVAL_RESOLVED -> handleApprovalResolved(ev)
        Kind.PLAN -> handlePlan(ev)
        Kind.CHANGES -> handleChanges(ev)
        Kind.CHECKPOINT -> handleCheckpoint(ev)
        Kind.USAGE, Kind.PROVIDER_SWITCH, Kind.MODEL_SWITCH -> handleUsage(ev)
        Kind.ERROR -> handleError(ev)
        Kind.RECOVERY_NOTE -> handleStatusLine(ev)
        Kind.TURN_STARTED -> handleLifecycle(ev, "turn_started")
        Kind.TURN_COMPLETED -> handleLifecycle(ev, "succeeded")
        Kind.TURN_FAILED -> handleLifecycle(ev, "failed")
        Kind.TURN_CANCELED -> handleLifecycle(ev, "canceled")
        Kind.TURN_INTERRUPTED -> handleLifecycle(ev, "interrupted")
        Kind.LIFECYCLE_RECONCILED -> handleLifecycle(ev, ev.payload.optString("status", "succeeded"))
        Kind.STATUS -> handleStatusLine(ev)
        Kind.PRIVATE -> false
    }

    // ---------- 身份与锚定 ----------

    private fun learnJobTurnLink(jobId: String?, turnId: String?) {
        if (jobId.isNullOrBlank() || turnId.isNullOrBlank()) return
        if (turnByJob[jobId] == turnId) return
        turnByJob[jobId] = turnId
        jobIdsByTurn.getOrPut(turnId) { HashSet() }.add(jobId)
    }

    /** live-only 事件（无 turn_id）在 canonical 事件到达后回填 turn 归属。 */
    private fun reanchor() {
        for (item in items.values) {
            if (item.turnId != null || item.jobId == null) continue
            val turnId = turnByJob[item.jobId] ?: continue
            item.turnId = turnId
            registerTurnOrder(turnId, item.pos)
        }
    }

    private fun registerTurnOrder(turnId: String, pos: Int) {
        val turnKey = "t:$turnId"
        if (turnOrder.containsKey(turnKey)) return
        turnOrder[turnKey] = nextTurnOrd++
        // 后续同 turn 事件的 pos 已可能更小；ordinal 只影响分组排序，不影响组内顺序
        if (turnOrder.size == 1) return
    }

    private fun turnOrdOf(item: TimelineItem): Int {
        val turnId = item.turnId
        if (turnId != null) {
            turnOrder["t:$turnId"]?.let { return it }
        }
        val jobId = item.jobId
        if (jobId != null) {
            turnByJob[jobId]?.let { tid -> turnOrder["t:$tid"]?.let { ord -> return ord } }
            turnOrder["j:$jobId"]?.let { return it }
        }
        return turnOrder["_orphan"] ?: Int.MAX_VALUE
    }

    private fun ensureTurnOrder(item: TimelineItem) {
        when {
            item.turnId != null -> {
                if (!turnOrder.containsKey("t:${item.turnId}")) turnOrder["t:${item.turnId}"] = nextTurnOrd++
            }
            item.jobId != null -> {
                val tid = turnByJob[item.jobId]
                if (tid != null && !turnOrder.containsKey("t:$tid")) turnOrder["t:$tid"] = nextTurnOrd++
                if (!turnOrder.containsKey("j:${item.jobId}")) turnOrder["j:${item.jobId}"] = nextTurnOrd++
            }
            else -> if (!turnOrder.containsKey("_orphan")) turnOrder["_orphan"] = nextTurnOrd++
        }
    }

    private fun item(key: String): TimelineItem? = items[key]

    private fun upsert(
        key: String,
        type: ItemType,
        ev: NormalizedEvent,
        configure: (TimelineItem) -> Unit,
    ): TimelineItem {
        learnJobTurnLink(ev.jobId, ev.turnId)
        val existing = items[key]
        if (existing != null) {
            if (ev.seq != null && existing.seq == null) existing.seq = ev.seq
            existing.jobId = existing.jobId ?: ev.jobId
            if (ev.turnId != null) existing.turnId = ev.turnId
            configure(existing)
            existing.bump()
            return existing
        }
        val created = TimelineItem(
            key = key,
            type = type,
            pos = posCounter++,
            turnId = ev.turnId,
            jobId = ev.jobId,
            seq = ev.seq,
            timestampMs = ev.timestampMs,
        )
        configure(created)
        items[key] = created
        ensureTurnOrder(created)
        closeStatusGroupIfInterleaved(created)
        return created
    }

    /** 连续 status 折叠成一组；任何非 status 新条目关闭当前组。 */
    private fun closeStatusGroupIfInterleaved(newItem: TimelineItem) {
        if (newItem.type != ItemType.STATUS) openStatusGroupKey = null
    }

    // ---------- 处理器 ----------

    private fun handleUserMessage(ev: NormalizedEvent): Boolean {
        val text = ConversationEventNormalizer.userText(ev.payload)
        if (text.isBlank()) return false
        val messageId = ev.payload.optString("message_id").takeIf { it.isNotBlank() }
        // 收养本地乐观 echo（同文本、无 turn 归属）
        val echo = items.values.firstOrNull {
            it.type == ItemType.USER && it.turnId == null && it.content.optString("text") == text
        }
        if (echo != null) {
            items.remove(echo.key)
            echo.turnId = ev.turnId
            echo.jobId = echo.jobId ?: ev.jobId
            echo.seq = ev.seq
            echo.messageId = messageId
            echo.timestampMs = echo.timestampMs ?: ev.timestampMs
            items[keyOfUser(ev, text)] = echo
            ensureTurnOrder(echo)
            echo.bump()
            return true
        }
        val dedupeKey = keyOfUser(ev, text)
        if (items.containsKey(dedupeKey)) return false
        val item = upsert(dedupeKey, ItemType.USER, ev) {
            it.content = JSONObject().put("text", text)
            it.messageId = messageId
        }
        return item != null
    }

    private fun keyOfUser(ev: NormalizedEvent, text: String): String {
        val owner = ev.turnId ?: ev.jobId ?: "?"
        return "user:$owner:${text.length}:${text.take(32)}"
    }

    /** 本地乐观用户消息：服务端 canonical user_message 到达后被收养。 */
    fun addLocalUserMessage(text: String, jobId: String?): String {
        val key = "local:${System.nanoTime()}"
        val item = TimelineItem(
            key = key,
            type = ItemType.USER,
            pos = posCounter++,
            jobId = jobId,
            content = JSONObject().put("text", text),
        )
        items[key] = item
        ensureTurnOrder(item)
        return key
    }

    /** 发送失败时移除乐观 echo。 */
    fun removeItem(key: String): Boolean = items.remove(key) != null

    private fun handleAssistantMessage(ev: NormalizedEvent): Boolean {
        val messageId = ev.payload.optString("message_id").takeIf { it.isNotBlank() }
        val text = ConversationEventNormalizer.assistantText(ev.payload)
        if (messageId == null && text.isBlank()) return false
        val isFinal = ConversationEventNormalizer.isFinalAssistant(ev.payload)

        // 收养同 message_id 的流式缓冲（或 turn 内未定稿流）
        val adoptedKey = messageId?.let { msgId ->
            items.values.firstOrNull { it.key == "stream:$msgId" || (it.messageId == msgId && it.type == ItemType.ASSISTANT) }?.key
        } ?: run {
            val turnKey = ev.turnId ?: ev.jobId?.let { turnByJob[it] } ?: return@run null
            openStreams[turnKey]?.firstOrNull()?.let { streamKey ->
                val stream = items[streamKey]
                if (stream?.messageId == null) streamKey else null
            }
        }
        if (adoptedKey != null && adoptedKey != "msg:$messageId") {
            val adopted = items.remove(adoptedKey) ?: return false
            val newKey = "msg:${messageId ?: adoptedKey.removePrefix("stream:")}"
            adopted.key.let { /* key is val in Kotlin: create new item preserving pos */ }
            val finalItem = TimelineItem(
                key = newKey,
                type = ItemType.ASSISTANT,
                pos = adopted.pos,
                turnId = ev.turnId ?: adopted.turnId,
                jobId = adopted.jobId ?: ev.jobId,
                seq = ev.seq ?: adopted.seq,
                timestampMs = ev.timestampMs ?: adopted.timestampMs,
                status = "done",
                messageId = messageId ?: adopted.messageId,
                content = JSONObject().put("text", text.ifBlank { adopted.content.optString("text") }),
                streaming = false,
                isFinal = isFinal,
                version = adopted.version + 1,
            )
            items[newKey] = finalItem
            if (finalItem.turnId != null) {
                openStreams[finalItem.turnId]?.remove(adoptedKey)
            }
            return true
        }

        val key = "msg:${messageId ?: "noid:" + (ev.seq ?: ev.timestampMs ?: posCounter)}"
        if (items.containsKey(key)) return false
        upsert(key, ItemType.ASSISTANT, ev) {
            it.content = JSONObject().put("text", text)
            it.status = "done"
            it.streaming = false
            it.isFinal = isFinal
            it.messageId = messageId
        }
        return true
    }

    /** text_delta：只追加到当前 turn 的流式缓冲，绝不新建状态行。 */
    private fun handleTextDelta(ev: NormalizedEvent): Boolean {
        val delta = ConversationEventNormalizer.deltaText(ev.payload)
        if (delta.isEmpty()) return false
        val messageId = ev.payload.optString("message_id").takeIf { it.isNotBlank() }
            ?: ev.payload.optString("stream_id").takeIf { it.isNotBlank() }
        val turnId = ev.turnId ?: ev.jobId?.let { turnByJob[it] }
        val key = "stream:${messageId ?: "turn:" + (turnId ?: "_")}"
        val existing = items[key]
        if (existing != null) {
            existing.content = JSONObject().put("text", existing.content.optString("text") + delta)
            existing.jobId = existing.jobId ?: ev.jobId
            existing.turnId = existing.turnId ?: turnId
            existing.streaming = true
            existing.status = "streaming"
            existing.messageId = existing.messageId ?: messageId
            existing.bump()
        } else {
            val created = upsert(key, ItemType.ASSISTANT, ev) {
                it.content = JSONObject().put("text", delta)
                it.streaming = true
                it.status = "streaming"
                it.messageId = messageId
            }
            created.turnId = turnId ?: created.turnId
            ensureTurnOrder(created)
        }
        registerOpenStream(turnId, key)
        return true
    }

    private fun registerOpenStream(turnId: String?, key: String) {
        if (turnId == null) return
        val list = openStreams.getOrPut(turnId) { mutableListOf() }
        if (!list.contains(key)) list.add(key)
    }

    /** text 快照：有身份直接替换；无身份做前缀协调（陈旧前缀忽略 / 增量追加 / 替换）。 */
    private fun handleTextSnapshot(ev: NormalizedEvent): Boolean {
        val snapshot = ConversationEventNormalizer.assistantText(ev.payload).ifBlank {
            ev.payload.optString("content").ifBlank { ev.payload.optString("message") }
        }
        if (snapshot.isBlank()) return false
        val messageId = ev.payload.optString("message_id").takeIf { it.isNotBlank() }
        val turnId = ev.turnId ?: ev.jobId?.let { turnByJob[it] }
        val target = messageId?.let { items["stream:$it"] }
            ?: turnId?.let { tid -> openStreams[tid]?.lastOrNull()?.let { items[it] } }
        if (target == null) {
            val key = "stream:${messageId ?: "turn:" + (turnId ?: "_")}"
            upsert(key, ItemType.ASSISTANT, ev) {
                it.content = JSONObject().put("text", snapshot)
                it.streaming = true
                it.status = "streaming"
                it.messageId = messageId
            }
            registerOpenStream(turnId, key)
            return true
        }
        val existing = target.content.optString("text")
        when {
            existing.startsWith(snapshot) -> Unit // 陈旧前缀，忽略
            snapshot.startsWith(existing) -> {
                target.content = JSONObject().put("text", snapshot)
                target.bump()
            }
            else -> {
                target.content = JSONObject().put("text", existing + snapshot)
                target.bump()
            }
        }
        return true
    }

    private fun handleTool(ev: NormalizedEvent): Boolean {
        val p = ev.payload
        val callId = p.optString("tool_call_id").takeIf { it.isNotBlank() }
        val name = p.optString("name").takeIf { it.isNotBlank() } ?: p.optString("tool")
        val key = if (callId != null) {
            "tool:$callId"
        } else {
            "tool:${ev.turnId ?: ev.jobId ?: "_"}:$name:${ev.seq ?: ev.taskEventId ?: posCounter}"
        }
        if (ev.kind == Kind.TOOL_CALL) {
            val inputObj = p.optJSONObject("input") ?: p.optJSONObject("arguments")
            upsert(key, ItemType.TOOL, ev) {
                it.toolCallId = callId ?: it.toolCallId
                it.content.put("name", name)
                if (inputObj != null) it.content.put("input", inputObj)
                it.status = when {
                    it.status == "waiting_approval" -> "waiting_approval"
                    it.status == "success" || it.status == "failed" -> it.status
                    else -> "running"
                }
                if (it.timestampMs == null) it.timestampMs = ev.timestampMs
            }
        } else {
            upsert(key, ItemType.TOOL, ev) {
                it.toolCallId = callId ?: it.toolCallId
                if (name.isNotBlank()) it.content.put("name", name)
                val output = toolOutput(p)
                if (output != null) it.content.put("output", output)
                val ok = if (p.has("ok") && !p.isNull("ok")) p.optBoolean("ok") else null
                when (ok) {
                    true -> it.status = "success"
                    false -> it.status = "failed"
                    null -> it.status = "done"
                }
                p.optString("error_type").takeIf { t -> t.isNotBlank() && t != "null" }?.let { t ->
                    it.content.put("error_type", t)
                }
                if (p.has("duration_ms") && !p.isNull("duration_ms")) it.content.put("duration_ms", p.optLong("duration_ms"))
                if (p.optBoolean("interrupted")) it.content.put("interrupted", true)
                if (p.has("input") && !it.content.has("input")) it.content.put("input", p.optJSONObject("input"))
            }
        }
        return true
    }

    /** 输出优先级：structured_output > model_output > output > message。 */
    private fun toolOutput(p: JSONObject): String? {
        if (p.has("structured_output") && !p.isNull("structured_output")) {
            val v = p.opt("structured_output")
            return if (v is JSONObject || v is JSONArray) v.toString() else v?.toString()
        }
        for (field in listOf("model_output", "output", "message")) {
            if (p.has(field) && !p.isNull(field)) {
                val s = p.optString(field)
                if (s.isNotBlank()) return s
            }
        }
        return null
    }

    private fun handleApprovalRequired(ev: NormalizedEvent): Boolean {
        val approvalId = ev.payload.optString("approval_id")
        if (approvalId.isBlank()) return handleStatusLine(ev)
        val key = "approval:$approvalId"
        upsert(key, ItemType.APPROVAL, ev) {
            it.approvalId = approvalId
            val merged = JSONObject()
            ev.payload.optJSONObject("request")?.let { req ->
                for (k in req.keys()) merged.put(k, req.get(k))
            }
            for (k in ev.payload.keys()) {
                if (k == "request") continue
                if (ev.payload.isNull(k)) continue
                if (!merged.has(k) || !ev.authoritative) merged.put(k, ev.payload.get(k))
            }
            it.content = merged
            // 历史回放可能晚于本地乐观决议：已决议的审批不得回退为 pending
            it.status = if (it.status in RESOLVED_DECISIONS) it.status else "pending"
            it.toolCallId = ev.payload.optString("tool_call_id").takeIf { c -> c.isNotBlank() }
        }
        // 审批与工具双向联动
        items[key]?.toolCallId?.let { callId ->
            items["tool:$callId"]?.let { tool ->
                if (tool.status == "running") tool.status = "waiting_approval"
                tool.content.put("approval_id", approvalId)
                tool.bump()
            }
        }
        return true
    }

    private fun handleApprovalResolved(ev: NormalizedEvent): Boolean {
        val approvalId = ev.payload.optString("approval_id")
        if (approvalId.isBlank()) return false
        val decisionRaw = ev.payload.optString("decision", "rejected")
        val decision = if (decisionRaw in RESOLVED_DECISIONS) decisionRaw else "rejected"
        val item = items["approval:$approvalId"] ?: return true
        item.status = decision
        item.content.put("decision", decision)
        item.content.put("resolved_at_ms", ev.timestampMs ?: System.currentTimeMillis())
        item.bump()
        item.toolCallId?.let { callId ->
            items["tool:$callId"]?.let { tool ->
                tool.status = when (decision) {
                    "approved" -> if (tool.status != "success" && tool.status != "failed") "running" else tool.status
                    else -> decision
                }
                tool.bump()
            }
        }
        return true
    }

    /** 本地乐观决议：服务端事件仍是最终裁决。 */
    fun setApprovalDecision(approvalId: String, decision: String) {
        handleApprovalResolved(
            NormalizedEvent(
                stableId = "local-approval:$approvalId:$decision",
                kind = Kind.APPROVAL_RESOLVED,
                rawType = "approval_resolved",
                turnId = null,
                jobId = null,
                seq = null,
                taskEventId = null,
                timestampMs = System.currentTimeMillis(),
                role = null,
                payload = JSONObject().put("approval_id", approvalId).put("decision", decision),
                authoritative = false,
            ),
        )
    }

    /** 任务终态时清理仍在等待的审批卡（服务端可能未回放 resolved 事件）。返回是否有变更。 */
    fun expirePendingApprovals(decision: String = "canceled"): Boolean {
        var changed = false
        for (item in items.values) {
            if (item.type == ItemType.APPROVAL && item.status == "pending") {
                item.status = decision
                item.content.put("decision", decision)
                item.content.put("resolved_at_ms", System.currentTimeMillis())
                item.bump()
                changed = true
            }
        }
        return changed
    }

    fun pendingApprovals(): List<TimelineItem> =
        sortedItems().filter { it.type == ItemType.APPROVAL && it.status == "pending" }

    private fun handlePlan(ev: NormalizedEvent): Boolean {
        val owner = ev.turnId ?: ev.jobId ?: "_"
        val key = "plan:$owner"
        upsert(key, ItemType.PLAN, ev) {
            it.content.put("text", ev.payload.optString("message"))
            it.status = "done"
        }
        return true
    }

    private fun handleChanges(ev: NormalizedEvent): Boolean {
        val owner = ev.turnId ?: ev.jobId ?: "_"
        val canonicalKey = "changes:${ev.turnId ?: owner}"
        val files = filesOf(ev.payload)
        // live（job key）与 canonical（turn key）合并为一张卡
        val existing = items[canonicalKey]
        if (existing != null) {
            val merged = JSONObject()
            val arr = JSONArray()
            val set = LinkedHashSet<String>()
            val oldFiles = existing.content.optJSONArray("files") ?: JSONArray()
            for (i in 0 until oldFiles.length()) {
                set.add(oldFiles.optString(i))
            }
            files.forEach { set.add(it) }
            set.forEach { arr.put(it) }
            merged.put("files", arr)
            existing.content = merged
            existing.seq = existing.seq ?: ev.seq
            existing.bump()
            return true
        }
        val liveKey = "changes:$owner"
        items.remove(liveKey)?.let { live ->
            val set = LinkedHashSet<String>()
            val liveFiles = live.content.optJSONArray("files") ?: JSONArray()
            for (i in 0 until liveFiles.length()) {
                set.add(liveFiles.optString(i))
            }
            files.forEach { set.add(it) }
            val merged = TimelineItem(
                key = canonicalKey,
                type = ItemType.CHANGES,
                pos = live.pos,
                turnId = ev.turnId ?: live.turnId,
                jobId = live.jobId ?: ev.jobId,
                seq = ev.seq ?: live.seq,
                timestampMs = live.timestampMs ?: ev.timestampMs,
                content = JSONObject().put("files", JSONArray(set)),
                version = live.version + 1,
            )
            items[canonicalKey] = merged
            return true
        }
        upsert(canonicalKey, ItemType.CHANGES, ev) {
            it.content = JSONObject().put("files", JSONArray(files))
        }
        return true
    }

    private fun filesOf(p: JSONObject): List<String> {
        val arr = p.optJSONArray("files") ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i -> arr.optString(i).takeIf { it.isNotBlank() } }
    }

    private fun handleCheckpoint(ev: NormalizedEvent): Boolean {
        val cpId = ev.payload.optString("checkpoint_id").takeIf { it.isNotBlank() }
            ?: ev.payload.optString("kind").takeIf { it.isNotBlank() }
        val key = "checkpoint:${cpId ?: ev.seq ?: ev.taskEventId ?: posCounter}"
        upsert(key, ItemType.CHECKPOINT, ev) {
            it.content.put("kind", ev.payload.optString("kind"))
            if (ev.payload.has("file_count")) it.content.put("file_count", ev.payload.optInt("file_count"))
        }
        return true
    }

    private fun handleUsage(ev: NormalizedEvent): Boolean {
        val owner = ev.turnId ?: ev.jobId ?: "_"
        val key = "usage:$owner:${ev.kind.name.lowercase()}"
        upsert(key, ItemType.USAGE, ev) {
            val usage = ev.payload.optJSONObject("usage")
            if (usage != null) it.content.put("usage", usage)
            it.content.put("message", ev.payload.optString("message"))
            it.content.put("kind", ev.kind.name.lowercase())
        }
        return true
    }

    private fun handleError(ev: NormalizedEvent): Boolean {
        val message = ev.payload.optString("message").ifBlank { ev.payload.optString("error") }
        val owner = ev.turnId ?: ev.jobId ?: "_"
        val key = "error:$owner:${message.take(48)}"
        upsert(key, ItemType.ERROR, ev) {
            it.content.put("message", message)
            it.status = "failed"
        }
        return true
    }

    private fun handleStatusLine(ev: NormalizedEvent): Boolean {
        val message = ev.payload.optString("message").ifBlank {
            when (ev.kind) {
                Kind.RECOVERY_NOTE -> ev.payload.optString("content")
                else -> ev.payload.optString("message")
            }
        }
        if (message.isBlank() && ev.kind != Kind.RECOVERY_NOTE) return false
        val turnId = ev.turnId ?: ev.jobId?.let { turnByJob[it] }
        // 连续 status 折叠：同 turn 上一条也是 status 且组仍打开 → 合并
        val groupKey = openStatusGroupKey
        if (groupKey != null) {
            val group = items[groupKey]
            if (group != null && (group.turnId ?: group.jobId?.let { j -> turnByJob[j] }) == turnId && turnId != null) {
                val messages = group.content.optJSONArray("messages") ?: JSONArray()
                if (messages.length() == 0 || messages.optString(messages.length() - 1) != message) {
                    messages.put(message)
                    group.content.put("messages", messages)
                    group.content.put("last", message)
                    group.seq = group.seq ?: ev.seq
                    group.bump()
                    return true
                }
                return false
            }
        }
        val key = "status:${turnId ?: "_"}:$posCounter"
        val created = upsert(key, ItemType.STATUS, ev) {
            it.content = JSONObject().put("messages", JSONArray().put(message)).put("last", message)
        }
        created.turnId = turnId ?: created.turnId
        openStatusGroupKey = key
        ensureTurnOrder(created)
        return true
    }

    private fun handleLifecycle(ev: NormalizedEvent, status: String): Boolean {
        val owner = ev.turnId ?: ev.jobId?.let { turnByJob[it] } ?: "_"
        val key = "lifecycle:$owner:$status"
        upsert(key, ItemType.LIFECYCLE, ev) {
            it.status = status
            it.content.put("status", status)
            if (ev.payload.has("result") && !ev.payload.isNull("result")) {
                it.content.put("result", ev.payload.optString("result"))
            }
            if (ev.payload.has("error") && !ev.payload.isNull("error")) {
                it.content.put("error", ev.payload.optString("error"))
            }
            if (ev.payload.has("diff_status")) it.content.put("diff_status", ev.payload.optString("diff_status"))
            if (ev.payload.has("diff_reason")) it.content.put("diff_reason", ev.payload.optString("diff_reason"))
            if (ev.payload.has("after_checkpoint_id")) it.content.put("after_checkpoint_id", ev.payload.optString("after_checkpoint_id"))
            if (ev.payload.has("provider")) it.content.put("provider", ev.payload.optString("provider"))
            if (ev.payload.has("model")) it.content.put("model", ev.payload.optString("model"))
        }
        if (status in TERMINAL_LIFECYCLE) {
            finalizeTurnStreams(owner, status)
            openStatusGroupKey = null
        }
        return true
    }

    /** 终态清场：关闭流缓冲、取消运行中工具（canceled 时）。 */
    private fun finalizeTurnStreams(owner: String, status: String) {
        val turnId = if (owner.startsWith("t:")) owner.removePrefix("t:") else owner
        openStreams.remove(turnId)?.forEach { streamKey ->
            val stream = items[streamKey] ?: return@forEach
            if (stream.content.optString("text").isBlank()) {
                items.remove(streamKey)
            } else {
                stream.streaming = false
                stream.status = "done"
                stream.bump()
            }
        }
        if (status == "canceled" || status == "failed" || status == "interrupted") {
            items.values.filter {
                it.type == ItemType.TOOL && it.status == "running" && (it.turnId == turnId || it.jobId?.let { j -> turnByJob[j] } == turnId)
            }.forEach {
                it.status = status
                it.bump()
            }
        }
    }

    // ---------- 查询 ----------

    fun sortedItems(): List<TimelineItem> {
        reanchor()
        return items.values.sortedWith(compareBy({ turnOrdOf(it) }, { it.pos }))
    }

    fun hasConversationSeq(seq: Long): Boolean = seq in seenSeqs

    companion object {
        val TERMINAL_LIFECYCLE = setOf("succeeded", "failed", "canceled", "interrupted")
    }
}
