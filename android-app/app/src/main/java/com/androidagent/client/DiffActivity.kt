package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
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
    private lateinit var adapter: DiffFileAdapter
    private var projectId = ""
    private var files: List<DiffEntry> = emptyList()
    private var checkpoints: List<CheckpointInfo> = emptyList()
    private var filter = ChangeFilter.ALL

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
        binding.toolbar.setNavigationIcon(R.drawable.ic_arrow_back)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = DiffFileAdapter { FileDiffActivity.start(this, projectId, it.path, intent.getStringExtra("turn_id"), intent.getStringExtra("checkpoint_id")) }
        binding.recyclerDiffFiles.layoutManager = LinearLayoutManager(this)
        binding.recyclerDiffFiles.adapter = adapter
        binding.chipFilters.setOnCheckedStateChangeListener { _, checked ->
            filter = when (checked.firstOrNull()) {
                R.id.chipModified -> ChangeFilter.MODIFIED
                R.id.chipAdded -> ChangeFilter.ADDED
                R.id.chipDeleted -> ChangeFilter.DELETED
                else -> ChangeFilter.ALL
            }
            renderList()
        }
        binding.btnExpandAll.setOnClickListener { adapter.setExpanded(true) }
        binding.btnCollapseAll.setOnClickListener { adapter.setExpanded(false) }
        binding.btnRestoreCheckpoint.setOnClickListener { confirmRestore() }
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    api.getDiff(projectId, intent.getStringExtra("turn_id"), intent.getStringExtra("checkpoint_id")) to api.listCheckpoints(projectId)
                }
                files = result.first.files.sortedWith(
                    compareBy<DiffEntry> { changeOrder(it.change) }.thenBy { it.path },
                )
                checkpoints = result.second
                val additions = files.sumOf { it.additions }
                val deletions = files.sumOf { it.deletions }
                binding.textSummary.text = getString(
                    R.string.changes_summary,
                    files.size,
                    additions,
                    deletions,
                )
                renderList()
                val checkpoint = checkpoints.firstOrNull { it.kind == "before_turn" &&
                    (intent.getStringExtra("turn_id") == null || it.turnId == intent.getStringExtra("turn_id")) }
                binding.textCheckpoint.text = if (checkpoint == null) {
                    getString(R.string.restore_checkpoint)
                } else {
                    val time = UiFormat.relativeTime(this@DiffActivity, checkpoint.createdAt)
                        .ifBlank { getString(R.string.time_just_now) }
                    getString(R.string.checkpoint_auto_save, time)
                }
                binding.btnRestoreCheckpoint.isEnabled = checkpoint != null
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun renderList() {
        val visible = DiffReview.filter(files, filter)
        adapter.submit(visible)
        binding.textEmpty.visibility = if (visible.isEmpty()) View.VISIBLE else View.GONE
    }

    private fun changeOrder(change: String) = when (DiffReview.normalizeChange(change)) {
        "modified" -> 0
        "added" -> 1
        else -> 2
    }

    private fun confirmRestore() {
        val checkpoint = checkpoints.firstOrNull { it.kind == "before_turn" &&
            (intent.getStringExtra("turn_id") == null || it.turnId == intent.getStringExtra("turn_id")) } ?: return
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
                val result = withContext(Dispatchers.IO) { api.restoreCheckpoint(projectId, checkpointId) }
                if (result.conflicts.isNotEmpty()) {
                    AlertDialog.Builder(this@DiffActivity)
                        .setTitle(R.string.restore_checkpoint)
                        .setMessage(getString(R.string.restore_conflicts, result.conflicts.joinToString("\n")))
                        .setPositiveButton(android.R.string.ok, null)
                        .show()
                } else {
                    toast(result.message ?: getString(R.string.hunk_reverted))
                }
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private class DiffFileAdapter(
        private val onClick: (DiffEntry) -> Unit,
    ) : RecyclerView.Adapter<DiffFileAdapter.VH>() {
        private var items: List<DiffEntry> = emptyList()
        private var expanded = false

        class VH(val binding: ItemDiffFileBinding) : RecyclerView.ViewHolder(binding.root)

        fun submit(value: List<DiffEntry>) {
            items = value
            notifyDataSetChanged()
        }

        fun setExpanded(value: Boolean) {
            expanded = value
            notifyDataSetChanged()
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = VH(
            ItemDiffFileBinding.inflate(LayoutInflater.from(parent.context), parent, false),
        )

        override fun getItemCount() = items.size

        override fun onBindViewHolder(holder: VH, position: Int) {
            val item = items[position]
            val normalized = DiffReview.normalizeChange(item.change)
            val previous = items.getOrNull(position - 1)?.let { DiffReview.normalizeChange(it.change) }
            holder.binding.textSection.visibility = if (normalized != previous) View.VISIBLE else View.GONE
            holder.binding.textSection.text = when (normalized) {
                "added" -> holder.itemView.context.getString(R.string.filter_added)
                "deleted" -> holder.itemView.context.getString(R.string.filter_deleted)
                else -> holder.itemView.context.getString(R.string.filter_modified)
            }
            val (badge, color) = when (normalized) {
                "added" -> "A" to R.color.status_success
                "deleted" -> "D" to R.color.status_failed
                else -> "M" to R.color.status_warning
            }
            holder.binding.textChangeBadge.text = badge
            holder.binding.textChangeBadge.setTextColor(ContextCompat.getColor(holder.itemView.context, color))
            holder.binding.textPath.text = item.path
            holder.binding.textLineStats.text = "+${item.additions} −${item.deletions}"
            holder.binding.textPreview.visibility = if (expanded && !item.patch.isNullOrBlank()) View.VISIBLE else View.GONE
            holder.binding.textPreview.text = item.patch.orEmpty()
                .lineSequence()
                .dropWhile { !it.startsWith("@@") }
                .take(12)
                .joinToString("\n")
            holder.binding.rowFile.setOnClickListener { onClick(item) }
        }
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        fun start(context: Context, projectId: String, turnId: String? = null, checkpointId: String? = null) {
            context.startActivity(Intent(context, DiffActivity::class.java).putExtra(EXTRA_PROJECT_ID, projectId)
                .putExtra("turn_id", turnId).putExtra("checkpoint_id", checkpointId))
        }
    }
}
