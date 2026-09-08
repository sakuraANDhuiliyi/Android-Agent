package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.PopupMenu
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ActivityFileDiffBinding
import com.androidagent.client.databinding.ItemDiffHunkBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class FileDiffActivity : AppCompatActivity() {
    private lateinit var binding: ActivityFileDiffBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var api: AgentApi
    private var projectId = ""
    private var initialPath = ""
    private var files: List<DiffEntry> = emptyList()
    private var currentIndex = 0
    private val reviewDecisions = mutableMapOf<String, Boolean>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFileDiffBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        initialPath = intent.getStringExtra(EXTRA_PATH).orEmpty()
        if (projectId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.toolbar.setNavigationIcon(R.drawable.ic_arrow_back)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.recyclerHunks.layoutManager = LinearLayoutManager(this)
        binding.btnPrevious.setOnClickListener { showFile(currentIndex - 1) }
        binding.btnNext.setOnClickListener { showFile(currentIndex + 1) }
        load()
    }

    private fun load(preferredPath: String = initialPath) {
        lifecycleScope.launch {
            try {
                files = withContext(Dispatchers.IO) { api.getDiff(projectId, intent.getStringExtra("turn_id"), intent.getStringExtra("checkpoint_id")).files }
                currentIndex = files.indexOfFirst { it.path == preferredPath }.takeIf { it >= 0 } ?: 0
                showFile(currentIndex)
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun showFile(index: Int) {
        if (files.isEmpty()) {
            binding.textPath.setText(R.string.no_changes)
            binding.textPosition.text = "0 / 0"
            binding.recyclerHunks.adapter = HunkAdapter(emptyList(), emptyMap(), ::showActions)
            return
        }
        currentIndex = index.coerceIn(files.indices)
        val file = files[currentIndex]
        binding.toolbar.title = file.path.substringAfterLast('/')
        binding.textPath.text = file.path
        binding.textStats.text = "+${file.additions}  −${file.deletions}"
        binding.textPosition.text = "${currentIndex + 1} / ${files.size}"
        binding.btnPrevious.isEnabled = currentIndex > 0
        binding.btnNext.isEnabled = currentIndex < files.lastIndex
        val hunks = DiffReview.hunks(file.patch.orEmpty())
        binding.textEmpty.visibility = if (hunks.isEmpty()) View.VISIBLE else View.GONE
        binding.recyclerHunks.adapter = HunkAdapter(hunks, reviewDecisions, ::showActions)
    }

    private fun showActions(anchor: View, hunk: DiffHunk) {
        PopupMenu(this, anchor).apply {
            menu.add(0, ACTION_ACCEPT, 0, R.string.accept_change)
            menu.add(0, ACTION_REJECT, 1, R.string.reject_change)
            menu.add(0, ACTION_EXPLAIN, 2, R.string.explain_change)
            menu.add(0, ACTION_MODIFY, 3, R.string.ask_agent_modify)
            if (!intent.hasExtra("turn_id") && !intent.hasExtra("checkpoint_id")) menu.add(0, ACTION_REVERT, 4, R.string.revert_hunk)
            menu.add(0, ACTION_COPY, 5, R.string.copy)
            setOnMenuItemClickListener { item ->
                when (item.itemId) {
                    ACTION_ACCEPT -> {
                        reviewDecisions[hunkKey(hunk)] = true
                        showFile(currentIndex)
                    }
                    ACTION_REJECT -> {
                        reviewDecisions[hunkKey(hunk)] = false
                        showFile(currentIndex)
                    }
                    ACTION_EXPLAIN -> openAgent(hunk, modify = false)
                    ACTION_MODIFY -> openAgent(hunk, modify = true)
                    ACTION_REVERT -> confirmRevert(hunk)
                    ACTION_COPY -> copy(hunk.patch)
                }
                true
            }
            show()
        }
    }

    private fun openAgent(hunk: DiffHunk, modify: Boolean) {
        val file = files.getOrNull(currentIndex) ?: return
        val prompt = DiffReview.reviewPrompt(file.path, hunk, modify)
        lifecycleScope.launch {
            try {
                val conversation = withContext(Dispatchers.IO) {
                    api.listConversations(projectId).maxByOrNull { it.updatedAt ?: 0.0 }
                        ?: api.createConversation(projectId, getString(R.string.code_review))
                }
                ConversationActivity.start(
                    this@FileDiffActivity,
                    projectId,
                    conversation.id,
                    conversation.title,
                    draft = prompt,
                )
            } catch (e: Exception) {
                toast(userMessage(e))
            }
        }
    }

    private fun confirmRevert(hunk: DiffHunk) {
        val file = files.getOrNull(currentIndex) ?: return
        AlertDialog.Builder(this)
            .setTitle(R.string.revert_hunk)
            .setMessage(getString(R.string.revert_hunk_confirm, file.path))
            .setPositiveButton(R.string.revert) { _, _ -> revert(file, hunk) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun revert(file: DiffEntry, hunk: DiffHunk) {
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { api.revertHunk(projectId, file.path, hunk.patch) }
                toast(getString(R.string.hunk_reverted))
                load(file.path)
            } catch (e: Exception) {
                toast(if (e is ApiException && e.isConflict) getString(R.string.hunk_conflict) else userMessage(e))
            }
        }
    }

    private fun copy(text: String) {
        val clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("diff", text))
        toast(getString(R.string.copied))
    }

    private fun hunkKey(hunk: DiffHunk) = "${files.getOrNull(currentIndex)?.path}:${hunk.header}"

    private fun userMessage(e: Exception) = when (e) {
        is ApiException -> e.detail.ifBlank { e.message.orEmpty() }
        else -> e.message ?: getString(R.string.error)
    }

    private fun toast(message: String) = Toast.makeText(this, message, Toast.LENGTH_SHORT).show()

    private inner class HunkAdapter(
        private val items: List<DiffHunk>,
        private val decisions: Map<String, Boolean>,
        private val onActions: (View, DiffHunk) -> Unit,
    ) : RecyclerView.Adapter<HunkAdapter.VH>() {
        inner class VH(val binding: ItemDiffHunkBinding) : RecyclerView.ViewHolder(binding.root)
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) = VH(
            ItemDiffHunkBinding.inflate(LayoutInflater.from(parent.context), parent, false),
        )
        override fun getItemCount() = items.size
        override fun onBindViewHolder(holder: VH, position: Int) {
            val hunk = items[position]
            holder.binding.textHunkHeader.text = hunk.header
            holder.binding.textHunkStats.text = "+${hunk.added}  −${hunk.deleted}"
            holder.binding.textHunkContent.text = DiffRenderer.spannable(
                hunk.body,
                getColor(R.color.diff_add_bg),
                getColor(R.color.diff_del_bg),
                getColor(R.color.diff_add_text),
                getColor(R.color.diff_del_text),
            )
            val decision = decisions[hunkKey(hunk)]
            holder.binding.textReviewed.visibility = if (decision == null) View.GONE else View.VISIBLE
            holder.binding.textReviewed.text = getString(
                if (decision == true) R.string.change_accepted else R.string.change_rejected,
            )
            holder.binding.textReviewed.setTextColor(
                getColor(if (decision == false) R.color.status_failed else R.color.status_success),
            )
            holder.binding.btnHunkActions.setOnClickListener { onActions(it, hunk) }
            holder.binding.root.setOnLongClickListener { onActions(it, hunk); true }
        }
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val EXTRA_PATH = "path"
        private const val ACTION_ACCEPT = 1
        private const val ACTION_REJECT = 2
        private const val ACTION_EXPLAIN = 3
        private const val ACTION_MODIFY = 4
        private const val ACTION_REVERT = 5
        private const val ACTION_COPY = 6

        fun start(context: Context, projectId: String, path: String, turnId: String? = null, checkpointId: String? = null) {
            context.startActivity(
                Intent(context, FileDiffActivity::class.java)
                    .putExtra(EXTRA_PROJECT_ID, projectId)
                    .putExtra(EXTRA_PATH, path).apply {
                        if (turnId != null) putExtra("turn_id", turnId)
                        if (checkpointId != null) putExtra("checkpoint_id", checkpointId)
                    },
            )
        }
    }
}
