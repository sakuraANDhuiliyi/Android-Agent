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
    private var currentJobId: String? = null
    private var displayedJobId: String? = null

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
        binding.btnStop.setOnClickListener { stopCurrentJob() }
        binding.btnDownloadApk.setOnClickListener { downloadApk() }
        binding.btnInstallApk.setOnClickListener { requestInstallPermissionIfNeeded() }
        binding.btnLoadFullLog.setOnClickListener { loadFullBuildLog() }

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
        binding.textStatus.text = "正在提交任务..."
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
                currentJobId = job.id
                binding.btnStop.visibility = View.VISIBLE
                binding.textStatus.text = "任务运行中"
                toast("任务已提交")
                pollJob = launch { pollJobUntilDone(currentApi, job.id) }
            } catch (e: Exception) {
                appendLog("发送失败: ${e.message}")
                binding.textStatus.text = "发送失败"
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
                renderDebugSummary(job)
                while (lastEventCount < job.events.size) {
                    appendLog(formatEvent(job.events[lastEventCount]))
                    lastEventCount++
                }

                when (job.status) {
                    "succeeded" -> {
                        appendLog("=== 任务完成 ===")
                        job.result?.let { appendLog(it) }
                        appendTaskSummary(job)
                        binding.textStatus.text = "任务成功"
                        refreshProjects()
                        setBusy(false)
                        finishPolling()
                        return
                    }
                    "failed" -> {
                        appendLog("=== 任务失败 ===")
                        appendLog(job.error ?: "未知错误")
                        appendTaskSummary(job)
                        binding.textStatus.text = "任务失败"
                        setBusy(false)
                        finishPolling()
                        return
                    }
                    "canceled" -> {
                        appendLog("=== 任务已停止 ===")
                        binding.textStatus.text = "任务已停止"
                        setBusy(false)
                        finishPolling()
                        return
                    }
                }
            } catch (e: Exception) {
                appendLog("轮询失败: ${e.message}")
                binding.textStatus.text = "同步失败"
                setBusy(false)
                return
            }
            delay(1000)
        }
    }

    private fun stopCurrentJob() {
        val currentApi = api ?: return
        val jobId = currentJobId ?: return
        binding.btnStop.isEnabled = false
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { currentApi.cancelJob(jobId) }
                appendLog("已发送停止请求，将在安全检查点停止")
            } catch (e: Exception) {
                appendLog("停止失败: ${e.message}")
                binding.btnStop.isEnabled = true
            }
        }
    }

    private fun finishPolling() {
        currentJobId = null
        binding.btnStop.visibility = View.GONE
        binding.btnStop.isEnabled = true
    }

    private fun appendTaskSummary(job: JobInfo) {
        job.totalTokens?.let {
            appendLog("Token: 输入 ${job.inputTokens ?: "?"} / 输出 ${job.outputTokens ?: "?"} / 总计 $it")
        }
        if (job.changedFiles.isNotEmpty()) {
            appendLog("改动文件 (${job.changedFiles.size}):")
            job.changedFiles.forEach { file ->
                appendLog("  ${file.optString("change")}  ${file.optString("path")}")
            }
        }
    }

    private fun renderDebugSummary(job: JobInfo) {
        displayedJobId = job.id
        val turns = job.events.count { it.optString("type") == "turn" }
        val toolCalls = job.events.count { it.optString("type") == "tool_call" }
        val toolFailures = job.events.count {
            it.optString("type") == "tool_result" && !it.optBoolean("ok")
        }
        val switches = job.events.count {
            it.optString("type") == "model_switch" || it.optString("type") == "provider_switch"
        }
        val started = job.startedAt ?: job.createdAt
        val ended = job.finishedAt ?: (System.currentTimeMillis() / 1000.0)
        val duration = started?.let { (ended - it).coerceAtLeast(0.0).toInt() }
        val changes = if (job.changedFiles.isEmpty()) {
            "无"
        } else {
            job.changedFiles.joinToString("\n") {
                "  ${it.optString("change")} ${it.optString("path")}"
            }
        }
        binding.textDebugSummary.text = buildString {
            appendLine("任务: ${job.id}")
            appendLine("状态: ${job.status}${if (job.cancelRequested) "（停止中）" else ""}")
            appendLine("模型: ${job.provider ?: "?"} / ${job.model ?: "?"}")
            appendLine("轮次: $turns  工具: $toolCalls  失败: $toolFailures  切换: $switches")
            appendLine("Token: ${job.inputTokens ?: "?"} + ${job.outputTokens ?: "?"} = ${job.totalTokens ?: "?"}")
            appendLine("耗时: ${duration?.let { "${it}s" } ?: "?"}")
            append("改动:\n$changes")
            job.error?.let { append("\n错误: $it") }
        }
        binding.btnLoadFullLog.visibility = if (job.hasBuildLog) View.VISIBLE else View.GONE
    }

    private fun loadFullBuildLog() {
        val currentApi = api ?: return
        val jobId = displayedJobId ?: currentJobId ?: selectedProject?.latestTaskId ?: run {
            toast("没有可用的任务日志")
            return
        }
        binding.btnLoadFullLog.isEnabled = false
        lifecycleScope.launch {
            try {
                val content = withContext(Dispatchers.IO) { currentApi.getTaskBuildLog(jobId) }
                appendLog("=== 完整构建日志 ===\n$content")
                binding.mainScroll.post { binding.mainScroll.fullScroll(View.FOCUS_DOWN) }
            } catch (e: Exception) {
                toast("读取日志失败")
                appendLog("读取完整构建日志失败: ${e.message}")
            } finally {
                binding.btnLoadFullLog.isEnabled = true
            }
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
        ProjectDetailActivity.start(this, project)
    }

    private fun loadRecentTasks(project: ProjectInfo) {
        val currentApi = api ?: return
        lifecycleScope.launch {
            try {
                val jobs = withContext(Dispatchers.IO) { currentApi.listJobs(project.id) }
                if (jobs.isNotEmpty()) {
                    appendLog("最近任务: " + jobs.take(5).joinToString(" | ") { "${it.id}:${it.status}" })
                    val latest = withContext(Dispatchers.IO) { currentApi.getJob(jobs.first().id) }
                    renderDebugSummary(latest)
                }
                val active = jobs.firstOrNull { it.status == "queued" || it.status == "running" }
                if (active != null && currentJobId != active.id) {
                    currentJobId = active.id
                    binding.btnStop.visibility = View.VISIBLE
                    pollJob?.cancel()
                    pollJob = launch { pollJobUntilDone(currentApi, active.id) }
                }
            } catch (e: Exception) {
                appendLog("读取任务历史失败: ${e.message}")
            }
        }
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
            } + event.optLong("duration_ms").takeIf { it > 0 }?.let { " (${it}ms)" }.orEmpty()
            "started" -> "任务开始"
            "completed" -> "任务结束"
            "failed" -> "任务失败: ${event.optString("error")}"
            "canceled" -> "任务已停止"
            "cancel_requested" -> "已请求停止"
            "usage" -> event.optJSONObject("usage")?.let {
                "Token usage: ${it.optInt("input_tokens")} + ${it.optInt("output_tokens")} = ${it.optInt("total_tokens")}"
            } ?: "Token usage 未知"
            "changes" -> message
            "auto_continue" -> message.ifBlank { "Agent 自动继续下一批轮次" }
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
    }

    private fun setBusy(busy: Boolean) {
        binding.btnRegister.isEnabled = !busy
        binding.btnConnect.isEnabled = !busy
        binding.btnRefreshProjects.isEnabled = !busy
        binding.btnNewProject.isEnabled = !busy
        binding.btnBrowseFiles.isEnabled = !busy
        binding.btnSend.isEnabled = !busy
        binding.btnStop.isEnabled = currentJobId != null
        binding.btnDownloadApk.isEnabled = !busy
        binding.btnInstallApk.isEnabled = !busy
        binding.dropdownModel.isEnabled = !busy
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
