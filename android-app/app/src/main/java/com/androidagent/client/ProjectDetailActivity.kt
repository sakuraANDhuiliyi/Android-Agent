package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.androidagent.client.databinding.ActivityProjectDetailBinding
import com.androidagent.client.databinding.DialogRenameConversationBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Project Hub：header + 快速继续 + Conversation 列表 + 辅助入口。 */
class ProjectDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityProjectDetailBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: ConversationAdapter

    private var projectId: String = ""
    private var projectName: String = ""
    private var hasApk: Boolean = false
    private var latestConversation: ConversationInfo? = null
    private var pendingApprovalJob: JobInfo? = null
    private var activeJob: JobInfo? = null
    private var latestBuildJob: JobInfo? = null
    private var projectPackage: String = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityProjectDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        projectName = intent.getStringExtra(EXTRA_PROJECT_NAME).orEmpty()
        projectPackage = intent.getStringExtra(EXTRA_PACKAGE).orEmpty()
        hasApk = intent.getBooleanExtra(EXTRA_HAS_APK, false)
        binding.textHubPackage.text = projectPackage.ifBlank { projectId }
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        prefs.selectedProjectId = projectId

        binding.toolbar.title = projectName.ifBlank { projectId }
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.textHubName.text = projectName.ifBlank { projectId }
        binding.textHubPackage.text = projectPackage.ifBlank { projectId }
        binding.textHubStatus.text = ""

        adapter = ConversationAdapter(
            onOpen = { openConversation(it) },
            onRename = { renameConversation(it) },
            onArchive = { archiveConversation(it) },
        )
        binding.recyclerConversations.layoutManager = LinearLayoutManager(this)
        binding.recyclerConversations.adapter = adapter

        binding.fabNewConversation.setOnClickListener { createConversation() }
        binding.cardQuickContinue.setOnClickListener {
            latestConversation?.let { openConversation(it) } ?: createConversation()
        }
        binding.cardCurrentTask.setOnClickListener { openJobConversation(activeJob) }
        binding.cardPendingApproval.setOnClickListener {
            pendingApprovalJob?.let { job ->
                val conversationId = job.conversationId
                if (conversationId.isNullOrBlank()) {
                    toast(getString(R.string.open_conversation_failed))
                } else {
                    ConversationActivity.start(this, job.projectId, conversationId, "")
                }
            }
        }
        binding.btnViewApk.setOnClickListener { ApkActivity.start(this, projectId, null, hasApk) }
        binding.rowFiles.setOnClickListener { openFiles() }
        binding.rowChanges.setOnClickListener { DiffActivity.start(this, projectId) }
        binding.cardChangesSummary.setOnClickListener { DiffActivity.start(this, projectId) }
        binding.rowBuild.setOnClickListener { openBuildOrApk() }
        binding.cardBuildSummary.setOnClickListener { openBuildOrApk() }
        binding.cardTestsSummary.setOnClickListener { openBuildLog() }
        binding.cardProblemsSummary.setOnClickListener { FeedbackActivity.start(this, projectId, problems = true) }
        binding.rowConversations.setOnClickListener {
            binding.hubScroll.smoothScrollTo(0, binding.recyclerConversations.top)
        }
        binding.rowProblems.setOnClickListener { FeedbackActivity.start(this, projectId, problems = true) }
        binding.rowTerminal.setOnClickListener { RemoteTerminalActivity.start(this, projectId) }
        binding.rowHistory.setOnClickListener { ProjectHistoryActivity.start(this, projectId) }
        binding.rowContext.setOnClickListener { openFiles() }
        binding.rowSettings.setOnClickListener { ProjectConfigActivity.start(this, projectId) }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        lifecycleScope.launch {
            try {
                val data = withContext(Dispatchers.IO) {
                    val conversations = api.listConversations(projectId)
                    val jobs = api.listJobs(projectId)
                    val workspace = runCatching { api.getWorkspaceStatus(projectId) }.getOrNull()
                    val diff = runCatching { api.getDiff(projectId) }.getOrNull()
                    val feedback = runCatching { api.feedback(projectId) }.getOrNull()
                    DashboardData(conversations, jobs, workspace, diff, feedback)
                }
                renderConversations(data.conversations)
                renderDashboard(data)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun renderConversations(list: List<ConversationInfo>) {
        val sorted = list.sortedByDescending { it.updatedAt ?: 0.0 }
        adapter.submitList(sorted.take(3))
        latestConversation = sorted.firstOrNull()
        binding.textEmptyConversations.visibility = if (sorted.isEmpty()) View.VISIBLE else View.GONE
        val selected = prefs.selectedConversationId
        if (selected != null && sorted.none { it.id == selected }) {
            prefs.selectedConversationId = sorted.firstOrNull()?.id
        }
    }

    private fun renderDashboard(data: DashboardData) {
        val jobs = data.jobs.sortedByDescending { it.createdAt ?: 0.0 }
        // 快速继续
        val latest = latestConversation
        if (latest != null) {
            binding.cardQuickContinue.visibility = View.VISIBLE
            binding.textQuickContinueTitle.text = getString(R.string.hub_quick_continue, latest.title)
            binding.textQuickContinueMeta.text = buildString {
                append(UiFormat.relativeTime(this@ProjectDetailActivity, latest.updatedAt))
                val status = latest.lastTurnStatus.ifBlank { latest.status }
                if (status.isNotBlank()) {
                    if (isNotEmpty()) append(" · ")
                    append(UiFormat.jobStatusLabel(this@ProjectDetailActivity, status))
                }
            }
        } else {
            binding.cardQuickContinue.visibility = View.VISIBLE
            binding.textQuickContinueTitle.setText(R.string.start_agent_task)
            binding.textQuickContinueMeta.setText(R.string.hub_no_conversations)
        }

        // 待审批警告卡置顶
        val pendingJobs = jobs.filter { it.resolvedStatus() == "awaiting_approval" }
        val pendingJob = pendingJobs.firstOrNull()
        pendingApprovalJob = pendingJob
        binding.cardPendingApproval.visibility =
            if (pendingJob != null) View.VISIBLE else View.GONE
        if (pendingJob != null) {
            binding.textPendingMeta.text = getString(R.string.pending_count, pendingJobs.size)
        }

        // 状态行
        val lastJob = jobs.maxByOrNull {
            it.finishedAt ?: it.startedAt ?: it.createdAt ?: 0.0
        }
        binding.textHubStatus.text = buildString {
            val status = lastJob?.status ?: ""
            if (status.isNotBlank()) {
                append("上次任务${UiFormat.jobStatusLabel(this@ProjectDetailActivity, status)}")
                UiFormat.relativeTime(
                    this@ProjectDetailActivity,
                    lastJob?.finishedAt ?: lastJob?.createdAt,
                ).takeIf { it.isNotBlank() }?.let { append(" · $it") }
            } else {
                append("暂无任务")
            }
        }

        val workspace = data.workspace
        val branch = workspace?.branch ?: "workspace"
        val workspaceLabel = when {
            workspace == null -> getString(R.string.workspace_unknown)
            workspace.dirty -> getString(R.string.workspace_dirty, workspace.changedFiles)
            else -> getString(R.string.workspace_clean)
        }
        binding.textBranch.text = getString(R.string.branch_status, branch, workspaceLabel)

        activeJob = jobs.firstOrNull { UiFormat.isActive(it.resolvedStatus()) }
        val current = activeJob
        if (current == null) {
            binding.textCurrentTaskTitle.setText(R.string.no_active_task)
            binding.textCurrentTaskMeta.text = lastJob?.let {
                UiFormat.jobStatusLabel(this, it.resolvedStatus()) + " · " +
                    UiFormat.relativeTime(this, it.finishedAt ?: it.createdAt)
            }.orEmpty()
        } else {
            binding.textCurrentTaskTitle.text = "● ${current.prompt.ifBlank { getString(R.string.current_task) }}"
            binding.textCurrentTaskMeta.text = buildString {
                append(UiFormat.jobStatusLabel(this@ProjectDetailActivity, current.resolvedStatus()))
                val elapsed = current.durationMs ?: current.startedAt?.let {
                    ((System.currentTimeMillis() / 1000.0 - it) * 1000).toLong().coerceAtLeast(0)
                }
                elapsed?.let { append(" · ").append(formatDuration(it)) }
            }
        }

        val diffFiles = data.diff?.files.orEmpty()
        val additions = diffFiles.sumOf { it.additions }
        val deletions = diffFiles.sumOf { it.deletions }
        binding.textChangesSummary.text = getString(
            R.string.changes_summary,
            diffFiles.size,
            additions,
            deletions,
        )

        latestBuildJob = jobs.filter { it.hasBuildLog }.maxByOrNull { it.createdAt ?: 0.0 }
        val build = latestBuildJob
        binding.textBuildSummary.text = if (build == null) {
            getString(R.string.no_task_selected)
        } else {
            val mark = if (build.resolvedStatus() == "succeeded") "✓" else if (build.resolvedStatus() == "failed") "✕" else "●"
            "$mark ${UiFormat.jobStatusLabel(this, build.resolvedStatus())}${build.durationMs?.let { " · ${formatDuration(it)}" }.orEmpty()}"
        }

        val feedbackBuild = data.feedback?.optJSONObject("build")
        binding.textBuildSummary.text = when (feedbackBuild?.optString("status")) {
            "success" -> "✓ assembleDebug · %.1fs".format(feedbackBuild.optLong("duration_ms") / 1000.0)
            "failed" -> "✕ assembleDebug"
            else -> getString(R.string.feedback_not_run)
        }
        val tests = data.feedback?.optJSONObject("tests")?.optJSONObject("tests")
        binding.textTestsSummary.text = if (tests?.optBoolean("reported") == true) "${tests.optInt("passed")} passed · ${tests.optInt("failed")} failed" else getString(R.string.tests_not_reported)
        val problemCount = data.feedback?.optJSONArray("problems")?.length() ?: 0
        binding.textProblemsSummary.text = if (problemCount == 0) {
            getString(R.string.problems_none)
        } else {
            getString(R.string.problems_count, problemCount)
        }

        // APK 卡
        val apkReady = hasApk || jobs.any { it.hasApk }
        hasApk = apkReady
        binding.cardApk.visibility = if (apkReady) View.VISIBLE else View.GONE
        binding.textApkMeta.text = getString(R.string.hub_view_apk)
    }

    private fun openFiles() {
        FileBrowserActivity.start(
            this,
            ProjectInfo(projectId, projectName, projectPackage, hasApk, null, null),
            prefs.serverUrl,
            prefs.apiToken,
        )
    }

    private fun openJobConversation(job: JobInfo?) {
        val conversationId = job?.conversationId
        if (conversationId.isNullOrBlank()) {
            latestConversation?.let { openConversation(it) } ?: createConversation()
        } else {
            val title = latestConversation?.takeIf { it.id == conversationId }?.title.orEmpty()
            ConversationActivity.start(this, projectId, conversationId, title, job.id)
        }
    }

    private fun openBuildOrApk() {
        FeedbackActivity.start(this, projectId)
    }

    private fun openBuildLog() {
        FeedbackActivity.start(this, projectId)
    }

    private fun formatDuration(durationMs: Long): String {
        val seconds = (durationMs / 1000).coerceAtLeast(1)
        return if (seconds < 60) "${seconds}s" else "%d:%02d".format(seconds / 60, seconds % 60)
    }

    private data class DashboardData(
        val conversations: List<ConversationInfo>,
        val jobs: List<JobInfo>,
        val workspace: WorkspaceStatus?,
        val diff: DiffSummary?,
        val feedback: org.json.JSONObject?,
    )

    private fun createConversation() {
        lifecycleScope.launch {
            try {
                val conv = withContext(Dispatchers.IO) { api.createConversation(projectId) }
                prefs.selectedConversationId = conv.id
                openConversation(conv)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun openConversation(conv: ConversationInfo) {
        prefs.selectedConversationId = conv.id
        ConversationActivity.start(this, projectId, conv.id, conv.title)
    }

    private fun renameConversation(conv: ConversationInfo) {
        val dialogBinding = DialogRenameConversationBinding.inflate(LayoutInflater.from(this))
        dialogBinding.editTitle.setText(conv.title)
        AlertDialog.Builder(this)
            .setTitle(R.string.rename)
            .setView(dialogBinding.root)
            .setPositiveButton(R.string.save) { _, _ ->
                val title = dialogBinding.editTitle.text?.toString()?.trim().orEmpty()
                if (title.isBlank()) return@setPositiveButton
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { api.renameConversation(conv.id, title) }
                        refresh()
                    } catch (e: Exception) {
                        toast(userMessage(e))
                    }
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun archiveConversation(conv: ConversationInfo) {
        AlertDialog.Builder(this)
            .setTitle(R.string.archive)
            .setMessage(conv.title)
            .setPositiveButton(R.string.archive) { _, _ ->
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { api.archiveConversation(conv.id) }
                        if (prefs.selectedConversationId == conv.id) {
                            prefs.selectedConversationId = null
                        }
                        refresh()
                    } catch (e: Exception) {
                        toast(userMessage(e))
                    }
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun userMessage(e: Exception): String {
        return when (e) {
            is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message.orEmpty()
            else -> e.message ?: "错误"
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val EXTRA_PROJECT_NAME = "project_name"
        private const val EXTRA_HAS_APK = "has_apk"
        private const val EXTRA_PACKAGE = "package_name"

        fun start(context: Context, project: ProjectInfo) {
            context.startActivity(
                Intent(context, ProjectDetailActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, project.id)
                    .putExtra(EXTRA_PROJECT_NAME, project.name)
                    .putExtra(EXTRA_HAS_APK, project.hasApk)
                    .putExtra(EXTRA_PACKAGE, project.packageName),
            )
        }
    }
}
