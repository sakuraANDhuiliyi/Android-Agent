package com.androidagent.client

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import com.androidagent.client.databinding.ActivityMainBinding
import com.androidagent.client.databinding.DialogCreateProjectBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext
import org.json.JSONObject
import java.io.File

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: AgentPrefs
    private lateinit var projectAdapter: ProjectAdapter

    private var api: AgentApi? = null
    private var modelOptions: List<ModelOption> = emptyList()
    private var selectedModelOption: ModelOption? = null
    private var projects: List<ProjectInfo> = emptyList()
    private var selectedProject: ProjectInfo? = null
    private var pollJob: Job? = null
    private var downloadedApk: File? = null

    private val installPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) {
        installDownloadedApk()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        prefs = AgentPrefs(this)
        projectAdapter = ProjectAdapter { project ->
            selectProject(project)
        }

        binding.editServerUrl.setText(prefs.serverUrl)
        binding.editUserId.setText(prefs.userId)
        binding.editApiToken.setText(prefs.apiToken)
        binding.recyclerProjects.layoutManager = LinearLayoutManager(this)
        binding.recyclerProjects.adapter = projectAdapter

        binding.btnConnect.setOnClickListener { connectServer() }
        binding.btnRegister.setOnClickListener { confirmRegisterUser() }
        binding.btnRefreshProjects.setOnClickListener { refreshProjects() }
        binding.btnNewProject.setOnClickListener { showCreateProjectDialog() }
        binding.btnBrowseFiles.setOnClickListener { browseFiles() }
        binding.btnSend.setOnClickListener { sendPrompt() }
        binding.btnDownloadApk.setOnClickListener { downloadApk() }
        binding.btnInstallApk.setOnClickListener { requestInstallPermissionIfNeeded() }

        binding.dropdownModel.setOnItemClickListener { _, _, position, _ ->
            if (position in modelOptions.indices) {
                selectModelOption(modelOptions[position])
            }
        }

        appendLog("填写电脑 IP 地址后点击「连接」")
    }

    private fun connectServer() {
        val serverUrl = binding.editServerUrl.text?.toString()?.trim().orEmpty()
        if (serverUrl.isBlank()) {
            toast("请填写服务器地址")
            return
        }

        prefs.serverUrl = serverUrl
        prefs.apiToken = binding.editApiToken.text?.toString()?.trim().orEmpty()
        if (prefs.apiToken.isBlank()) {
            toast("请先注册用户")
            return
        }
        api = AgentApi(serverUrl, prefs.apiToken)

        setBusy(true)
        lifecycleScope.launch {
            try {
                val health = withContext(Dispatchers.IO) { api!!.health() }
                if (health.userId.isNotBlank()) {
                    prefs.userId = health.userId
                    binding.editUserId.setText(health.userId)
                }
                binding.textStatus.text = getString(R.string.status_connected)
                appendLog(
                    "已连接用户: ${health.userId}\n" +
                        "模型: ${health.provider}/${health.model}\n" +
                        "API Key: ${if (health.apiKeyConfigured) "已配置" else "未配置"}",
                )
                loadModelOptions()
                refreshProjects()
            } catch (e: Exception) {
                api = null
                binding.textStatus.text = getString(R.string.status_disconnected)
                binding.layoutModelSelect.visibility = View.GONE
                modelOptions = emptyList()
                selectedModelOption = null
                appendLog("连接失败: ${e.message}")
                toast("连接失败")
            } finally {
                setBusy(false)
            }
        }
    }

    private fun confirmRegisterUser() {
        val serverUrl = binding.editServerUrl.text?.toString()?.trim().orEmpty()
        if (serverUrl.isBlank()) {
            toast("请填写服务器地址")
            return
        }
        if (prefs.apiToken.isBlank()) {
            registerUser(serverUrl)
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.register_confirm_title)
            .setMessage(R.string.register_confirm_message)
            .setPositiveButton(R.string.register_user) { _, _ -> registerUser(serverUrl) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun registerUser(serverUrl: String) {
        prefs.serverUrl = serverUrl
        setBusy(true)
        lifecycleScope.launch {
            try {
                val account = withContext(Dispatchers.IO) {
                    AgentApi(serverUrl).register()
                }
                prefs.userId = account.userId
                prefs.apiToken = account.token
                prefs.selectedProjectId = null
                binding.editUserId.setText(account.userId)
                binding.editApiToken.setText(account.token)
                api = AgentApi(serverUrl, account.token)
                appendLog("注册成功，用户 ID: ${account.userId}")
                connectServer()
            } catch (e: Exception) {
                appendLog("注册失败: ${e.message}")
                toast("注册失败")
            } finally {
                setBusy(false)
            }
        }
    }

    private suspend fun loadModelOptions() {
        val currentApi = api ?: return
        val catalog = withContext(Dispatchers.IO) { currentApi.listModels() }
        modelOptions = catalog.models
        if (modelOptions.isEmpty()) {
            binding.layoutModelSelect.visibility = View.GONE
            return
        }

        val labels = modelOptions.map { it.label }
        val adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, labels)
        binding.dropdownModel.setAdapter(adapter)
        binding.layoutModelSelect.visibility = View.VISIBLE

        val savedId = prefs.selectedProviderId
        val selected = modelOptions.firstOrNull { it.id == savedId }
            ?: modelOptions.firstOrNull { it.isDefault }
            ?: modelOptions.first()
        selectModelOption(selected, persist = false)
        appendLog("可选模型: ${modelOptions.size} 个")
    }

    private fun selectModelOption(option: ModelOption, persist: Boolean = true) {
        selectedModelOption = option
        if (persist) {
            prefs.selectedProviderId = option.id
            appendLog("已选择模型: ${option.label}")
        }
        binding.dropdownModel.setText(option.label, false)
    }

    private fun refreshProjects() {
        val currentApi = api ?: run {
            toast("请先连接服务器")
            return
        }

        setBusy(true)
        lifecycleScope.launch {
            try {
                val loaded = withContext(Dispatchers.IO) { currentApi.listProjects() }
                projects = loaded
                val selectedId = selectedProject?.id ?: prefs.selectedProjectId
                val restored = loaded.firstOrNull { it.id == selectedId } ?: loaded.firstOrNull()
                selectedProject = restored
                if (restored != null) {
                    prefs.selectedProjectId = restored.id
                }
                projectAdapter.submitList(projects, selectedProject?.id)
                if (loaded.isEmpty()) {
                    appendLog(getString(R.string.no_projects))
                } else {
                    appendLog("已加载 ${loaded.size} 个项目")
                }
            } catch (e: Exception) {
                appendLog("刷新项目失败: ${e.message}")
                toast("刷新失败")
            } finally {
                setBusy(false)
            }
        }
    }

    private fun showCreateProjectDialog() {
        if (api == null) {
            toast("请先连接服务器")
            return
        }

        val dialogBinding = DialogCreateProjectBinding.inflate(LayoutInflater.from(this))
        AlertDialog.Builder(this)
            .setTitle(R.string.new_project)
            .setView(dialogBinding.root)
            .setPositiveButton(R.string.create) { _, _ ->
                val name = dialogBinding.editProjectName.text?.toString()?.trim().orEmpty()
                val packageName = dialogBinding.editPackageName.text?.toString()?.trim()
                if (name.isBlank()) {
                    toast("请填写项目名称")
                    return@setPositiveButton
                }
                createProject(name, packageName)
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun createProject(name: String, packageName: String?) {
        val currentApi = api ?: return
        setBusy(true)
        lifecycleScope.launch {
            try {
                val project = withContext(Dispatchers.IO) {
                    currentApi.createProject(name, packageName)
                }
                selectProject(project)
                refreshProjects()
                appendLog("已创建项目: ${project.id}")
            } catch (e: Exception) {
                appendLog("创建项目失败: ${e.message}")
                toast("创建失败")
            } finally {
                setBusy(false)
            }
        }
    }

    private fun sendPrompt() {
        val currentApi = api ?: run {
            toast("请先连接服务器")
            return
        }
        val project = selectedProject ?: run {
            toast("请先选择一个项目")
            return
        }
        val prompt = binding.editPrompt.text?.toString()?.trim().orEmpty()
        if (prompt.isBlank()) {
            toast("请填写需求")
            return
        }

        val modelOption = selectedModelOption
        val provider = modelOption?.provider?.takeUnless { it == "auto" }
        val autoFallback = modelOption?.id == "auto"

        pollJob?.cancel()
        setBusy(true)
        lifecycleScope.launch {
            try {
                appendLog("提交任务到 Agent...")
                val job = withContext(Dispatchers.IO) {
                    currentApi.ask(
                        projectId = project.id,
                        prompt = prompt,
                        provider = provider ?: "auto",
                        autoFallback = autoFallback,
                    )
                }
                appendLog("任务已创建: ${job.id}")
                pollJob = launch { pollJobUntilDone(currentApi, job.id) }
            } catch (e: Exception) {
                appendLog("发送失败: ${e.message}")
                toast("发送失败")
                setBusy(false)
            }
        }
    }

    private suspend fun pollJobUntilDone(currentApi: AgentApi, jobId: String) {
        var lastEventCount = 0
        while (coroutineContext.isActive) {
            try {
                val job = withContext(Dispatchers.IO) { currentApi.getJob(jobId) }
                while (lastEventCount < job.events.size) {
                    appendLog(formatEvent(job.events[lastEventCount]))
                    lastEventCount++
                }

                when (job.status) {
                    "completed" -> {
                        appendLog("=== 任务完成 ===")
                        job.result?.let { appendLog(it) }
                        refreshProjects()
                        setBusy(false)
                        return
                    }
                    "failed" -> {
                        appendLog("=== 任务失败 ===")
                        appendLog(job.error ?: "未知错误")
                        setBusy(false)
                        return
                    }
                }
            } catch (e: Exception) {
                appendLog("轮询失败: ${e.message}")
                setBusy(false)
                return
            }
            delay(1000)
        }
    }

    private fun downloadApk() {
        val currentApi = api ?: run {
            toast("请先连接服务器")
            return
        }
        val project = selectedProject ?: run {
            toast("请先选择一个项目")
            return
        }

        setBusy(true)
        lifecycleScope.launch {
            try {
                val apkFile = File(cacheDir, "apk/${project.id}.apk")
                withContext(Dispatchers.IO) {
                    currentApi.downloadApk(project.id, apkFile)
                }
                downloadedApk = apkFile
                appendLog("APK 已下载: ${apkFile.absolutePath}")
                toast("下载完成")
            } catch (e: Exception) {
                appendLog("下载 APK 失败: ${e.message}")
                toast("下载失败")
            } finally {
                setBusy(false)
            }
        }
    }

    private fun requestInstallPermissionIfNeeded() {
        if (downloadedApk == null) {
            toast("请先下载 APK")
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !packageManager.canRequestPackageInstalls()
        ) {
            val intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES).apply {
                data = Uri.parse("package:$packageName")
            }
            installPermissionLauncher.launch(intent)
        } else {
            installDownloadedApk()
        }
    }

    private fun installDownloadedApk() {
        val apk = downloadedApk ?: run {
            toast("请先下载 APK")
            return
        }
        val uri = FileProvider.getUriForFile(
            this,
            "${packageName}.fileprovider",
            apk,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        startActivity(intent)
    }

    private fun selectProject(project: ProjectInfo) {
        selectedProject = project
        prefs.selectedProjectId = project.id
        projectAdapter.submitList(projects, project.id)
        appendLog("已选择项目: ${project.name} (${project.id})")
    }

    private fun browseFiles() {
        val project = selectedProject ?: run {
            toast("请先选择一个项目")
            return
        }
        if (api == null) {
            toast("请先连接服务器")
            return
        }
        val serverUrl = binding.editServerUrl.text?.toString()?.trim().orEmpty()
        val apiToken = binding.editApiToken.text?.toString()?.trim().orEmpty()
        FileBrowserActivity.start(this, project, serverUrl, apiToken)
    }

    private fun formatEvent(event: JSONObject): String {
        val type = event.optString("type")
        val message = event.optString("message")
        return when (type) {
            "turn" -> message.ifBlank { "轮次 ${event.optInt("turn")}" }
            "text" -> event.optString("content", message)
            "tool_call" -> message.ifBlank {
                "工具: ${event.optString("name")}"
            }
            "tool_result" -> message.ifBlank {
                "结果: ${event.optString("name")} -> ${event.optBoolean("ok")}"
            }
            "started" -> "任务开始"
            "completed" -> "任务结束"
            "failed" -> "任务失败: ${event.optString("error")}"
            "model_switch" -> message.ifBlank {
                "切换模型: ${event.optString("from_model")} -> ${event.optString("to_model")}"
            }
            "provider_switch" -> message.ifBlank {
                "切换提供商: ${event.optString("from_provider")} -> ${event.optString("to_provider")}"
            }
            else -> message.ifBlank { type }
        }
    }

    private fun appendLog(message: String) {
        val current = binding.textLogs.text?.toString().orEmpty()
        binding.textLogs.text = if (current.isBlank()) {
            message
        } else {
            "$current\n$message"
        }
        binding.scrollLogs.post {
            binding.scrollLogs.fullScroll(android.view.View.FOCUS_DOWN)
        }
    }

    private fun setBusy(busy: Boolean) {
        binding.btnRegister.isEnabled = !busy
        binding.btnConnect.isEnabled = !busy
        binding.btnRefreshProjects.isEnabled = !busy
        binding.btnNewProject.isEnabled = !busy
        binding.btnBrowseFiles.isEnabled = !busy
        binding.btnSend.isEnabled = !busy
        binding.btnDownloadApk.isEnabled = !busy
        binding.btnInstallApk.isEnabled = !busy
        binding.dropdownModel.isEnabled = !busy
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
