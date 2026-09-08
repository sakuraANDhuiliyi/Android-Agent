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
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ActivityConfigListBinding
import com.androidagent.client.databinding.DialogMemoryEditorBinding
import com.androidagent.client.databinding.ItemMemoryBinding
import com.androidagent.client.databinding.ItemTurnUsageBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Project Memory：Add / Edit / Approve / Archive / Delete + 最近使用记录。 */
class MemoryActivity : AppCompatActivity() {
    private lateinit var binding: ActivityConfigListBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private lateinit var adapter: MemoryAdapter
    private var projectId = ""
    private var status = "active"
    private var memories: List<MemoryInfo> = emptyList()
    private var usage: List<MemoryUsageRow> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityConfigListBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.setTitle(R.string.memory_title)
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = MemoryAdapter(onMore = { memory -> showMemoryMenu(memory) })
        binding.recyclerConfig.layoutManager = LinearLayoutManager(this)
        binding.recyclerConfig.adapter = adapter
        binding.fabConfigAction.visibility = View.VISIBLE
        binding.fabConfigAction.setOnClickListener { showMemoryEditor(null) }
        binding.chipConfigFilter.visibility = View.VISIBLE
        binding.chipConfigFilter.setOnCheckedStateChangeListener { _, checkedIds ->
            status = when (checkedIds.firstOrNull()) {
                R.id.chipFilterCandidate -> "candidate"
                R.id.chipFilterArchived -> "archived"
                else -> "active"
            }
            load()
        }
    }

    override fun onResume() {
        super.onResume()
        load()
    }

    private fun load() {
        lifecycleScope.launch {
            try {
                val result = withContext(Dispatchers.IO) {
                    val list = api.listMemories(projectId, status)
                    val rows = if (status == "active") api.listMemoryUsage(projectId) else emptyList()
                    list to rows
                }
                memories = result.first
                usage = result.second
                adapter.submit(memories, usage)
                binding.textConfigStatus.visibility =
                    if (memories.isEmpty() && usage.isEmpty()) View.VISIBLE else View.GONE
                binding.textConfigStatus.setText(
                    when (status) {
                        "candidate" -> R.string.memory_empty_candidates
                        "archived" -> R.string.memory_empty_archived
                        else -> R.string.memory_empty
                    },
                )
            } catch (e: Exception) {
                binding.textConfigStatus.visibility = View.VISIBLE
                binding.textConfigStatus.text = userMessage(e)
            }
        }
    }

    private fun showMemoryMenu(memory: MemoryInfo) {
        val options = when (memory.status) {
            "candidate" -> arrayOf(
                getString(R.string.memory_approve),
                getString(R.string.memory_reject),
                getString(R.string.memory_edit),
            )
            "active" -> arrayOf(
                getString(R.string.memory_edit),
                getString(R.string.memory_archive),
                getString(R.string.memory_delete),
            )
            else -> arrayOf(
                getString(R.string.memory_restore),
                getString(R.string.memory_delete),
            )
        }
        AlertDialog.Builder(this)
            .setTitle(memory.title)
            .setItems(options) { _, which ->
                when (options[which]) {
                    getString(R.string.memory_approve) -> setStatus(memory, "approve")
                    getString(R.string.memory_reject) -> setStatus(memory, "reject")
                    getString(R.string.memory_archive) -> setStatus(memory, "archive")
                    getString(R.string.memory_restore) -> setStatus(memory, "approve")
                    getString(R.string.memory_edit) -> showMemoryEditor(memory)
                    getString(R.string.memory_delete) -> confirmDelete(memory)
                }
            }
            .show()
    }

    private fun setStatus(memory: MemoryInfo, action: String) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.setMemoryStatus(memory.id, action) }
                toast(getString(R.string.memory_status_updated))
                load()
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun confirmDelete(memory: MemoryInfo) {
        AlertDialog.Builder(this)
            .setTitle(R.string.memory_delete)
            .setMessage(getString(R.string.memory_delete_confirm, memory.title))
            .setPositiveButton(R.string.memory_delete) { _, _ ->
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { api.deleteMemory(memory.id) }
                        toast(getString(R.string.memory_deleted))
                        load()
                    } catch (e: Exception) {
                        toast(userMessage(e))
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun showMemoryEditor(memory: MemoryInfo?) {
        val editor = DialogMemoryEditorBinding.inflate(layoutInflater)
        if (memory != null) {
            editor.editMemoryTitle.setText(memory.title)
            editor.editMemoryContent.setText(memory.content)
        }
        AlertDialog.Builder(this)
            .setTitle(if (memory == null) R.string.memory_add else R.string.memory_edit)
            .setView(editor.root)
            .setPositiveButton(R.string.memory_save) { _, _ ->
                val title = editor.editMemoryTitle.text?.toString()?.trim().orEmpty()
                val content = editor.editMemoryContent.text?.toString()?.trim().orEmpty()
                if (title.isBlank() || content.isBlank()) {
                    toast(getString(R.string.memory_invalid_input))
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) {
                            if (memory == null) {
                                api.createMemory(
                                    projectId,
                                    title,
                                    content,
                                    "preference",
                                    "project",
                                )
                            } else {
                                api.updateMemory(memory.id, title, content, null)
                            }
                        }
                        toast(getString(R.string.memory_saved))
                        load()
                    } catch (e: Exception) {
                        toast(userMessage(e))
                    }
                }
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private inner class MemoryAdapter(
        private val onMore: (MemoryInfo) -> Unit,
    ) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {
        private var items: List<MemoryInfo> = emptyList()
        private var usageRows: List<MemoryUsageRow> = emptyList()

        fun submit(value: List<MemoryInfo>, rows: List<MemoryUsageRow>) {
            items = value
            usageRows = rows
            notifyDataSetChanged()
        }

        override fun getItemViewType(position: Int): Int = if (position < items.size) TYPE_ITEM else TYPE_USAGE

        override fun getItemCount() = items.size + if (usageRows.isEmpty()) 0 else usageRows.size + 1

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder =
            if (viewType == TYPE_ITEM) {
                MemoryVH(
                    ItemMemoryBinding.inflate(LayoutInflater.from(parent.context), parent, false),
                )
            } else {
                UsageVH(
                    ItemTurnUsageBinding.inflate(LayoutInflater.from(parent.context), parent, false),
                )
            }

        override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
            if (holder is MemoryVH) {
                val memory = items[position]
                holder.binding.textMemoryTitle.text = memory.title
                holder.binding.textMemoryMeta.text =
                    "${memory.memoryType} · ${memory.scope}"
                holder.binding.textMemoryContent.text = memory.content
                holder.binding.btnMemoryMore.setOnClickListener { onMore(memory) }
            } else if (holder is UsageVH) {
                val context = holder.itemView.context
                if (position == items.size) {
                    holder.binding.textTurnTitle.setText(R.string.memory_recent_usage)
                    holder.binding.textTurnCost.visibility = View.GONE
                    holder.binding.textTurnMeta.setText(R.string.memory_used_by_turn_hint)
                    holder.binding.root.isClickable = false
                    return
                }
                val row = usageRows[position - items.size - 1]
                val title = memories.firstOrNull { it.id == row.memoryId }?.title
                    ?: row.memoryId.takeLast(8)
                holder.binding.textTurnTitle.text = title
                holder.binding.textTurnCost.visibility = View.GONE
                holder.binding.textTurnMeta.text = buildString {
                    row.reason?.let { append(it) }
                    if (row.createdAt != null) {
                        if (isNotEmpty()) append(" · ")
                        append(UiFormat.relativeTime(context, row.createdAt))
                    }
                }
            }
        }

        inner class MemoryVH(val binding: ItemMemoryBinding) : RecyclerView.ViewHolder(binding.root)
        inner class UsageVH(val binding: ItemTurnUsageBinding) : RecyclerView.ViewHolder(binding.root)
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val TYPE_ITEM = 0
        private const val TYPE_USAGE = 1

        fun start(context: Context, projectId: String) {
            context.startActivity(
                Intent(context, MemoryActivity::class.java).putExtra(EXTRA_PROJECT_ID, projectId),
            )
        }
    }
}
