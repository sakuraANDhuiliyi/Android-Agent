package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ActivityDiffBinding
import com.androidagent.client.databinding.ItemDiffFileBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class DiffActivity : AppCompatActivity() {

    private lateinit var binding: ActivityDiffBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private var projectId: String = ""
    private var files: List<DiffEntry> = emptyList()
    private var checkpoints: List<CheckpointInfo> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityDiffBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.title = getString(R.string.change_details)
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.recyclerDiffFiles.layoutManager = LinearLayoutManager(this)
        binding.btnRestoreCheckpoint.setOnClickListener { confirmRestore() }
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val diff = withContext(Dispatchers.IO) { api.getDiff(projectId) }
                checkpoints = withContext(Dispatchers.IO) { api.listCheckpoints(projectId) }
                files = diff.files
                val first = files.firstOrNull()
                binding.textDiffPath.text = first?.path ?: getString(R.string.view_diff)
                showPatch(first?.patch.orEmpty(), first?.truncated == true)
                binding.recyclerDiffFiles.adapter = DiffFileAdapter(files) { entry ->
                    binding.textDiffPath.text = entry.path
                    showPatch(entry.patch.orEmpty(), entry.truncated)
                }
                val checkpoint = checkpoints.firstOrNull()
                binding.textCheckpoint.text = if (checkpoint == null) {
                    getString(R.string.restore_checkpoint)
                } else {
                    val whenText = UiFormat.relativeTime(this@DiffActivity, checkpoint.createdAt)
                        .ifBlank { getString(R.string.time_just_now) }
                    getString(R.string.checkpoint_auto_save, whenText)
                }
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun showPatch(patch: String, truncated: Boolean) {
        val display = if (truncated || patch.length > 20_000) {
            patch.take(20_000) + "\n\n… " + getString(R.string.large_diff_truncated)
        } else {
            patch.ifBlank { "(无文本 diff)" }
        }
        val stats = DiffRenderer.stats(display)
        binding.textDiffAdded.text = getString(R.string.diff_added, stats.added)
        binding.textDiffDeleted.text = getString(R.string.diff_deleted, stats.deleted)
        binding.textDiffContext.text = getString(R.string.diff_context, stats.context)
        binding.textDiffContent.text = DiffRenderer.spannable(
            display,
            getColor(R.color.diff_add_bg),
            getColor(R.color.diff_del_bg),
            getColor(R.color.diff_add_text),
            getColor(R.color.diff_del_text),
        )
    }

    private fun confirmRestore() {
        val checkpoint = checkpoints.firstOrNull() ?: run {
            toast("没有可用 checkpoint")
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.restore_confirm_title)
            .setMessage(getString(R.string.restore_undo_message) + "\n\n${checkpoint.id}")
            .setPositiveButton(R.string.confirm_restore) { _, _ -> restore(checkpoint.id) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun restore(checkpointId: String) {
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    api.restoreCheckpoint(projectId, checkpointId)
                }
                if (result.conflicts.isNotEmpty()) {
                    AlertDialog.Builder(this@DiffActivity)
                        .setTitle(R.string.restore_checkpoint)
                        .setMessage(getString(R.string.restore_conflicts, result.conflicts.joinToString("\n")))
                        .setPositiveButton(android.R.string.ok, null)
                        .show()
                } else {
                    toast(result.message ?: "已恢复")
                }
                load()
            } catch (e: Exception) {
                if (e is ApiException && e.isConflict) {
                    AlertDialog.Builder(this@DiffActivity)
                        .setTitle(R.string.restore_checkpoint)
                        .setMessage(e.detail.ifBlank { e.message })
                        .setPositiveButton(android.R.string.ok, null)
                        .show()
                } else {
                    toast(userMessage(e))
                }
            }
        }
    }

    private fun userMessage(e: Exception): String = when (e) {
        is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message.orEmpty()
        else -> e.message ?: "错误"
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    private class DiffFileAdapter(
        private val items: List<DiffEntry>,
        private val onClick: (DiffEntry) -> Unit,
    ) : RecyclerView.Adapter<DiffFileAdapter.VH>() {
        class VH(val binding: ItemDiffFileBinding) : RecyclerView.ViewHolder(binding.root)
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
            return VH(ItemDiffFileBinding.inflate(LayoutInflater.from(parent.context), parent, false))
        }
        override fun getItemCount(): Int = items.size
        override fun onBindViewHolder(holder: VH, position: Int) {
            val item = items[position]
            holder.binding.textPath.text = "${item.change}  ${item.path}"
            holder.binding.root.setOnClickListener { onClick(item) }
        }
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, DiffActivity::class.java).putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
