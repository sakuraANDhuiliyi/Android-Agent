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

class ProjectDetailActivity : AppCompatActivity() {

    private lateinit var binding: ActivityProjectDetailBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: ConversationAdapter

    private var projectId: String = ""
    private var projectName: String = ""
    private var hasApk: Boolean = false

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
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.textProjectMeta.text = "ID: $projectId\nAPK: ${if (hasApk) "可用" else "暂无"}"

        adapter = ConversationAdapter(
            onOpen = { openConversation(it) },
            onRename = { renameConversation(it) },
            onArchive = { archiveConversation(it) },
        )
        binding.recyclerConversations.layoutManager = LinearLayoutManager(this)
        binding.recyclerConversations.adapter = adapter

        binding.btnNewConversation.setOnClickListener { createConversation() }
        binding.btnBrowseFiles.setOnClickListener {
            FileBrowserActivity.start(
                this,
                ProjectInfo(projectId, projectName, "", hasApk, null, null),
                prefs.serverUrl,
                prefs.apiToken,
            )
        }
        binding.btnOpenDiff.setOnClickListener {
            DiffActivity.start(this, projectId)
        }
        binding.btnOpenApk.setOnClickListener {
            ApkActivity.start(this, projectId, null, hasApk)
        }
    }

    override fun onResume() {
        super.onResume()
        refreshConversations()
    }

    private fun refreshConversations() {
        lifecycleScope.launch {
            try {
                val list = withContext(Dispatchers.IO) { api.listConversations(projectId) }
                adapter.submit(list)
                binding.textEmptyConversations.visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
                val selected = prefs.selectedConversationId
                if (selected != null && list.none { it.id == selected }) {
                    prefs.selectedConversationId = list.firstOrNull()?.id
                }
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
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
                        refreshConversations()
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
                        refreshConversations()
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
