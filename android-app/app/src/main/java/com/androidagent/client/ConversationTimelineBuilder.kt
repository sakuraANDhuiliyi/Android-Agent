package com.androidagent.client

/**
 * Turn 聚合视图模型层：把 [TimelineStore] 的扁平条目按 turn 分组，
 * 再结合展开策略生成 RecyclerView 行模型。语义对齐桌面端 agent-timeline.js。
 */
object ConversationTimelineBuilder {

    class TurnGroup(
        val key: String,
        val turnId: String?,
        val jobId: String?,
        val userMessage: TimelineStore.TimelineItem?,
        val workItems: List<TimelineStore.TimelineItem>,
        val finalMessages: List<TimelineStore.TimelineItem>,
        val changes: TimelineStore.TimelineItem?,
        val lifecycle: TimelineStore.TimelineItem?,
        val status: String,
        val startedAtMs: Long?,
        val finishedAtMs: Long?,
        val durationMs: Long?,
        val summary: String,
        var isCurrent: Boolean,
    )

    // ---------- 行模型 ----------

    sealed class Row {
        abstract val id: String
        /** 行内容版本（底层 item.version 或聚合哈希），供 DiffUtil payload 判断。 */
        abstract val version: Int

        data class LoadingHistory(
            override val id: String = "loading_history",
            override val version: Int = 1,
            val loading: Boolean = true,
        ) : Row()

        data class User(
            override val id: String,
            override val version: Int,
            val turnKey: String,
            val text: String,
        ) : Row()

        /** 工作过程组：折叠显示摘要，展开渲染 workItems 步骤。 */
        data class WorkGroup(
            override val id: String,
            override val version: Int,
            val turnKey: String,
            val status: String,
            val title: String,
            val summary: String,
            val expanded: Boolean,
            val steps: List<TimelineStore.TimelineItem>,
        ) : Row()

        data class Assistant(
            override val id: String,
            override val version: Int,
            val turnKey: String,
            val text: String,
            val streaming: Boolean,
        ) : Row()

        data class Changes(
            override val id: String,
            override val version: Int,
            val turnKey: String,
            val files: List<String>,
        ) : Row()

        /** Turn 终态结果卡：状态、耗时、构建、APK 与操作入口。 */
        data class Result(
            override val id: String,
            override val version: Int,
            val turnKey: String,
            val status: String,
            val durationMs: Long?,
            val provider: String?,
            val model: String?,
        ) : Row()

        data class Error(
            override val id: String,
            override val version: Int,
            val turnKey: String,
            val message: String,
        ) : Row()
    }

    // ---------- 展开策略 ----------

    /** 用户显式选择优先；否则当前（最后一个活跃）Turn 展开，历史 Turn 收起。 */
    class ExpansionPolicy(
        private val overrides: MutableMap<String, Boolean> = HashMap(),
    ) {
        fun userToggle(turnKey: String, expanded: Boolean) {
            overrides[turnKey] = expanded
        }

        fun isExpanded(turn: TurnGroup, isLastTurn: Boolean): Boolean =
            overrides[turn.key] ?: defaultExpanded(turn, isLastTurn)

        fun defaultExpanded(turn: TurnGroup, isLastTurn: Boolean): Boolean =
            turn.isCurrent || (isLastTurn && turn.status !in TERMINAL_STATUSES)

        fun snapshot(): Map<String, Boolean> = HashMap(overrides)
        fun restore(saved: Map<String, Boolean>) {
            overrides.putAll(saved)
        }
    }

    // ---------- 聚合 ----------

    private val TERMINAL_STATUSES = setOf("succeeded", "failed", "canceled", "interrupted")
    private val ACTIVE_STATUSES = setOf("running", "awaiting_approval", "queued", "paused", "cancel_requested")

    private val READ_TOOLS = setOf("read_file", "list_files", "git_status", "git_diff")
    private val SEARCH_TOOLS = setOf("search_code", "search_files", "web_search")
    private val WRITE_TOOLS = setOf("write_file", "str_replace", "apply_patch", "download_file")
    private val COMMAND_TOOLS = setOf("run_command", "run_gradle")

    /** noise 条目不进入工作过程（usage/checkpoint 等对用户无信息量）。 */
    private fun isNoiseItem(item: TimelineStore.TimelineItem): Boolean =
        item.type == TimelineStore.ItemType.USAGE || item.type == TimelineStore.ItemType.CHECKPOINT

    private fun isStreamingAssistant(item: TimelineStore.TimelineItem): Boolean = item.streaming

    private fun isFinalAssistant(item: TimelineStore.TimelineItem): Boolean =
        item.type == TimelineStore.ItemType.ASSISTANT && !item.streaming || item.isFinal

    fun buildTurns(store: TimelineStore): List<TurnGroup> {
        val items = store.sortedItems()
        data class MutableTurn(
            var turnId: String?,
            var jobId: String?,
            var userMessage: TimelineStore.TimelineItem? = null,
            val workItems: MutableList<TimelineStore.TimelineItem> = ArrayList(),
            val finalMessages: MutableList<TimelineStore.TimelineItem> = ArrayList(),
            var changes: TimelineStore.TimelineItem? = null,
            var lifecycle: TimelineStore.TimelineItem? = null,
            var startedAt: Long? = null,
            var finishedAt: Long? = null,
        )

        val byTurnId = HashMap<String, MutableTurn>()
        val byJobId = HashMap<String, MutableTurn>()
        var orphan: MutableTurn? = null

        val mutableGroups = ArrayList<Pair<MutableTurn, String>>()
        for (item in items) {
            val turn: MutableTurn = when {
                item.turnId != null -> byTurnId[item.turnId]
                    ?: byJobId.takeIf { item.jobId != null }?.get(item.jobId!!)?.also { byTurnId[item.turnId!!] = it }
                    ?: MutableTurn(item.turnId, item.jobId).also {
                        byTurnId[item.turnId!!] = it
                        if (item.jobId != null) byJobId[item.jobId!!] = it
                        mutableGroups.add(it to "t:${item.turnId}")
                    }
                item.jobId != null -> byJobId[item.jobId]
                    ?: MutableTurn(null, item.jobId).also {
                        byJobId[item.jobId!!] = it
                        mutableGroups.add(it to "j:${item.jobId}")
                    }
                else -> orphan ?: MutableTurn(null, null).also {
                    orphan = it
                    mutableGroups.add(it to "_orphan")
                }
            }
            if (item.jobId != null && byJobId[item.jobId] !== turn) byJobId[item.jobId!!] = turn
            if (item.turnId != null && byTurnId[item.turnId] !== turn) byTurnId[item.turnId!!] = turn

            item.timestampMs?.let { ms ->
                if (turn.startedAt == null || ms < turn.startedAt!!) turn.startedAt = ms
                if (turn.finishedAt == null || ms > turn.finishedAt!!) turn.finishedAt = ms
            }
            when {
                item.type == TimelineStore.ItemType.USER -> {
                    if (turn.userMessage == null) turn.userMessage = item else turn.workItems.add(item)
                }
                item.type == TimelineStore.ItemType.LIFECYCLE -> {
                    turn.lifecycle = item
                }
                item.type == TimelineStore.ItemType.CHANGES -> {
                    turn.changes = item
                    turn.workItems.add(item)
                }
                isFinalAssistant(item) -> turn.finalMessages.add(item)
                !isNoiseItem(item) -> turn.workItems.add(item)
            }
        }

        val materialized = mutableGroups.map { (mt, key) ->
            val life = mt.lifecycle?.status
            val status = when {
                life in TERMINAL_STATUSES -> life!!
                mt.workItems.any { it.type == TimelineStore.ItemType.APPROVAL && it.status == "pending" } -> "awaiting_approval"
                mt.workItems.any {
                    (it.type == TimelineStore.ItemType.TOOL && (it.status == "running" || it.status == "waiting_approval")) ||
                        isStreamingAssistant(it)
                } -> "running"
                life == "turn_started" -> "running"
                mt.finalMessages.isNotEmpty() || mt.workItems.isNotEmpty() -> life ?: "succeeded"
                else -> "unknown"
            }
            val duration = if (mt.startedAt != null && mt.finishedAt != null && mt.finishedAt!! >= mt.startedAt!!) {
                mt.finishedAt!! - mt.startedAt!!
            } else null
            val group = TurnGroup(
                key = key,
                turnId = mt.turnId,
                jobId = mt.jobId,
                userMessage = mt.userMessage,
                workItems = mt.workItems,
                finalMessages = mt.finalMessages,
                changes = mt.changes,
                lifecycle = mt.lifecycle,
                status = status,
                startedAtMs = mt.startedAt,
                finishedAtMs = mt.finishedAt,
                durationMs = duration,
                summary = turnSummary(mt.workItems, mt.changes, status),
                isCurrent = false,
            )
            group
        }
        if (materialized.isNotEmpty()) {
            val last = materialized.last()
            last.isCurrent = last.status in ACTIVE_STATUSES
        }
        return materialized
    }

    /** 折叠态摘要：只统计真实可观察操作（对齐桌面 computeTurnSummary）。 */
    private fun turnSummary(
        workItems: List<TimelineStore.TimelineItem>,
        changes: TimelineStore.TimelineItem?,
        status: String,
    ): String {
        var reads = 0
        var searches = 0
        var commands = 0
        var writes = 0
        var approvals = 0
        var pendingApprovals = 0
        var tests = 0
        var gradleFailed = false
        var commandFailed = false
        for (item in workItems) {
            when (item.type) {
                TimelineStore.ItemType.TOOL -> {
                    val name = item.content.optString("name")
                    when {
                        name in READ_TOOLS -> reads++
                        name in SEARCH_TOOLS -> searches++
                        name in WRITE_TOOLS -> writes++
                        name in COMMAND_TOOLS -> {
                            commands++
                            val summary = toolSummary(item)
                            if (Regex("""\btest\b|pytest|connectedAndroidTest|unitTest""", RegexOption.IGNORE_CASE).containsMatchIn(summary)) tests++
                            if (item.status == "failed") {
                                if (name == "run_gradle") gradleFailed = true else commandFailed = true
                            }
                        }
                    }
                }
                TimelineStore.ItemType.APPROVAL -> {
                    approvals++
                    if (item.status == "pending") pendingApprovals++
                }
                else -> Unit
            }
        }
        changes?.content?.optJSONArray("files")?.let { arr ->
            if (arr.length() > 0) writes = arr.length()
        }
        val parts = ArrayList<String>()
        if (gradleFailed) parts.add("构建失败")
        if (reads > 0) parts.add("查看 $reads 个文件")
        if (searches > 0) parts.add("搜索 $searches 次")
        if (commands > 0) parts.add("执行 $commands 条命令")
        if (writes > 0) parts.add("修改 $writes 个文件")
        if (tests > 0) parts.add("运行测试 $tests 次")
        if (approvals > 0) {
            parts.add(if (pendingApprovals > 0) "$pendingApprovals 个审批待处理" else "$approvals 次审批")
        }
        if (parts.isEmpty() && commandFailed) parts.add("命令执行失败")
        if (parts.isEmpty()) {
            when (status) {
                "failed" -> parts.add("任务失败")
                "canceled" -> parts.add("任务已停止")
                "interrupted" -> parts.add("任务被中断")
            }
        }
        return parts.take(4).joinToString(" · ")
    }

    private fun toolSummary(item: TimelineStore.TimelineItem): String {
        val input = item.content.optJSONObject("input") ?: return item.content.optString("name")
        input.optJSONArray("argv")?.let { arr ->
            return (0 until arr.length()).joinToString(" ") { arr.optString(it) }
        }
        for (field in listOf("command", "path", "pattern", "query", "task", "url")) {
            val v = input.optString(field)
            if (v.isNotBlank()) return if (field == "task") "gradle $v" else v
        }
        return item.content.optString("name")
    }

    /** 人性化耗时："2 分 18 秒"。 */
    fun formatWorked(ms: Long?): String {
        if (ms == null || ms < 0) return ""
        val totalSec = Math.round(ms / 1000.0)
        if (totalSec < 1) return "不到 1 秒"
        val h = totalSec / 3600
        val m = totalSec % 3600 / 60
        val s = totalSec % 60
        return when {
            h > 0 -> "$h 小时 $m 分"
            m > 0 -> if (s > 0) "$m 分 $s 秒" else "$m 分"
            else -> "$s 秒"
        }
    }

    fun statusLabel(status: String): String {
        val normalized = if (status == "canceling") "cancel_requested" else status
        return when (normalized) {
            "queued" -> "排队中"
            "running" -> "运行中"
            "awaiting_approval" -> "等待审批"
            "paused" -> "已暂停"
            "cancel_requested" -> "正在停止"
            "succeeded" -> "已完成"
            "failed" -> "失败"
            "canceled" -> "已停止"
            "interrupted" -> "已中断"
            "turn_started" -> "运行中"
            else -> status
        }
    }

    fun statusLabel(context: android.content.Context, status: String): String =
        UiFormat.jobStatusLabel(context, if (status == "canceling") "cancel_requested" else status)

    // ---------- 行生成 ----------

    /**
     * 把 Turn 列表展开为 RecyclerView 行。
     * @param hasEarlierHistory 是否还有更早历史可加载（列表头部显示加载行）。
     */
    fun buildRows(
        turns: List<TurnGroup>,
        policy: ExpansionPolicy,
        hasEarlierHistory: Boolean = false,
    ): List<Row> {
        val rows = ArrayList<Row>()
        if (hasEarlierHistory) rows.add(Row.LoadingHistory())
        turns.forEachIndexed { index, turn ->
            val isLast = index == turns.size - 1
            val expanded = policy.isExpanded(turn, isLast)
            turn.userMessage?.let { user ->
                rows.add(
                    Row.User(
                        id = "row:${user.key}",
                        version = user.version,
                        turnKey = turn.key,
                        text = user.content.optString("text"),
                    ),
                )
            }
            if (turn.workItems.isNotEmpty() || turn.status == "running") {
                val title = if (turn.durationMs != null || turn.isCurrent) {
                    if (turn.isCurrent && turn.durationMs == null) "工作中…" else "工作了 ${formatWorked(turn.durationMs)}"
                } else "已结束"
                rows.add(
                    Row.WorkGroup(
                        id = "work:${turn.key}",
                        version = turn.workItems.sumOf { it.version } + turn.status.hashCode() % 1000 + if (expanded) 100_000 else 0,
                        turnKey = turn.key,
                        status = turn.status,
                        title = title,
                        summary = turn.summary,
                        expanded = expanded,
                        steps = turn.workItems,
                    ),
                )
            }
            turn.finalMessages.forEach { msg ->
                rows.add(
                    Row.Assistant(
                        id = "row:${msg.key}",
                        version = msg.version,
                        turnKey = turn.key,
                        text = msg.content.optString("text"),
                        streaming = msg.streaming,
                    ),
                )
            }
            // 没有任何回答且轮次已失败：错误卡兜底
            if (turn.finalMessages.isEmpty() && turn.status == "failed") {
                val errorText = turn.lifecycle?.content?.optString("error").orEmpty()
                    .ifBlank { turn.workItems.lastOrNull { it.type == TimelineStore.ItemType.ERROR }?.content?.optString("message").orEmpty() }
                if (errorText.isNotBlank()) {
                    rows.add(Row.Error("err:${turn.key}", 1, turn.key, errorText))
                }
            }
            turn.changes?.let { changes ->
                val files = ArrayList<String>()
                val arr = changes.content.optJSONArray("files")
                if (arr != null) for (i in 0 until arr.length()) files.add(arr.optString(i))
                if (files.isNotEmpty()) {
                    rows.add(Row.Changes("row:${changes.key}", changes.version, turn.key, files))
                }
            }
            if (turn.status in TERMINAL_STATUSES || turn.status == "awaiting_approval") {
                rows.add(
                    Row.Result(
                        id = "result:${turn.key}",
                        version = turn.lifecycle?.version ?: 1,
                        turnKey = turn.key,
                        status = turn.status,
                        durationMs = turn.durationMs,
                        provider = turn.lifecycle?.content?.optString("provider")?.takeIf { it.isNotBlank() },
                        model = turn.lifecycle?.content?.optString("model")?.takeIf { it.isNotBlank() },
                    ),
                )
            }
            // 错误条目（非终态轮内联错误）
            turn.workItems.filter { it.type == TimelineStore.ItemType.ERROR }.forEach { err ->
                rows.add(Row.Error("row:${err.key}", err.version, turn.key, err.content.optString("message")))
            }
        }
        return rows
    }
}
