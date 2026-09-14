package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.text.Spanned
import android.text.TextUtils
import android.text.Layout
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.ConversationTimelineBuilder.Row
import com.androidagent.client.databinding.ItemAssistantMessageBinding
import com.androidagent.client.databinding.ItemApprovalBinding
import com.androidagent.client.databinding.ItemChangesSummaryBinding
import com.androidagent.client.databinding.ItemCodeBlockBinding
import com.androidagent.client.databinding.ItemErrorMessageBinding
import com.androidagent.client.databinding.ItemLoadingHistoryBinding
import com.androidagent.client.databinding.ItemStatusLineBinding
import com.androidagent.client.databinding.ItemToolStepBinding
import com.androidagent.client.databinding.ItemToolClusterBinding
import com.androidagent.client.databinding.ItemUserMessageBinding
import com.androidagent.client.databinding.ItemWorkGroupBinding
import io.noties.markwon.AbstractMarkwonPlugin
import io.noties.markwon.Markwon
import io.noties.markwon.ext.tables.TableAwareMovementMethod
import io.noties.markwon.ext.tables.TablePlugin
import org.json.JSONArray
import org.json.JSONObject
import java.lang.ref.WeakReference

/**
 * 会话时间线适配器：ListAdapter + DiffUtil + 稳定 ID。
 * ViewType：加载历史 / 用户消息 / 工作组 / 回答 / Outcome / 错误。
 */
class ConversationTimelineAdapter(
    private val callbacks: Callbacks,
) : ListAdapter<Row, RecyclerView.ViewHolder>(RowDiff) {

    interface Callbacks {
        fun onToggleWork(turnKey: String, expanded: Boolean)
        fun onApprovalAction(model: ApprovalCardBinder.Model, approve: Boolean, always: Boolean = false)
        fun onViewChanges(turnKey: String)
        fun onRevertTurn(turnKey: String) {}
        fun onViewAgents(turnKey: String) {}
        fun onLoadEarlier()
        fun onAgentFix(message: String) {}
        fun onViewErrorDetails() {}
        fun onOpenApk(jobId: String?) {}
    }

    object RowDiff : DiffUtil.ItemCallback<Row>() {
        override fun areItemsTheSame(oldItem: Row, newItem: Row): Boolean = oldItem.id == newItem.id
        override fun areContentsTheSame(oldItem: Row, newItem: Row): Boolean = oldItem == newItem
        override fun getChangePayload(oldItem: Row, newItem: Row): Any? =
            if (oldItem.id == newItem.id) PAYLOAD_CONTENT else null
    }

    /** 条目级展开状态独立于数据，避免流式刷新被重置。 */
    val toolExpanded = HashMap<String, Boolean>()
    val clusterExpanded = HashMap<String, Boolean>()
    val statusExpanded = HashMap<String, Boolean>()
    val approvalDetailExpanded = HashSet<String>()

    /** 已定稿 markdown 的 Spanned 解析缓存：滚动复用 bind 不再重复解析。 */
    private val spannedCache = MarkdownSpannedCache()

    fun resetViewState() {
        toolExpanded.clear()
        clusterExpanded.clear()
        statusExpanded.clear()
        approvalDetailExpanded.clear()
    }

    private var markwonRef: WeakReference<Markwon>? = null

    fun markwon(context: Context): Markwon {
        markwonRef?.get()?.let { return it }
        val mw = Markwon.builder(context.applicationContext)
            .usePlugin(TablePlugin.create(context.applicationContext))
            .usePlugin(object : AbstractMarkwonPlugin() {
                override fun beforeSetText(textView: TextView, markdown: Spanned) {
                    textView.textSize = 16f
                    textView.setLineSpacing(0f, 1.55f)
                }
            })
            .build()
        markwonRef = WeakReference(mw)
        return mw
    }

    init {
        super.setHasStableIds(true)
    }

    override fun getItemId(position: Int): Long = getItem(position).id.hashCode().toLong()

    companion object {
        const val TYPE_LOADING = 0
        const val TYPE_USER = 1
        const val TYPE_WORK = 2
        const val TYPE_ASSISTANT = 3
        const val TYPE_CHANGES = 4
        const val TYPE_ERROR = 5
        const val TYPE_AGENTS = 6
        private val PAYLOAD_CONTENT = Any()
        private const val CODE_COLLAPSE_LINES = 24
        private const val TOOL_OUTPUT_DISPLAY_LIMIT = 2000

        private val TOOL_LABELS = mapOf(
            "read_file" to "读取文件",
            "list_files" to "列出文件",
            "search_code" to "搜索代码",
            "search_files" to "搜索文件",
            "web_search" to "联网搜索",
            "write_file" to "写入文件",
            "str_replace" to "替换内容",
            "apply_patch" to "应用补丁",
            "run_command" to "执行命令",
            "run_gradle" to "Gradle 构建",
            "download_file" to "下载文件",
            "git_status" to "Git 状态",
            "git_diff" to "Git 差异",
        )

        fun statusColor(context: Context, status: String): Int {
            val res = when (status) {
                "running", "queued", "turn_started" -> R.color.status_running
                "succeeded", "approved", "success" -> R.color.status_success
                "failed" -> R.color.status_failed
                "awaiting_approval", "waiting_approval", "paused", "canceling" -> R.color.status_warning
                else -> R.color.status_idle
            }
            return ContextCompat.getColor(context, res)
        }

        fun resolveColor(context: Context, attr: Int): Int {
            val typed = context.obtainStyledAttributes(intArrayOf(attr))
            return typed.getColor(0, 0xFF000000.toInt()).also { typed.recycle() }
        }

        fun dp(context: Context, value: Int): Int =
            (value * context.resources.displayMetrics.density + 0.5f).toInt()

        fun toolIcon(name: String): Int = when {
            name in setOf("read_file", "list_files", "git_status", "git_diff") -> R.drawable.ic_tool_read
            name in setOf("search_code", "search_files", "web_search") -> R.drawable.ic_tool_search
            name in setOf("run_command", "run_gradle") -> R.drawable.ic_tool_command
            name in setOf("write_file", "str_replace", "apply_patch", "download_file") -> R.drawable.ic_tool_edit
            else -> R.drawable.ic_tool_generic
        }

        fun toolLabel(name: String): String =
            TOOL_LABELS[name] ?: if (name.startsWith("mcp__")) "MCP 工具" else name.ifBlank { "工具" }

        fun toolStepSummary(step: TimelineStore.TimelineItem): String {
            val name = step.content.optString("name")
            val input = step.content.optJSONObject("input")
            val arg: String? = when {
                input?.optJSONArray("argv") != null ->
                    (0 until input.optJSONArray("argv")!!.length()).joinToString(" ") { input.optJSONArray("argv")!!.optString(it) }
                input != null -> listOf("command", "path", "pattern", "query", "task", "url")
                    .firstOrNull { input.optString(it).isNotBlank() }
                    ?.let { key -> if (key == "task") "gradle ${input.optString(key)}" else input.optString(key) }
                else -> null
            }
            return if (arg.isNullOrBlank()) toolLabel(name) else "${toolLabel(name)} · $arg"
        }

        fun toolTarget(step: TimelineStore.TimelineItem): String {
            val input = step.content.optJSONObject("input") ?: return ""
            input.optJSONArray("argv")?.let { argv ->
                return (0 until argv.length()).joinToString(" ") { argv.optString(it) }
            }
            for (field in listOf("command", "path", "pattern", "query", "task", "url")) {
                val value = input.optString(field)
                if (value.isNotBlank()) return if (field == "task") "gradle $value" else value
            }
            return ""
        }

        fun clusterLabel(category: String, count: Int): String = when (category) {
            "read" -> "读取 $count 个文件"
            "search" -> "搜索 $count 次"
            "write" -> "修改 $count 个文件"
            "command" -> "执行 $count 条命令"
            else -> "$count 个操作"
        }

        fun stepStatusText(step: TimelineStore.TimelineItem): String {
            // Domain Core：优先使用服务端结构化摘要，客户端不再解析日志
            step.content.optJSONObject("summary")?.let { return summaryStatusText(it, step) }
            return when (step.status) {
                "running" -> "运行中"
                "waiting_approval" -> "待审批"
                "success", "done" -> step.content.optLong("duration_ms", 0L).takeIf { it > 0 }
                    ?.let { formatDurationMs(it) } ?: "完成"
                "failed" -> "失败"
                "canceled", "interrupted" -> "已取消"
                else -> ""
            }
        }

        /** 结构化摘要状态行："成功 · APK 2.0MB · 12.3s" / "未通过 · 10 通过 · 2 失败"。 */
        fun summaryStatusText(summary: JSONObject, step: TimelineStore.TimelineItem): String {
            val success = if (summary.has("success") && !summary.isNull("success")) {
                summary.optBoolean("success")
            } else {
                step.status != "failed"
            }
            val parts = ArrayList<String>()
            if (summary.optString("kind") == "test") {
                parts.add(if (success) "通过" else "未通过")
                summary.optJSONObject("tests")?.let { tests ->
                    val counts = ArrayList<String>()
                    if (tests.has("passed") && !tests.isNull("passed")) counts.add("${tests.optInt("passed")} 通过")
                    if (tests.has("failed") && !tests.isNull("failed")) counts.add("${tests.optInt("failed")} 失败")
                    tests.optInt("skipped", 0).takeIf { it > 0 }?.let { counts.add("$it 跳过") }
                    if (counts.isNotEmpty()) parts.add(counts.joinToString(" · "))
                }
            } else {
                parts.add(if (success) "成功" else "失败")
                if (!success) {
                    summary.optInt("error_count", 0).takeIf { it > 0 }?.let { parts.add("$it 处错误") }
                }
                summary.optLong("apk_size_bytes", 0L).takeIf { it > 0 }?.let { parts.add("APK ${formatBytes(it)}") }
            }
            summary.optLong("duration_ms", 0L).takeIf { it > 0 }?.let { parts.add(formatDurationMs(it)) }
            return parts.joinToString(" · ")
        }

        fun formatBytes(bytes: Long): String = when {
            bytes >= 1024 * 1024 -> "%.1fMB".format(bytes / 1024.0 / 1024.0)
            bytes >= 1024 -> "%.0fKB".format(bytes / 1024.0)
            else -> "${bytes}B"
        }

        fun formatDurationMs(ms: Long): String =
            if (ms < 1000) "${ms}ms" else "%.1fs".format(ms / 1000.0)

        fun displayToolOutput(output: String): String =
            if (output.length > TOOL_OUTPUT_DISPLAY_LIMIT) {
                output.take(TOOL_OUTPUT_DISPLAY_LIMIT) + "\n…（输出已截断，复制可获取完整内容）"
            } else output

        fun prettyJson(obj: org.json.JSONObject): String = try {
            obj.toString(2)
        } catch (_: Exception) {
            obj.toString()
        }
    }

    override fun getItemViewType(position: Int): Int = when (getItem(position)) {
        is Row.LoadingHistory -> TYPE_LOADING
        is Row.User -> TYPE_USER
        is Row.WorkGroup -> TYPE_WORK
        is Row.Assistant -> TYPE_ASSISTANT
        is Row.Changes -> TYPE_CHANGES
        is Row.Error -> TYPE_ERROR
        is Row.Agents -> TYPE_AGENTS
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            TYPE_LOADING -> LoadingVH(ItemLoadingHistoryBinding.inflate(inflater, parent, false))
            TYPE_USER -> UserVH(ItemUserMessageBinding.inflate(inflater, parent, false))
            TYPE_WORK -> WorkVH(ItemWorkGroupBinding.inflate(inflater, parent, false))
            TYPE_ASSISTANT -> AssistantVH(ItemAssistantMessageBinding.inflate(inflater, parent, false))
            TYPE_CHANGES -> ChangesVH(ItemChangesSummaryBinding.inflate(inflater, parent, false))
            TYPE_AGENTS -> AgentsVH(com.google.android.material.button.MaterialButton(parent.context).apply {
                layoutParams = RecyclerView.LayoutParams(-1, -2)
                minHeight = (64 * resources.displayMetrics.density).toInt()
            })
            else -> ErrorVH(ItemErrorMessageBinding.inflate(inflater, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val row = getItem(position)) {
            is Row.LoadingHistory -> (holder as LoadingVH).bind(row, callbacks)
            is Row.User -> (holder as UserVH).bind(row)
            is Row.WorkGroup -> (holder as WorkVH).bind(row, callbacks, this)
            is Row.Assistant -> (holder as AssistantVH).bind(row, markwon(holder.itemView.context), spannedCache)
            is Row.Changes -> (holder as ChangesVH).bind(row, callbacks)
            is Row.Error -> (holder as ErrorVH).bind(row, callbacks)
            is Row.Agents -> (holder as AgentsVH).bind(row, callbacks)
        }
    }

    // ---------- ViewHolders ----------

    class AgentsVH(private val button: com.google.android.material.button.MaterialButton) : RecyclerView.ViewHolder(button) {
        fun bind(row: Row.Agents, callbacks: Callbacks) {
            button.text = row.summary + "\nView details ›"
            button.setOnClickListener { callbacks.onViewAgents(row.turnKey) }
        }
    }

    class LoadingVH(private val binding: ItemLoadingHistoryBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.LoadingHistory, callbacks: Callbacks) {
            binding.progressEarlier.visibility = if (row.loading) View.VISIBLE else View.GONE
            binding.textLoadEarlier.setText(if (row.loading) R.string.loading_earlier else R.string.load_earlier)
            binding.root.isEnabled = !row.loading
            binding.root.setOnClickListener { if (!row.loading) callbacks.onLoadEarlier() }
        }
    }

    class UserVH(private val binding: ItemUserMessageBinding) : RecyclerView.ViewHolder(binding.root) {
        init {
            binding.root.addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
                val available = view.width - view.paddingLeft - view.paddingRight
                if (available > 0) {
                    val limit = (available * 0.78f).toInt()
                    if (binding.textUserMessage.maxWidth != limit) binding.textUserMessage.maxWidth = limit
                }
            }
        }
        fun bind(row: Row.User) {
            binding.textUserMessage.text = row.text
        }
    }

    class ErrorVH(private val binding: ItemErrorMessageBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.Error, callbacks: Callbacks) {
            binding.textErrorMessage.text = row.message
            binding.btnAgentFix.setOnClickListener {
                callbacks.onAgentFix(binding.root.context.getString(R.string.agent_fix_prompt))
            }
            binding.btnViewError.setOnClickListener { callbacks.onViewErrorDetails() }
        }
    }

    class ChangesVH(private val binding: ItemChangesSummaryBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.Changes, callbacks: Callbacks) {
            val context = binding.root.context
            // 服务端行级统计（ChangesSummary），无统计时退回文件数展示
            val lineStats = listOfNotNull(
                row.additions?.takeIf { it > 0 }?.let { "+$it" },
                row.deletions?.takeIf { it > 0 }?.let { "−$it" },
            ).joinToString(" ").takeIf { it.isNotBlank() }
            binding.textChangesCount.text = context.getString(R.string.files_changed, row.files.size) +
                (lineStats?.let { " · $it" } ?: "")
            bindCount(binding.textChangesAdd, row.added, "+")
            bindCount(binding.textChangesMod, row.modified, "~")
            bindCount(binding.textChangesDel, row.deleted, "−")
            binding.textChangesFiles.text = row.files.take(4).joinToString("\n") +
                if (row.files.size > 4) "\n…" else ""
            binding.btnViewChanges.setOnClickListener { callbacks.onViewChanges(row.turnKey) }
            binding.btnRevertTurn.setOnClickListener { callbacks.onRevertTurn(row.turnKey) }
        }

        private fun bindCount(view: TextView, count: Int, prefix: String) {
            view.visibility = if (count > 0) View.VISIBLE else View.GONE
            view.text = "$prefix$count"
        }
    }

    class AssistantVH(private val binding: ItemAssistantMessageBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.Assistant, markwon: Markwon, cache: MarkdownSpannedCache) {
            val container = binding.layoutSegments
            binding.textStreamingHint.visibility = if (row.streaming) View.VISIBLE else View.GONE
            // Rendering is already coalesced to an 80 ms cadence by the
            // Activity. Use the same tolerant Markdown profile while streaming
            // so finalization does not suddenly replace plain text with a
            // differently measured layout.
            val segments = MarkdownCodec.split(row.text)
            if (segments.isEmpty()) {
                ensureChildCount(container, 0)
                return
            }
            ensureChildCount(container, segments.size)
            segments.forEachIndexed { index, segment ->
                when (segment) {
                    is MarkdownCodec.Segment.Text -> {
                        val tv = ensureTextChild(container, index)
                        if (row.streaming) {
                            // 流式文本每帧变化，缓存无法命中，直接解析
                            markwon.setMarkdown(tv, segment.text)
                        } else {
                            markwon.setParsedMarkdown(tv, cache.spanned(markwon, segment.text))
                        }
                    }
                    is MarkdownCodec.Segment.Code -> {
                        if (!ensureCodeChild(container, index)) {
                            container.removeViewAt(index)
                            container.addView(CodeBlockView(container.context), index)
                        }
                        (container.getChildAt(index) as CodeBlockView).bind(segment)
                    }
                }
            }
        }

        private fun ensureChildCount(container: ViewGroup, count: Int) {
            while (container.childCount > count) container.removeViewAt(container.childCount - 1)
            while (container.childCount < count) {
                container.addView(newTextNode(container))
            }
        }

        private fun newTextNode(container: ViewGroup): TextView =
            TextView(container.context).apply {
                setTextColor(resolveColor(context, com.google.android.material.R.attr.colorOnSurface))
                setTextIsSelectable(true)
                // Older platforms keep TextView's native line-breaking default.
                if (android.os.Build.VERSION.SDK_INT >= 29) {
                    breakStrategy = android.graphics.text.LineBreaker.BREAK_STRATEGY_HIGH_QUALITY
                }
                hyphenationFrequency = Layout.HYPHENATION_FREQUENCY_NORMAL
                movementMethod = TableAwareMovementMethod.create()
                setPadding(0, dp(context, 4), 0, dp(context, 4))
            }

        private fun ensureTextChild(container: ViewGroup, index: Int): TextView {
            val child = container.getChildAt(index)
            if (child is TextView) return child
            container.removeViewAt(index)
            val tv = newTextNode(container)
            container.addView(tv, index)
            return tv
        }

        private fun ensureCodeChild(container: ViewGroup, index: Int): Boolean {
            return container.getChildAt(index) is CodeBlockView
        }
    }

    /** 代码块控件：等宽 + 独立背景 + 横向滚动 + 复制 + 超长折叠。 */
    class CodeBlockView(context: Context) : LinearLayout(context) {
        private val binding = ItemCodeBlockBinding.inflate(LayoutInflater.from(context), this, true)
        private var expanded = false

        fun bind(segment: MarkdownCodec.Segment.Code) {
            binding.textCodeLang.text = segment.lang.ifBlank { "code" }
            val lines = segment.code.split('\n')
            val collapsible = lines.size > CODE_COLLAPSE_LINES
            binding.btnToggleCode.visibility = if (collapsible) View.VISIBLE else View.GONE
            binding.textCodeContent.text = when {
                !collapsible -> segment.code
                expanded -> segment.code
                else -> lines.take(CODE_COLLAPSE_LINES).joinToString("\n") + "\n…"
            }
            if (collapsible) {
                binding.btnToggleCode.setText(
                    if (expanded) R.string.code_block_collapse else R.string.code_block_expand
                )
                binding.btnToggleCode.setOnClickListener {
                    expanded = !expanded
                    bind(segment)
                }
            } else {
                binding.btnToggleCode.setOnClickListener(null)
            }
            binding.btnCopyCode.setOnClickListener {
                copyToClipboard(context, "code", segment.code)
            }
        }
    }

    class WorkVH(private val binding: ItemWorkGroupBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.WorkGroup, callbacks: Callbacks, adapter: ConversationTimelineAdapter) {
            val context = binding.root.context
            binding.textWorkTitle.text = row.title
            binding.statusDot.setBackgroundColor(statusColor(context, row.status))
            binding.textWorkSummary.visibility = if (row.expanded) View.GONE else View.VISIBLE
            binding.textWorkSummaryFull.visibility = if (row.expanded && row.summary.isNotBlank()) View.VISIBLE else View.GONE
            binding.textWorkSummary.text = row.summary
            binding.textWorkSummaryFull.text = row.summary
            binding.iconExpand.rotation = if (row.expanded) 180f else 0f
            binding.iconExpand.contentDescription =
                context.getString(if (row.expanded) R.string.collapse else R.string.expand)
            binding.rowHeader.setOnClickListener { callbacks.onToggleWork(row.turnKey, !row.expanded) }

            if (!row.expanded) {
                binding.layoutStepsWrap.visibility = View.GONE
                return
            }
            binding.layoutStepsWrap.visibility = View.VISIBLE
            renderSteps(binding.layoutSteps, row.steps, callbacks, adapter)
        }

        private fun renderSteps(
            container: ViewGroup,
            steps: List<ConversationTimelineBuilder.WorkEntry>,
            callbacks: Callbacks,
            adapter: ConversationTimelineAdapter,
        ) {
            val inflater = LayoutInflater.from(container.context)
            while (container.childCount > steps.size) container.removeViewAt(container.childCount - 1)
            steps.forEachIndexed { index, entry ->
                when (entry) {
                    is ConversationTimelineBuilder.WorkEntry.ToolCluster -> {
                        val tag = childBinder(container, index, ToolClusterTag::class.java) {
                            val b = ItemToolClusterBinding.inflate(inflater, container, false)
                            b.root to ToolClusterTag(b, adapter)
                        }
                        tag.bind(entry)
                    }
                    is ConversationTimelineBuilder.WorkEntry.Item -> {
                        val step = entry.item
                        when (step.type) {
                            TimelineStore.ItemType.TOOL -> {
                                val tag = childBinder(container, index, ToolStepTag::class.java) {
                                    val b = ItemToolStepBinding.inflate(inflater, container, false)
                                    b.root to ToolStepTag(b, adapter)
                                }
                                tag.bind(step)
                            }
                            TimelineStore.ItemType.APPROVAL -> {
                                val tag = childBinder(container, index, ApprovalStepTag::class.java) {
                                    val b = ItemApprovalBinding.inflate(inflater, container, false)
                                    b.root to ApprovalStepTag(b)
                                }
                                tag.bind(step, callbacks, adapter)
                            }
                            else -> {
                                val tag = childBinder(container, index, StatusStepTag::class.java) {
                                    val b = ItemStatusLineBinding.inflate(inflater, container, false)
                                    b.root to StatusStepTag(b, adapter)
                                }
                                tag.bind(step)
                            }
                        }
                    }
                }
            }
        }

        /** 容器子视图按 binder 类型复用，类型不匹配时原位替换。 */
        private fun <T : Any> childBinder(
            container: ViewGroup,
            index: Int,
            tagClass: Class<T>,
            create: () -> Pair<View, T>,
        ): T {
            while (container.childCount <= index) {
                val (view, tag) = create()
                view.tag = tag
                container.addView(view)
            }
            val child = container.getChildAt(index)
            val tag = child.tag
            if (tagClass.isInstance(tag)) {
                @Suppress("UNCHECKED_CAST")
                return tag as T
            }
            val (view, newTag) = create()
            view.tag = newTag
            container.removeViewAt(index)
            container.addView(view, index)
            return newTag
        }
    }

    class ToolStepTag(private val binding: ItemToolStepBinding, private val adapter: ConversationTimelineAdapter) {
        fun bind(step: TimelineStore.TimelineItem) {
            val context = binding.root.context
            val name = step.content.optString("name")
            binding.iconTool.setImageResource(toolIcon(name))
            binding.textStepSummary.text = toolLabel(name)
            binding.textStepTarget.text = toolTarget(step)
            binding.textStepTarget.visibility = if (binding.textStepTarget.text.isBlank()) View.GONE else View.VISIBLE
            binding.textStepStatus.text = stepStatusText(step)
            binding.textStepStatus.setTextColor(statusColor(context, step.status))

            val expanded = adapter.toolExpanded[step.key] ?: false
            applyExpanded(expanded)
            binding.rowStep.setOnClickListener {
                val next = !(adapter.toolExpanded[step.key] ?: false)
                adapter.toolExpanded[step.key] = next
                applyExpanded(next)
            }

            val meta = StringBuilder("工具: ${toolLabel(name)}")
            step.content.optJSONObject("input")?.let { input ->
                meta.append('\n').append(prettyJson(input))
            }
            step.content.optJSONObject("summary")?.let { summary ->
                meta.append("\n\n摘要:\n").append(prettyJson(summary))
            }
            step.content.optJSONObject("artifact")?.let { artifact ->
                meta.append("\n\n产物:\n").append(prettyJson(artifact))
            }
            binding.textStepMeta.text = meta

            val output = step.content.optString("output")
            val display = displayToolOutput(output)
            binding.scrollOutput.visibility = if (output.isBlank()) View.GONE else View.VISIBLE
            binding.textStepOutput.text = display
            binding.btnCopyStep.visibility =
                if (output.isBlank() && step.content.optJSONObject("input") == null) View.GONE else View.VISIBLE
            binding.btnCopyStep.setOnClickListener {
                val full = buildString {
                    append(binding.textStepMeta.text)
                    if (output.isNotBlank()) append("\n\n输出:\n").append(output)
                }
                copyToClipboard(context, "tool", full)
            }

            // Domain Core：构建产物（Artifact 事件）直达 APK 下载/安装页
            val apkArtifact = step.content.optJSONObject("artifact")
                ?.takeIf { it.optString("kind") == "apk" }
            binding.btnApkStep.visibility = if (apkArtifact != null) View.VISIBLE else View.GONE
            binding.btnApkStep.setOnClickListener {
                adapter.callbacks.onOpenApk(apkArtifact?.let(::artifactJobId))
            }
        }

        private fun applyExpanded(expanded: Boolean) {
            binding.layoutStepDetail.visibility = if (expanded) View.VISIBLE else View.GONE
            binding.iconStepExpand.rotation = if (expanded) 180f else 0f
        }
    }

    class ToolClusterTag(
        private val binding: ItemToolClusterBinding,
        private val adapter: ConversationTimelineAdapter,
    ) {
        fun bind(cluster: ConversationTimelineBuilder.WorkEntry.ToolCluster) {
            val context = binding.root.context
            binding.iconCluster.setImageResource(
                when (cluster.category) {
                    "read" -> R.drawable.ic_tool_read
                    "search" -> R.drawable.ic_tool_search
                    "write" -> R.drawable.ic_tool_edit
                    "command" -> R.drawable.ic_tool_command
                    else -> R.drawable.ic_tool_generic
                },
            )
            binding.textClusterTitle.text = clusterLabel(cluster.category, cluster.items.size)
            val failed = cluster.items.count { it.status == "failed" }
            binding.textClusterStatus.text = when {
                failed > 0 -> "$failed 失败"
                cluster.status == "running" -> "进行中"
                cluster.durationMs != null -> formatDurationMs(cluster.durationMs)
                else -> ""
            }
            binding.textClusterStatus.setTextColor(statusColor(context, cluster.status))

            val defaultExpanded = failed > 0
            val expanded = adapter.clusterExpanded[cluster.id] ?: defaultExpanded
            renderExpanded(cluster, expanded)
            binding.rowCluster.setOnClickListener {
                val next = !(adapter.clusterExpanded[cluster.id] ?: defaultExpanded)
                adapter.clusterExpanded[cluster.id] = next
                renderExpanded(cluster, next)
            }
        }

        private fun renderExpanded(cluster: ConversationTimelineBuilder.WorkEntry.ToolCluster, expanded: Boolean) {
            binding.layoutClusterMembers.visibility = if (expanded) View.VISIBLE else View.GONE
            binding.iconClusterExpand.rotation = if (expanded) 180f else 0f
            if (!expanded) return
            val inflater = LayoutInflater.from(binding.root.context)
            val container = binding.layoutClusterMembers
            while (container.childCount > cluster.items.size) container.removeViewAt(container.childCount - 1)
            cluster.items.forEachIndexed { index, item ->
                val tag = if (index < container.childCount && container.getChildAt(index).tag is ToolStepTag) {
                    container.getChildAt(index).tag as ToolStepTag
                } else {
                    val child = ItemToolStepBinding.inflate(inflater, container, false)
                    val binder = ToolStepTag(child, adapter)
                    child.root.tag = binder
                    if (index < container.childCount) container.removeViewAt(index)
                    container.addView(child.root, index)
                    binder
                }
                tag.bind(item)
            }
        }
    }

    class ApprovalStepTag(private val binding: ItemApprovalBinding) {
        fun bind(step: TimelineStore.TimelineItem, callbacks: Callbacks, adapter: ConversationTimelineAdapter) {
            val payload = step.content
            val model = ApprovalCardBinder.Model(
                approvalId = step.approvalId ?: step.key,
                jobId = step.jobId,
                kind = payload.optString("kind").ifBlank { payload.optString("approval_kind") },
                risk = payload.optString("risk").takeIf { it.isNotBlank() },
                payload = payload,
                status = step.status,
            )
            ApprovalCardBinder.bind(
                binding = binding,
                model = model,
                handlers = ApprovalCardBinder.Handlers(
                    onApprove = { callbacks.onApprovalAction(it, true) },
                    onReject = { callbacks.onApprovalAction(it, false) },
                    onAlwaysAllow = { callbacks.onApprovalAction(it, true, always = true) },
                ),
                expandedDetail = model.approvalId in adapter.approvalDetailExpanded,
                onToggleDetail = {
                    if (model.approvalId in adapter.approvalDetailExpanded) {
                        adapter.approvalDetailExpanded.remove(model.approvalId)
                    } else {
                        adapter.approvalDetailExpanded.add(model.approvalId)
                    }
                    bind(step, callbacks, adapter)
                },
            )
        }
    }

    class StatusStepTag(
        private val binding: ItemStatusLineBinding,
        private val adapter: ConversationTimelineAdapter,
    ) {
        fun bind(step: TimelineStore.TimelineItem) {
            binding.rowStatus.setOnClickListener(null)
            binding.rowStatus.isClickable = false
            binding.iconStatusExpand.rotation = 0f
            binding.textStatusLine.maxLines = 1
            binding.textStatusLine.ellipsize = TextUtils.TruncateAt.END
            binding.textStatusLine.setTextColor(
                resolveColor(binding.root.context, com.google.android.material.R.attr.colorOnSurfaceVariant)
            )
            when (step.type) {
                TimelineStore.ItemType.STATUS -> {
                    val messages = step.content.optJSONArray("messages") ?: JSONArray()
                    val last = step.content.optString("last").ifBlank {
                        if (messages.length() > 0) messages.optString(messages.length() - 1) else ""
                    }
                    binding.textStatusLine.text = last
                    val expandable = messages.length() > 1 || last.length > 80 || last.contains('\n')
                    binding.textStatusCount.visibility = if (messages.length() > 1) View.VISIBLE else View.GONE
                    binding.textStatusCount.text = "共 ${messages.length()} 条"
                    binding.iconStatusExpand.visibility = if (expandable) View.VISIBLE else View.GONE
                    if (expandable) {
                        val expanded = adapter.statusExpanded[step.key] ?: false
                        binding.rowStatus.isClickable = true
                        applyStatusExpansion(messages, last, expanded)
                        binding.rowStatus.setOnClickListener {
                            val next = !(adapter.statusExpanded[step.key] ?: false)
                            adapter.statusExpanded[step.key] = next
                            applyStatusExpansion(messages, last, next)
                        }
                    }
                }
                TimelineStore.ItemType.PLAN -> {
                    binding.textStatusLine.text = "计划: ${step.content.optString("text")}"
                    binding.textStatusLine.maxLines = Int.MAX_VALUE
                    binding.textStatusLine.ellipsize = null
                    binding.textStatusCount.visibility = View.GONE
                    binding.iconStatusExpand.visibility = View.GONE
                }
                TimelineStore.ItemType.CHANGES -> {
                    binding.textStatusLine.text = "文件改动: ${step.content.optJSONArray("files")?.length() ?: 0} 个"
                    binding.textStatusCount.visibility = View.GONE
                    binding.iconStatusExpand.visibility = View.GONE
                }
                TimelineStore.ItemType.ERROR -> {
                    binding.textStatusLine.text = step.content.optString("message")
                    binding.textStatusLine.maxLines = Int.MAX_VALUE
                    binding.textStatusLine.ellipsize = null
                    binding.textStatusLine.setTextColor(statusColor(binding.root.context, "failed"))
                    binding.textStatusCount.visibility = View.GONE
                    binding.iconStatusExpand.visibility = View.GONE
                }
                else -> {
                    binding.textStatusLine.text = step.content.optString("message")
                    binding.textStatusCount.visibility = View.GONE
                    binding.iconStatusExpand.visibility = View.GONE
                }
            }
        }

        private fun applyStatusExpansion(messages: JSONArray, last: String, expanded: Boolean) {
            binding.iconStatusExpand.rotation = if (expanded) 180f else 0f
            binding.textStatusLine.maxLines = if (expanded) Int.MAX_VALUE else 1
            binding.textStatusLine.ellipsize = if (expanded) null else TextUtils.TruncateAt.END
            binding.textStatusLine.text = if (expanded) {
                (0 until messages.length()).joinToString("\n") { messages.optString(it) }
            } else last
        }
    }
}

fun copyToClipboard(context: Context, label: String, text: String) {
    val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
    Toast.makeText(context, R.string.copied, Toast.LENGTH_SHORT).show()
}

/** Artifact 事件的 url 形如 /api/jobs/{task_id}/apk，直接解析出所属任务。 */
internal fun artifactJobId(artifact: JSONObject): String? =
    Regex("/api/jobs/([^/]+)/apk").find(artifact.optString("url"))?.groupValues?.get(1)

/**
 * 已定稿 markdown 文本的 Spanned LRU 缓存。
 * 滚动回收后重新 bind 时直接复用解析结果，跳过 Markwon 全文解析；
 * 流式文本不入缓存（每帧变化无法命中）。
 */
class MarkdownSpannedCache(
    maxEntries: Int = 64,
    private val maxCacheableChars: Int = 24_000,
) {
    private val cache = object : android.util.LruCache<String, Spanned>(maxEntries) {}

    fun spanned(markwon: Markwon, text: String): Spanned {
        if (text.length <= maxCacheableChars) {
            cache.get(text)?.let { return it }
        }
        val parsed = markwon.toMarkdown(text)
        if (text.length <= maxCacheableChars) cache.put(text, parsed)
        return parsed
    }

    fun clear() = cache.evictAll()
}
