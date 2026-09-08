package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.ViewGroup
import android.view.ActionMode
import android.view.Menu
import android.view.MenuItem
import android.widget.PopupMenu
import androidx.core.widget.doAfterTextChanged
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.GravityCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.databinding.ActivityFileBrowserBinding
import com.androidagent.client.databinding.ItemFileEntryBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class FileBrowserActivity : AppCompatActivity() {

    private lateinit var binding: ActivityFileBrowserBinding
    private lateinit var api: AgentApi
    private lateinit var adapter: FileEntryAdapter

    private var projectId: String = ""
    private var projectName: String = ""
    private var currentPath: String = "."

    private var openFilePath: String? = null
    private var loadedContent: String = ""
    private var isDirty: Boolean = false
    private var isWritable: Boolean = false
    private var isTruncated: Boolean = false
    private var suppressTextWatch: Boolean = false
    private var revision: String? = null
    private var editMode = false
    private var loadingFile = false
    private val history = EditHistory()
    private val openFiles = linkedMapOf<String, FileEntry>()
    private val expanded = mutableSetOf("app", "app/manifests", "app/kotlin", "app/res")
    private var allFiles = emptyList<FileEntry>()
    private var fileKind = "all"
    private var modifiedOnly = false
    private var openOnly = false
    private var searchJob: Job? = null
    private var fileLoadVersion = 0
    private var saving = false

    private val textWatcher = object : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
        override fun afterTextChanged(s: Editable?) {
            if (suppressTextWatch || openFilePath == null) {
                return
            }
            val dirty = s?.toString() != loadedContent
            history.record(s?.toString().orEmpty(), android.os.SystemClock.uptimeMillis())
            binding.btnUndo.isEnabled = history.canUndo
            binding.btnRedo.isEnabled = history.canRedo
            if (dirty != isDirty) {
                isDirty = dirty
                refreshEditorChrome()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFileBrowserBinding.inflate(layoutInflater)
        setContentView(binding.root)

        projectId = intent.getStringExtra(EXTRA_PROJECT_ID).orEmpty()
        projectName = intent.getStringExtra(EXTRA_PROJECT_NAME).orEmpty()
        val serverUrl = intent.getStringExtra(EXTRA_SERVER_URL).orEmpty()
        val apiToken = intent.getStringExtra(EXTRA_API_TOKEN).orEmpty()

        if (projectId.isBlank() || serverUrl.isBlank()) {
            toast("缺少项目或服务器信息")
            finish()
            return
        }

        api = AgentApi(serverUrl, apiToken)
        adapter = FileEntryAdapter { entry -> onEntryClick(entry) }

        setSupportActionBar(binding.toolbar)
        binding.toolbar.setNavigationOnClickListener { handleBack() }
        binding.textProjectTitle.text = getString(R.string.browse_project_title, projectName)
        binding.recyclerFiles.layoutManager = LinearLayoutManager(this)
        binding.recyclerFiles.adapter = adapter

        binding.btnOpenFiles.setOnClickListener {
            binding.drawerLayout.openDrawer(GravityCompat.START)
        }
        binding.btnParentDir.setOnClickListener { navigateUp() }
        binding.btnSave.setOnClickListener { saveCurrentFile() }
        binding.btnEdit.setOnClickListener { editMode = !editMode; refreshEditorChrome() }
        binding.btnUndo.setOnClickListener { applyHistory(history.undo()) }
        binding.btnRedo.setOnClickListener { applyHistory(history.redo()) }
        binding.btnCodeAgent.setOnClickListener { showAgentActions() }
        binding.btnOpenTabs.setOnClickListener {
            val entries = openFiles.values.toList()
            AlertDialog.Builder(this).setTitle(R.string.explorer_open_files)
                .setItems(entries.map { it.path }.toTypedArray()) { _, index -> openFile(entries[index]) }
                .setNegativeButton(R.string.cancel, null).show()
        }
        binding.btnFileFilter.setOnClickListener { showFilters() }
        binding.editSearchFiles.doAfterTextChanged { refreshFiles() }
        binding.editFileContent.customSelectionActionModeCallback = object : ActionMode.Callback {
            override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
                listOf("Explain", "Fix", "Refactor", "Add tests", "Ask Agent").forEachIndexed { index, title ->
                    menu.add(0, 200 + index, index, title).setShowAsAction(MenuItem.SHOW_AS_ACTION_NEVER)
                }
                return true
            }
            override fun onPrepareActionMode(mode: ActionMode, menu: Menu) = false
            override fun onDestroyActionMode(mode: ActionMode) = Unit
            override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
                if (item.itemId !in 200..204) return false
                sendSelectionToAgent(item.title.toString()); mode.finish(); return true
            }
        }
        binding.editFileContent.addTextChangedListener(textWatcher)
        binding.editFileContent.onRangeSelected = { _, _ ->
            binding.editFileContent.post { renderSelectionActions(binding.editFileContent.selectionStart, binding.editFileContent.selectionEnd) }
        }
        binding.editFileContent.isEnabled = false

        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    handleBack()
                }
            },
        )

        refreshEditorChrome()
        savedInstanceState?.getStringArrayList("open_files")?.forEach { path -> openFiles[path] = FileEntry(path.substringAfterLast('/'), path, "file") }
        savedInstanceState?.getStringArrayList("expanded")?.let { expanded.clear(); expanded.addAll(it) }
        refreshFiles()
        val initialPath = savedInstanceState?.getString("open_path") ?: intent.getStringExtra("file_path")
        if (initialPath != null) {
            restoreDraft = savedInstanceState
            actuallyOpenFile(FileEntry(initialPath.substringAfterLast('/'), initialPath, "file"))
        } else binding.drawerLayout.openDrawer(GravityCompat.START)
    }

    private fun handleBack() {
        when {
            binding.drawerLayout.isDrawerOpen(GravityCompat.START) -> {
                binding.drawerLayout.closeDrawer(GravityCompat.START)
            }
            isDirty -> confirmDiscardOrSave { finish() }
            else -> finish()
        }
    }

    private fun onEntryClick(entry: FileEntry) {
        if (entry.type == "group") {
            if (!expanded.add(entry.path)) expanded.remove(entry.path)
            renderFiles()
            return
        }
        if (entry.type == "dir") {
            loadDirectory(entry.path)
            return
        }
        openFile(entry)
    }

    private fun navigateUp() {
        if (currentPath == "." || currentPath.isBlank()) {
            return
        }
        val slash = currentPath.lastIndexOf('/')
        val parent = if (slash <= 0) "." else currentPath.substring(0, slash)
        loadDirectory(parent)
    }

    private fun loadDirectory(path: String) {
        currentPath = path
        binding.textCurrentPath.text = if (path == ".") "/" else path
        binding.btnParentDir.isEnabled = path != "."

        lifecycleScope.launch {
            try {
                val (_, entries) = withContext(Dispatchers.IO) {
                    api.listFiles(projectId, path)
                }
                adapter.submitList(entries, openFilePath)
            } catch (e: Exception) {
                toast("加载目录失败: ${e.message}")
            }
        }
    }

    private fun openFile(entry: FileEntry) {
        if (isDirty) {
            confirmDiscardOrSave { actuallyOpenFile(entry) }
            return
        }
        actuallyOpenFile(entry)
    }

    private fun actuallyOpenFile(entry: FileEntry) {
        if (saving) return
        val version = ++fileLoadVersion
        loadingFile = true
        refreshEditorChrome()
        binding.drawerLayout.closeDrawer(GravityCompat.START)
        binding.textOpenFile.text = entry.path
        binding.textEditorStatus.text = getString(R.string.loading_file)
        binding.editFileContent.isEnabled = false

        lifecycleScope.launch {
            try {
                val content = withContext(Dispatchers.IO) {
                    api.readFile(projectId, entry.path)
                }
                if (version != fileLoadVersion) return@launch
                openFilePath = content.path
                openFiles[content.path] = entry
                loadedContent = content.content
                revision = content.revision
                isWritable = content.writable && !content.truncated
                isTruncated = content.truncated
                isDirty = false

                suppressTextWatch = true
                binding.editFileContent.setText(content.content)
                suppressTextWatch = false
                history.reset(content.content)
                editMode = false
                loadingFile = false
                binding.editFileContent.isEnabled = true
                binding.editFileContent.setTextIsSelectable(false)
                binding.editFileContent.setTextIsSelectable(true)
                binding.editFileContent.setSelection(0)
                restoreDraft?.takeIf { it.getString("open_path") == content.path }?.let { saved ->
                    loadedContent = saved.getString("loaded", content.content)
                    revision = saved.getString("revision") ?: content.revision
                    history.reset(loadedContent)
                    val draft = saved.getString("buffer", loadedContent)
                    history.record(draft, android.os.SystemClock.uptimeMillis())
                    applyHistory(draft)
                    editMode = saved.getBoolean("edit_mode")
                }
                val line = intent.getIntExtra("file_line", 1).coerceAtLeast(1)
                intent.removeExtra("file_line")
                val offset = binding.editFileContent.text.toString().lineSequence().take(line - 1).sumOf { it.length + 1 }.coerceAtMost(binding.editFileContent.length())
                binding.editFileContent.setSelection(offset)
                if (line > 1) {
                    val end = binding.editFileContent.text.toString().indexOf('\n', offset).let { if (it < 0) binding.editFileContent.length() else it }
                    binding.editFileContent.setSelection(offset, end)
                }
                restoreDraft?.let { saved ->
                    binding.editFileContent.setSelection(saved.getInt("selection_start").coerceIn(0, binding.editFileContent.length()), saved.getInt("selection_end").coerceIn(0, binding.editFileContent.length()))
                }
                restoreDraft = null
                binding.editFileContent.post { binding.editFileContent.layout?.let { layout ->
                    binding.scrollEditor.smoothScrollTo(0, layout.getLineTop(layout.getLineForOffset(offset)))
                } }
                adapter.setSelectedPath(openFilePath)
                refreshEditorChrome()
            } catch (e: Exception) {
                if (version != fileLoadVersion) return@launch
                loadingFile = false
                openFilePath = null
                loadedContent = ""
                isDirty = false
                isWritable = false
                suppressTextWatch = true
                binding.editFileContent.setText("")
                suppressTextWatch = false
                binding.editFileContent.isEnabled = false
                binding.textEditorStatus.text = getString(R.string.read_failed, e.message.orEmpty())
                refreshEditorChrome()
            }
        }
    }

    private fun saveCurrentFile(onSuccess: (() -> Unit)? = null) {
        val path = openFilePath ?: return
        if (saving || loadingFile) return
        if (!isWritable) {
            toast(getString(R.string.file_readonly))
            return
        }
        val content = binding.editFileContent.text?.toString().orEmpty()
        saving = true
        refreshEditorChrome()
        binding.btnSave.isEnabled = false
        lifecycleScope.launch {
            try {
                val message = withContext(Dispatchers.IO) {
                    api.writeFile(projectId, path, content, revision)
                }
                loadedContent = content
                revision = java.security.MessageDigest.getInstance("SHA-256").digest(content.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
                isDirty = binding.editFileContent.text?.toString() != content
                saving = false
                refreshEditorChrome()
                toast(message)
                refreshFiles()
                if (!isDirty) onSuccess?.invoke()
            } catch (e: Exception) {
                saving = false
                toast("保存失败: ${e.message}")
                refreshEditorChrome()
            }
        }
    }

    private fun confirmDiscardOrSave(onContinue: () -> Unit) {
        if (saving) return
        AlertDialog.Builder(this)
            .setTitle(R.string.unsaved_changes_title)
            .setMessage(R.string.unsaved_changes_message)
            .setPositiveButton(R.string.save) { _, _ -> saveCurrentFile(onContinue) }
            .setNegativeButton(R.string.discard) { _, _ -> isDirty = false; onContinue() }
            .setNeutralButton(R.string.cancel, null).show()
    }

    private var restoreDraft: Bundle? = null

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("open_path", openFilePath)
        outState.putString("buffer", binding.editFileContent.text.toString())
        outState.putString("loaded", loadedContent)
        outState.putString("revision", revision)
        outState.putBoolean("edit_mode", editMode)
        outState.putStringArrayList("open_files", ArrayList(openFiles.keys))
        outState.putStringArrayList("expanded", ArrayList(expanded))
        outState.putInt("selection_start", binding.editFileContent.selectionStart)
        outState.putInt("selection_end", binding.editFileContent.selectionEnd)
    }

    private fun applyHistory(text: String) {
        suppressTextWatch = true
        binding.editFileContent.setText(text)
        binding.editFileContent.setSelection(text.length)
        suppressTextWatch = false
        isDirty = text != loadedContent
        refreshEditorChrome()
    }

    private fun showFilters() {
        PopupMenu(this, binding.btnFileFilter).apply {
            menu.add(0, 1, 0, "All files")
            menu.add(0, 2, 1, "Code · Kotlin / Java")
            menu.add(0, 3, 2, "Resources")
            menu.add(0, 4, 3, "Modified only").apply { isCheckable = true; isChecked = modifiedOnly }
            menu.add(0, 5, 4, getString(R.string.explorer_open_files)).apply { isCheckable = true; isChecked = openOnly }
            setOnMenuItemClickListener {
                when (it.itemId) {
                    1 -> { fileKind = "all"; modifiedOnly = false; openOnly = false }
                    2 -> fileKind = "code"
                    3 -> fileKind = "resources"
                    4 -> modifiedOnly = !modifiedOnly
                    5 -> openOnly = !openOnly
                }
                refreshFiles(); true
            }
        }.show()
    }

    private fun refreshFiles() {
        searchJob?.cancel()
        val query = binding.editSearchFiles.text.toString()
        searchJob = lifecycleScope.launch {
            delay(180)
            try {
                val (files, truncated) = withContext(Dispatchers.IO) { api.searchFiles(projectId, query, fileKind, modifiedOnly) }
                allFiles = files
                binding.textCurrentPath.text = if (truncated) getString(R.string.explorer_more_results) else "${files.size} files"
                binding.btnFileFilter.text = listOfNotNull(fileKind, if (modifiedOnly) "Modified" else null, if (openOnly) "Open" else null).joinToString(" · ")
                binding.btnParentDir.text = getString(R.string.explorer_collapse)
                binding.btnParentDir.isEnabled = true
                binding.btnParentDir.setOnClickListener { expanded.clear(); renderFiles() }
                renderFiles()
            } catch (e: kotlinx.coroutines.CancellationException) { throw e }
            catch (e: Exception) { binding.textCurrentPath.text = e.message; toast(e.message.orEmpty()) }
        }
    }

    private fun renderFiles() {
        val files = allFiles.filter { !openOnly || openFiles.containsKey(it.path) }
        adapter.submitList(
            if (binding.editSearchFiles.text.isNullOrBlank() && !modifiedOnly && !openOnly) CodeExplorer.tree(files, expanded) else files,
            openFilePath,
        )
    }

    private fun showAgentActions() {
        val actions = arrayOf("Copy", "Explain", "Fix", "Refactor", "Add tests", "Ask Agent")
        AlertDialog.Builder(this).setTitle(R.string.explorer_code_actions)
            .setItems(actions) { _, index ->
                if (index == 0) {
                    val editor = binding.editFileContent
                    val text = editor.text.toString()
                    val start = minOf(editor.selectionStart, editor.selectionEnd).coerceAtLeast(0)
                    val end = maxOf(editor.selectionStart, editor.selectionEnd).coerceAtLeast(0)
                    val value = if (end > start) text.substring(start, end) else text
                    (getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager).setPrimaryClip(android.content.ClipData.newPlainText(openFilePath, value))
                    toast(getString(R.string.copied))
                } else sendSelectionToAgent(actions[index])
            }
            .setNegativeButton(R.string.cancel, null).show()
    }

    private fun renderSelectionActions(start: Int, end: Int) {
        val active = !loadingFile && openFilePath != null && start >= 0 && end >= 0 && start != end
        binding.selectionActions.visibility = if (active) android.view.View.VISIBLE else android.view.View.GONE
        if (!active) return
        val text = binding.editFileContent.text.toString()
        if (maxOf(start, end) > text.length) return
        val selection = CodeExplorer.selectedContext(openFilePath!!, text, start, end)
        binding.selectionMenu.removeAllViews()
        binding.selectionMenu.addView(android.widget.TextView(this).apply { this.text = "Ln ${selection.lineStart}–${selection.lineEnd}"; setPadding(16, 0, 16, 0) })
        listOf("Copy", "Explain", "Fix", "Refactor", "Add tests", "Ask Agent").forEach { action ->
            binding.selectionMenu.addView(com.google.android.material.button.MaterialButton(this, null, com.google.android.material.R.attr.borderlessButtonStyle).apply {
                this.text = action
                setOnClickListener {
                    if (action == "Copy") {
                        (getSystemService(CLIPBOARD_SERVICE) as android.content.ClipboardManager).setPrimaryClip(android.content.ClipData.newPlainText(selection.label, selection.text))
                        toast(getString(R.string.copied))
                    } else sendSelectionToAgent(action)
                }
            })
        }
    }

    private fun sendSelectionToAgent(action: String) {
        val path = openFilePath ?: return
        val editor = binding.editFileContent
        val text = editor.text.toString()
        val selected = editor.selectionStart >= 0 && editor.selectionEnd >= 0 && editor.selectionEnd != editor.selectionStart
        val item = if (selected) CodeExplorer.selectedContext(path, text, editor.selectionStart, editor.selectionEnd)
            else if (isDirty) CodeExplorer.selectedContext(path, text, 0, text.length)
            else ContextAttachment("file", path.substringAfterLast('/'), path = path)
        if ((item.text?.length ?: 0) > 24_000) { toast(getString(R.string.explorer_selection_large)); return }
        val prompt = "$action: ${item.label}" + if (isDirty) "\n以下选区包含尚未保存的本地修改；请以附带选区为准，保留其他改动。" else ""
        lifecycleScope.launch {
            binding.btnCodeAgent.isEnabled = false
            try {
                val conversation = withContext(Dispatchers.IO) { api.createConversation(projectId, "$action · ${item.label}") }
                ConversationActivity.start(this@FileBrowserActivity, projectId, conversation.id, conversation.title, draft = prompt, contexts = listOf(item))
            } catch (e: Exception) { toast(e.message.orEmpty()) }
            finally { binding.btnCodeAgent.isEnabled = true }
        }
    }

    private fun refreshEditorChrome() {
        binding.btnEdit.isEnabled = isWritable && !loadingFile && !saving
        binding.btnEdit.text = getString(if (editMode) R.string.explorer_view else R.string.explorer_edit)
        binding.btnUndo.isEnabled = history.canUndo && editMode && !saving && !loadingFile
        binding.btnRedo.isEnabled = history.canRedo && editMode && !saving && !loadingFile
        binding.btnCodeAgent.isEnabled = openFilePath != null && !loadingFile
        binding.editFileContent.keyListener = if (editMode && isWritable && !saving && !loadingFile) android.text.method.TextKeyListener.getInstance() else null
        binding.editFileContent.setTextIsSelectable(true)
        if (editMode) binding.editFileContent.setRawInputType(android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE or android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS)
        val path = openFilePath
        if (path == null) {
            binding.textOpenFile.text = getString(R.string.no_file_open)
            binding.textEditorStatus.text = getString(R.string.select_file_hint)
            binding.btnSave.isEnabled = false
            binding.toolbar.title = getString(R.string.workspace_title)
            return
        }

        val name = path.substringAfterLast('/')
        binding.toolbar.title = if (isDirty) "● $name" else name
        binding.textOpenFile.text = path

        val statusParts = mutableListOf<String>()
        when {
            isTruncated -> statusParts.add(getString(R.string.file_truncated_readonly))
            isWritable -> statusParts.add(getString(if (editMode) R.string.file_editable else R.string.explorer_selection_hint))
            else -> statusParts.add(getString(R.string.file_readonly))
        }
        if (isDirty) {
            statusParts.add(getString(R.string.file_dirty))
        }
        binding.textEditorStatus.text = statusParts.joinToString(" · ")
        binding.btnSave.isEnabled = isDirty && isWritable && !saving && !loadingFile
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    companion object {
        private const val EXTRA_PROJECT_ID = "project_id"
        private const val EXTRA_PROJECT_NAME = "project_name"
        private const val EXTRA_SERVER_URL = "server_url"
        private const val EXTRA_API_TOKEN = "api_token"

        fun start(
            context: Context,
            project: ProjectInfo,
            serverUrl: String,
            apiToken: String,
            filePath: String? = null,
            line: Int = 1,
        ) {
            val intent = Intent(context, FileBrowserActivity::class.java).apply {
                putExtra(EXTRA_PROJECT_ID, project.id)
                putExtra(EXTRA_PROJECT_NAME, project.name)
                putExtra(EXTRA_SERVER_URL, serverUrl)
                putExtra(EXTRA_API_TOKEN, apiToken)
                putExtra("file_path", filePath)
                putExtra("file_line", line)
            }
            context.startActivity(intent)
        }
    }
}

private class FileEntryAdapter(
    private val onClick: (FileEntry) -> Unit,
) : RecyclerView.Adapter<FileEntryAdapter.ViewHolder>() {

    private val items = mutableListOf<FileEntry>()
    private var selectedPath: String? = null

    fun submitList(entries: List<FileEntry>, selected: String? = selectedPath) {
        items.clear()
        items.addAll(entries)
        selectedPath = selected
        notifyDataSetChanged()
    }

    fun setSelectedPath(path: String?) {
        selectedPath = path
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemFileEntryBinding.inflate(
            LayoutInflater.from(parent.context),
            parent,
            false,
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount(): Int = items.size

    inner class ViewHolder(
        private val binding: ItemFileEntryBinding,
    ) : RecyclerView.ViewHolder(binding.root) {

        fun bind(entry: FileEntry) {
            binding.textFileIcon.text = if (entry.type in setOf("dir", "group")) "📁" else "📄"
            binding.textFileName.text = entry.name
            binding.textFilePath.text = entry.path
            binding.root.isSelected = entry.path == selectedPath
            binding.root.alpha = if (entry.path == selectedPath) 1f else 0.92f
            binding.root.setOnClickListener { onClick(entry) }
        }
    }
}
