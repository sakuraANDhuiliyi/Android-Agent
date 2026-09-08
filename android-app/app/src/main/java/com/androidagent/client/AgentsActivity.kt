package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AlertDialog
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class AgentsActivity : WorkspaceScreen() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        screen(getString(R.string.workspace_agents))
        val parentId = intent.getStringExtra("job_id").orEmpty()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    try {
                        val tasks = withContext(Dispatchers.IO) { api.listJobs(projectId) }
                        val children = tasks.filter { it.parentTaskId == parentId }
                        body.removeAllViews()
                        label("${children.size} agents · 子 Agent 对话单独保留，不展开到主时间线。")
                        for (job in tasks.filter { it.id == parentId } + children) {
                            button("${job.role ?: "Main Agent"} · ${UiFormat.jobStatusLabel(this@AgentsActivity, job.status)}\n${job.prompt.take(140)}") {
                                AlertDialog.Builder(this@AgentsActivity).setTitle(job.role ?: "Main Agent")
                                    .setMessage(job.error ?: job.result ?: "尚无结果 · ${job.status}")
                                    .setPositiveButton("View conversation") { _, _ -> job.conversationId?.let {
                                        ConversationActivity.start(this@AgentsActivity, projectId, it, job.role ?: "Main Agent", job.id)
                                    } }.setNeutralButton("Review") { _, _ -> DiffActivity.start(this@AgentsActivity, projectId, job.turnId) }
                                    .setNegativeButton("关闭", null).show()
                            }
                        }
                    } catch (e: kotlinx.coroutines.CancellationException) { throw e }
                    catch (e: Exception) { label(e.message.orEmpty()) }
                    delay(5000)
                }
            }
        }
    }
    companion object {
        fun start(context: Context, projectId: String, jobId: String) = context.startActivity(
            Intent(context, AgentsActivity::class.java).putExtra("project_id", projectId).putExtra("job_id", jobId))
    }
}
