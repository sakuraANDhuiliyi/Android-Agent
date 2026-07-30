package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityConversationBinding
import com.androidagent.client.databinding.ItemApprovalBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class ConversationActivity : AppCompatActivity() {

    private lateinit var binding: ActivityConversationBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi

    private var projectId: String = ""
    private var conversationId: String = ""
    private var conversationTitle: String = ""
    private var currentJobId: String? = null
    private var currentJob: JobInfo? = null
    private var watcher: JobWatcher? = null
    private val eventLines = StringBuilder()
    private var lastToolName: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConversationBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        conversationId = intent.getStringExtra(EXTRA_CONVERSATION_ID).orEmpty()
        conversationTitle = intent.getStringExtra(EXTRA_CONVERSATION_TITLE).orEmpty()
        if (projectId.isBlank() || conversationId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        prefs.selectedProjectId = projectId
        prefs.selectedConversationId = conversationId

        binding.toolbar.title = conversationTitle.ifBlank { conversationId.take(8) }
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }

        binding.btnSend.setOnClickListener { sendAsk() }
        binding.btnSteer.setOnClickListener { sendMidTask("steer") }
        binding.btnFollowUp.setOnClickListener { sendMidTask("follow_up") }
        binding.btnPause.setOnClickListener { controlJob("pause") }
        binding.btnResume.setOnClickListener { controlJob("resume") }
        binding.btnCancel.setOnClickListener { controlJob("cancel") }
        binding.btnViewDiff.setOnClickListener { DiffActivity.start(this, projectId) }
        binding.btnViewLog.setOnClickListener {
            val jobId = currentJobId
            if (jobId == null) toast(getString(R.string.no_task_selected))
            else BuildLogActivity.start(this, jobId)
        }
        binding.btnApk.setOnClickListener {
            ApkActivity.start(this, projectId, currentJobId, currentJob?.hasApk == true)
        }
    }

    override fun onStart() {
        super.onStart()
        restoreSession()
    }

    override fun onStop() {
        currentJobId?.let { prefs.setEventCursor(it, watcher?.currentCursor() ?: prefs.eventCursor(it)) }
        watcher?.stop()
        super.onStop()
    }

    private fun restoreSession() {
        lifecycleScope.launch {
            try {
                // Restore conversation events cursor page (display cache).
                val after = prefs.conversationEventCursor(conversationId)
                val page = withContext(Dispatchers.IO) {
                    api.listConversationEvents(conversationId, afterSeq = after.takeIf { it > 0 }, limit = 100)
                }
                page.nextAfterSeq?.let { prefs.setConversationEventCursor(conversationId, it) }
                if (page.events.isNotEmpty() && eventLines.isEmpty()) {
                    page.events.takeLast(30).forEach { appendEvent(it) }
                }

                val jobs = withContext(Dispatchers.IO) { api.listJobs(projectId, conversationId) }
                val preferred = prefs.selectedJobId
                val active = jobs.firstOrNull { it.id == preferred }
                    ?: jobs.firstOrNull { it.status in ACTIVE_STATUSES }
                    ?: jobs.firstOrNull()
                if (active != null) {
                    attachJob(active.id, resume = true)
                } else {
                    binding.textJobStatus.text = getString(R.string.no_task_selected)
                }
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun sendAsk() {
        val prompt = binding.editPrompt.text?.toString()?.trim().orEmpty()
        if (prompt.isBlank()) {
            toast("请填写需求")
            return
        }
        setBusy(true)
        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    api.askConversation(conversationId, prompt, provider = prefs.selectedProviderId.takeUnless { it == "auto" })
                }
                binding.editPrompt.setText("")
                attachJob(job.id, resume = false)
            } catch (e: Exception) {
                toast(userMessage(e))
            } finally {
                setBusy(false)
            }
        }
    }

    private fun sendMidTask(type: String) {
        val jobId = currentJobId ?: run {
            toast(getString(R.string.no_task_selected))
            return
        }
        val text = binding.editPrompt.text?.toString()?.trim().orEmpty()
        if (text.isBlank()) {
            toast("请输入内容")
            return
        }
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    if (type == "steer") api.steerJob(jobId, text) else api.followUpJob(jobId, text)
                }
                binding.editPrompt.setText("")
                toast("已发送")
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun controlJob(action: String) {
        val jobId = currentJobId ?: return
        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) {
                    when (action) {
                        "pause" -> api.pauseJob(jobId)
                        "resume" -> api.resumeJob(jobId)
                        else -> api.cancelJob(jobId)
                    }
                }
                renderJob(job)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun attachJob(jobId: String, resume: Boolean) {
        currentJobId = jobId
        prefs.selectedJobId = jobId
        watcher?.stop()
        val cursor = if (resume) prefs.eventCursor(jobId) else 0L
        watcher = JobWatcher(
            api = api,
            scope = lifecycleScope,
            onEvent = { event ->
                runOnUiThread { appendEvent(event) }
            },
            onJob = { job ->
                runOnUiThread { renderJob(job) }
            },
            onDone = { job ->
                runOnUiThread {
                    renderJob(job)
                    prefs.setEventCursor(jobId, watcher?.currentCursor() ?: prefs.eventCursor(jobId))
                }
            },
            onError = { err ->
                runOnUiThread { binding.textCurrentTool.text = "同步: ${err.message}" }
            },
        ).also { it.start(jobId, cursor) }

        lifecycleScope.launch {
            try {
                val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
                renderJob(job)
                refreshApprovals(jobId)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun renderJob(job: JobInfo) {
        currentJob = job
        val started = job.startedAt ?: job.createdAt
        val now = System.currentTimeMillis() / 1000.0
        val elapsed = started?.let { ((job.finishedAt ?: now) - it).toInt() }
        binding.textJobStatus.text = "状态: ${statusLabel(job.status)} · 耗时: ${elapsed?.let { "${it}s" } ?: "—"} · ${job.provider ?: "?"}/${job.model ?: "?"}"
        if (job.plan.isNotEmpty()) {
            binding.textPlan.visibility = View.VISIBLE
            binding.textPlan.text = "Plan:\n" + job.plan.joinToString("\n") {
                val st = it.optString("status")
                val title = it.optString("title").ifBlank { it.optString("text") }
                "- [$st] $title"
            }
        }
        lastToolName?.let { binding.textCurrentTool.text = "当前工具: $it" }
        job.result?.let { binding.textResult.text = it }
        job.error?.let { binding.textResult.text = "错误: $it" }
        binding.btnPause.isEnabled = job.status == "running"
        binding.btnResume.isEnabled = job.status == "paused"
        binding.btnCancel.isEnabled = job.status in ACTIVE_STATUSES
        binding.btnApk.isEnabled = job.hasApk || job.status == "succeeded"
        if (job.status == "awaiting_approval") {
            refreshApprovals(job.id)
        }
    }

    private fun refreshApprovals(jobId: String) {
        lifecycleScope.launch {
            try {
                val approvals = withContext(Dispatchers.IO) { api.listApprovals(jobId) }
                binding.layoutApprovals.removeAllViews()
                for (approval in approvals.filter { it.status == "pending" }) {
                    binding.layoutApprovals.addView(buildApprovalCard(jobId, approval))
                }
            } catch (_: Exception) {
                /* ignore transient */
            }
        }
    }

    private fun buildApprovalCard(jobId: String, approval: ApprovalInfo): View {
        val item = ItemApprovalBinding.inflate(LayoutInflater.from(this), binding.layoutApprovals, false)
        item.textRisk.text = (approval.risk ?: "process").uppercase()
        item.textKind.text = approval.kind.ifBlank { "approval" }
        val scope = approval.payload.optString("scope")
            .ifBlank { approval.payload.optString("path") }
            .ifBlank { approval.payload.optString("url") }
            .ifBlank { approval.toolCallId ?: "" }
        item.textScope.text = "范围: ${scope.ifBlank { "—" }}"
        item.textPayload.text = approval.payload.toString().take(1200)
        item.btnApprove.setOnClickListener { decide(jobId, approval.id, true) }
        item.btnReject.setOnClickListener { decide(jobId, approval.id, false) }
        return item.root
    }

    private fun decide(jobId: String, approvalId: String, approved: Boolean) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.resolveApproval(jobId, approvalId, approved) }
                toast(if (approved) "已批准" else "已拒绝")
                refreshApprovals(jobId)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun appendEvent(event: JSONObject) {
        val type = event.optString("type")
        if (type == "tool_call") {
            lastToolName = event.optString("name").ifBlank { event.optString("tool") }
            binding.textCurrentTool.text = "当前工具: $lastToolName"
        }
        if (type == "approval_required") {
            currentJobId?.let { refreshApprovals(it) }
        }
        val line = formatEvent(event)
        if (line.isBlank()) return
        if (eventLines.isNotEmpty()) eventLines.append('\n')
        eventLines.append(line)
        // Keep display bounded for large logs.
        val text = eventLines.toString()
        binding.textEvents.text = if (text.length > 40_000) text.takeLast(40_000) else text
        binding.scrollContent.post { binding.scrollContent.fullScroll(View.FOCUS_DOWN) }
        prefs.cacheSnippet("events:$conversationId", binding.textEvents.text.toString())
    }

    private fun formatEvent(event: JSONObject): String {
        val type = event.optString("type")
        val message = event.optString("message")
        return when (type) {
            "text", "assistant_message" -> event.optString("content", message)
            "tool_call" -> "→ ${event.optString("name").ifBlank { event.optString("tool") }}"
            "tool_result" -> "← ${event.optString("name")} ${if (event.optBoolean("ok", true)) "ok" else "fail"}"
            "approval_required" -> "需要审批: ${event.optString("kind")}"
            "approval_resolved" -> "审批结果: ${event.optString("decision").ifBlank { message }}"
            "usage" -> "tokens: ${event.optJSONObject("usage")}"
            "failed" -> "失败: ${event.optString("error").ifBlank { message }}"
            else -> message.ifBlank { type }
        }
    }

    private fun statusLabel(status: String): String = when (status) {
        "queued" -> "排队中"
        "running" -> "运行中"
        "paused" -> "已暂停"
        "awaiting_approval" -> "等待审批"
        "succeeded" -> "成功"
        "failed" -> "失败"
        "canceled" -> "已取消"
        else -> status
    }

    private fun setBusy(busy: Boolean) {
        binding.btnSend.isEnabled = !busy
    }

    private fun userMessage(e: Exception): String = when (e) {
        is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message.orEmpty()
        else -> e.message ?: "错误"
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val EXTRA_CONVERSATION_ID = "conversation_id"
        private const val EXTRA_CONVERSATION_TITLE = "conversation_title"
        private val ACTIVE_STATUSES = setOf("queued", "running", "paused", "awaiting_approval")

        fun start(context: Context, projectId: String, conversationId: String, title: String) {
            context.startActivity(
                Intent(context, ConversationActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, projectId)
                    .putExtra(EXTRA_CONVERSATION_ID, conversationId)
                    .putExtra(EXTRA_CONVERSATION_TITLE, title),
            )
        }
    }
}
