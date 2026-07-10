package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.ViewGroup
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

    private val textWatcher = object : TextWatcher {
        override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
        override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = Unit
        override fun afterTextChanged(s: Editable?) {
            if (suppressTextWatch || openFilePath == null) {
                return
            }
            val dirty = s?.toString() != loadedContent
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
        binding.editFileContent.addTextChangedListener(textWatcher)
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
        loadDirectory(currentPath)
        binding.drawerLayout.openDrawer(GravityCompat.START)
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
        binding.drawerLayout.closeDrawer(GravityCompat.START)
        binding.textOpenFile.text = entry.path
        binding.textEditorStatus.text = getString(R.string.loading_file)
        binding.editFileContent.isEnabled = false

        lifecycleScope.launch {
            try {
                val content = withContext(Dispatchers.IO) {
                    api.readFile(projectId, entry.path)
                }
                openFilePath = content.path
                loadedContent = content.content
                isWritable = content.writable && !content.truncated
                isTruncated = content.truncated
                isDirty = false

                suppressTextWatch = true
                binding.editFileContent.setText(content.content)
                suppressTextWatch = false
                binding.editFileContent.isEnabled = isWritable
                binding.editFileContent.setSelection(0)
                adapter.setSelectedPath(openFilePath)
                refreshEditorChrome()
            } catch (e: Exception) {
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

    private fun saveCurrentFile() {
        val path = openFilePath ?: return
        if (!isWritable) {
            toast(getString(R.string.file_readonly))
            return
        }
        val content = binding.editFileContent.text?.toString().orEmpty()
        binding.btnSave.isEnabled = false
        lifecycleScope.launch {
            try {
                val message = withContext(Dispatchers.IO) {
                    api.writeFile(projectId, path, content)
                }
                loadedContent = content
                isDirty = false
                refreshEditorChrome()
                toast(message)
            } catch (e: Exception) {
                toast("保存失败: ${e.message}")
                refreshEditorChrome()
            }
        }
    }

    private fun confirmDiscardOrSave(onContinue: () -> Unit) {
        AlertDialog.Builder(this)
            .setTitle(R.string.unsaved_changes_title)
            .setMessage(R.string.unsaved_changes_message)
            .setPositiveButton(R.string.save) { _, _ ->
                val path = openFilePath
                if (path == null || !isWritable) {
                    onContinue()
                    return@setPositiveButton
                }
                lifecycleScope.launch {
                    try {
                        val content = binding.editFileContent.text?.toString().orEmpty()
                        withContext(Dispatchers.IO) {
                            api.writeFile(projectId, path, content)
                        }
                        loadedContent = content
                        isDirty = false
                        refreshEditorChrome()
                        onContinue()
                    } catch (e: Exception) {
                        toast("保存失败: ${e.message}")
                    }
                }
            }
            .setNegativeButton(R.string.discard) { _, _ ->
                isDirty = false
                onContinue()
            }
            .setNeutralButton(R.string.cancel, null)
            .show()
    }

    private fun refreshEditorChrome() {
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
            isWritable -> statusParts.add(getString(R.string.file_editable))
            else -> statusParts.add(getString(R.string.file_readonly))
        }
        if (isDirty) {
            statusParts.add(getString(R.string.file_dirty))
        }
        binding.textEditorStatus.text = statusParts.joinToString(" · ")
        binding.btnSave.isEnabled = isDirty && isWritable
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
        ) {
            val intent = Intent(context, FileBrowserActivity::class.java).apply {
                putExtra(EXTRA_PROJECT_ID, project.id)
                putExtra(EXTRA_PROJECT_NAME, project.name)
                putExtra(EXTRA_SERVER_URL, serverUrl)
                putExtra(EXTRA_API_TOKEN, apiToken)
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
            binding.textFileIcon.text = if (entry.type == "dir") "📁" else "📄"
            binding.textFileName.text = entry.name
            binding.textFilePath.text = entry.path
            binding.root.isSelected = entry.path == selectedPath
            binding.root.alpha = if (entry.path == selectedPath) 1f else 0.92f
            binding.root.setOnClickListener { onClick(entry) }
        }
    }
}
