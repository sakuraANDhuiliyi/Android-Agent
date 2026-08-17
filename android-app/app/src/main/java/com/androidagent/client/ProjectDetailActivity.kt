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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityProjectDetailBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        projectName = intent.getStringExtra(EXTRA_PROJECT_NAME).orEmpty()
        hasApk = intent.getBooleanExtra(EXTRA_HAS_APK, false)
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
        binding.textHubPackage.text = projectId
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
            latestConversation?.let { openConversation(it) }
        }
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
        binding.rowFiles.setOnClickListener {
            FileBrowserActivity.start(
                this,
                ProjectInfo(projectId, projectName, "", hasApk, null, null),
                prefs.serverUrl,
                prefs.apiToken,
            )
        }
        binding.rowChanges.setOnClickListener { DiffActivity.start(this, projectId) }
        binding.rowBuild.setOnClickListener { ApkActivity.start(this, projectId, null, hasApk) }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        lifecycleScope.launch {
            try {
                val (conversations, jobs) = withContext(Dispatchers.IO) {
                    api.listConversations(projectId) to api.listJobs(projectId)
                }
                renderConversations(conversations)
                renderQuickCards(conversations, jobs)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun renderConversations(list: List<ConversationInfo>) {
        val sorted = list.sortedByDescending { it.updatedAt ?: 0.0 }
        adapter.submitList(sorted)
        latestConversation = sorted.firstOrNull()
        binding.textEmptyConversations.visibility = if (sorted.isEmpty()) View.VISIBLE else View.GONE
        val selected = prefs.selectedConversationId
        if (selected != null && sorted.none { it.id == selected }) {
            prefs.selectedConversationId = sorted.firstOrNull()?.id
        }
    }

    private fun renderQuickCards(conversations: List<ConversationInfo>, jobs: List<JobInfo>) {
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
            binding.cardQuickContinue.visibility = View.GONE
        }

        // 待审批警告卡置顶
        val pendingJob = jobs.firstOrNull { it.status == "awaiting_approval" }
        pendingApprovalJob = pendingJob
        binding.cardPendingApproval.visibility =
            if (pendingJob != null) View.VISIBLE else View.GONE

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

        // APK 卡
        val apkReady = hasApk || jobs.any { it.hasApk }
        binding.cardApk.visibility = if (apkReady) View.VISIBLE else View.GONE
        binding.textApkMeta.text = getString(R.string.hub_view_apk)
    }

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

        fun start(context: Context, project: ProjectInfo) {
            context.startActivity(
                Intent(context, ProjectDetailActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, project.id)
                    .putExtra(EXTRA_PROJECT_NAME, project.name)
                    .putExtra(EXTRA_HAS_APK, project.hasApk),
            )
        }
    }
}
