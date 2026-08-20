package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.text.Spanned
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
import com.androidagent.client.databinding.ItemTurnResultBinding
import com.androidagent.client.databinding.ItemUserMessageBinding
import com.androidagent.client.databinding.ItemWorkGroupBinding
import io.noties.markwon.AbstractMarkwonPlugin
import io.noties.markwon.Markwon
import io.noties.markwon.ext.tables.TableAwareMovementMethod
import io.noties.markwon.ext.tables.TablePlugin
import org.json.JSONArray
import java.lang.ref.WeakReference

/**
 * 会话时间线适配器：ListAdapter + DiffUtil + 稳定 ID。
 * ViewType：加载历史 / 用户消息 / 工作组 / 回答 / 改动卡 / 结果卡 / 错误。
 */
class ConversationTimelineAdapter(
    private val callbacks: Callbacks,
) : ListAdapter<Row, RecyclerView.ViewHolder>(RowDiff) {

    interface Callbacks {
        fun onToggleWork(turnKey: String, expanded: Boolean)
        fun onApprovalAction(model: ApprovalCardBinder.Model, approve: Boolean, always: Boolean = false)
        fun onViewChanges(turnKey: String)
        fun onLoadEarlier()
        fun onAgentFix(message: String) {}
        fun onViewErrorDetails() {}
    }

    object RowDiff : DiffUtil.ItemCallback<Row>() {
        override fun areItemsTheSame(oldItem: Row, newItem: Row): Boolean = oldItem.id == newItem.id
        override fun areContentsTheSame(oldItem: Row, newItem: Row): Boolean = oldItem == newItem
        override fun getChangePayload(oldItem: Row, newItem: Row): Any? =
            if (oldItem.id == newItem.id) PAYLOAD_CONTENT else null
    }

    /** 条目级展开状态独立于数据，避免流式刷新被重置。 */
    val toolExpanded = HashMap<String, Boolean>()
    val approvalDetailExpanded = HashSet<String>()

    fun resetViewState() {
        toolExpanded.clear()
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
        private const val TYPE_LOADING = 0
        private const val TYPE_USER = 1
        private const val TYPE_WORK = 2
        private const val TYPE_ASSISTANT = 3
        private const val TYPE_CHANGES = 4
        private const val TYPE_RESULT = 5
        private const val TYPE_ERROR = 6
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

        fun stepStatusText(step: TimelineStore.TimelineItem): String = when (step.status) {
            "running" -> "运行中"
            "waiting_approval" -> "待审批"
            "success", "done" -> step.content.optLong("duration_ms", 0L).takeIf { it > 0 }
                ?.let { formatDurationMs(it) } ?: "完成"
            "failed" -> "失败"
            "canceled", "interrupted" -> "已取消"
            else -> ""
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
        is Row.Result -> TYPE_RESULT
        is Row.Error -> TYPE_ERROR
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return when (viewType) {
            TYPE_LOADING -> LoadingVH(ItemLoadingHistoryBinding.inflate(inflater, parent, false))
            TYPE_USER -> UserVH(ItemUserMessageBinding.inflate(inflater, parent, false))
            TYPE_WORK -> WorkVH(ItemWorkGroupBinding.inflate(inflater, parent, false))
            TYPE_ASSISTANT -> AssistantVH(ItemAssistantMessageBinding.inflate(inflater, parent, false))
            TYPE_CHANGES -> ChangesVH(ItemChangesSummaryBinding.inflate(inflater, parent, false))
            TYPE_RESULT -> ResultVH(ItemTurnResultBinding.inflate(inflater, parent, false))
            else -> ErrorVH(ItemErrorMessageBinding.inflate(inflater, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val row = getItem(position)) {
            is Row.LoadingHistory -> (holder as LoadingVH).bind(row, callbacks)
            is Row.User -> (holder as UserVH).bind(row)
            is Row.WorkGroup -> (holder as WorkVH).bind(row, callbacks, this)
            is Row.Assistant -> (holder as AssistantVH).bind(row, markwon(holder.itemView.context))
            is Row.Changes -> (holder as ChangesVH).bind(row, callbacks)
            is Row.Result -> (holder as ResultVH).bind(row)
            is Row.Error -> (holder as ErrorVH).bind(row, callbacks)
        }
    }

    // ---------- ViewHolders ----------

    class LoadingVH(private val binding: ItemLoadingHistoryBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.LoadingHistory, callbacks: Callbacks) {
            binding.progressEarlier.visibility = if (row.loading) View.VISIBLE else View.GONE
            binding.textLoadEarlier.setText(if (row.loading) R.string.loading_earlier else R.string.load_earlier)
            binding.root.isEnabled = !row.loading
            binding.root.setOnClickListener { if (!row.loading) callbacks.onLoadEarlier() }
        }
    }

    class UserVH(private val binding: ItemUserMessageBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.User) {
            binding.textUserMessage.text = row.text
        }
    }

    class ResultVH(private val binding: ItemTurnResultBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.Result) {
            binding.textResultStatus.text = ConversationTimelineBuilder.statusLabel(row.status)
            binding.textResultStatus.setTextColor(statusColor(binding.root.context, row.status))
            val duration = ConversationTimelineBuilder.formatWorked(row.durationMs)
            binding.textResultDuration.text = if (duration.isBlank()) "" else "总耗时 $duration"
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
            binding.textChangesCount.text =
                binding.root.context.getString(R.string.files_changed, row.files.size)
            binding.textChangesFiles.text = row.files.take(4).joinToString("\n") +
                if (row.files.size > 4) "\n…" else ""
            binding.btnViewChanges.setOnClickListener { callbacks.onViewChanges(row.turnKey) }
        }
    }

    class AssistantVH(private val binding: ItemAssistantMessageBinding) : RecyclerView.ViewHolder(binding.root) {
        fun bind(row: Row.Assistant, markwon: Markwon) {
            val container = binding.layoutSegments
            binding.textStreamingHint.visibility = if (row.streaming) View.VISIBLE else View.GONE
            if (row.streaming) {
                // 流式快速路径：纯文本 + 光标，不做完整 Markdown 解析
                ensureChildCount(container, 1)
                val tv = ensureTextChild(container, 0)
                tv.text = if (row.text.endsWith("\n")) row.text else row.text + " ▌"
                return
            }
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
                        markwon.setMarkdown(tv, segment.text)
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
                movementMethod = TableAwareMovementMethod.create()
                setPadding(0, dp(context, 4), 0, dp(context, 4))
            }

        private fun ensureTextChild(container: ViewGroup, index: Int): TextView {
            val child = container.getChildAt(index)
            if (child is TextView && child !is CodeBlockView) return child
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
                binding.layoutSteps.visibility = View.GONE
                return
            }
            binding.layoutSteps.visibility = View.VISIBLE
            renderSteps(binding.layoutSteps, row.steps, callbacks, adapter)
        }

        private fun renderSteps(
            container: ViewGroup,
            steps: List<TimelineStore.TimelineItem>,
            callbacks: Callbacks,
            adapter: ConversationTimelineAdapter,
        ) {
            val inflater = LayoutInflater.from(container.context)
            while (container.childCount > steps.size) container.removeViewAt(container.childCount - 1)
            steps.forEachIndexed { index, step ->
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
                            b.root to StatusStepTag(b)
                        }
                        tag.bind(step)
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
            binding.textStepSummary.text = toolStepSummary(step)
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
        }

        private fun applyExpanded(expanded: Boolean) {
            binding.layoutStepDetail.visibility = if (expanded) View.VISIBLE else View.GONE
            binding.iconStepExpand.rotation = if (expanded) 180f else 0f
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

    class StatusStepTag(private val binding: ItemStatusLineBinding) {
        fun bind(step: TimelineStore.TimelineItem) {
            binding.rowStatus.setOnClickListener(null)
            binding.rowStatus.isClickable = false
            binding.iconStatusExpand.rotation = 0f
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
                    binding.textStatusCount.visibility = if (messages.length() > 1) View.VISIBLE else View.GONE
                    binding.textStatusCount.text = "共 ${messages.length()} 条"
                    binding.iconStatusExpand.visibility = if (messages.length() > 1) View.VISIBLE else View.GONE
                    if (messages.length() > 1) {
                        binding.rowStatus.isClickable = true
                        binding.rowStatus.setOnClickListener { v ->
                            val opened = v.tag == true
                            v.tag = !opened
                            binding.iconStatusExpand.rotation = if (opened) 0f else 180f
                            binding.textStatusLine.text = if (opened) last else {
                                (0 until messages.length()).joinToString("\n") { messages.optString(it) }
                            }
                        }
                    }
                }
                TimelineStore.ItemType.PLAN -> {
                    binding.textStatusLine.text = "计划: ${step.content.optString("text")}"
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
    }
}

fun copyToClipboard(context: Context, label: String, text: String) {
    val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    cm.setPrimaryClip(ClipData.newPlainText(label, text))
    Toast.makeText(context, R.string.copied, Toast.LENGTH_SHORT).show()
}
