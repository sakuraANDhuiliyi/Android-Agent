package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.androidagent.client.databinding.ActivityFeedbackBinding
import com.androidagent.client.core.database.CacheSession
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class FeedbackActivity : AppCompatActivity() {
    private lateinit var binding: ActivityFeedbackBinding
    private lateinit var api: AgentApi
    private lateinit var prefs: AgentPrefs
    private lateinit var session: CacheSession
    private var projectId = ""
    private var jobId: String? = null
    private var report = JSONObject()
    private var optionsLoaded = false
    private var busy = false
    private var initialScroll = true

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityFeedbackBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        session = CacheSession.capture(prefs)
        api = AgentApi(session.serverUrl, session.apiToken)
        projectId = intent.getStringExtra("project_id").orEmpty()
        jobId = intent.getStringExtra("job_id")
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.toolbar.title = getString(if (intent.getBooleanExtra("problems", false)) R.string.feedback_problems else R.string.feedback_title)
        binding.btnSaveOptions.setOnClickListener { saveOptions(false) }
        binding.btnRunBuild.setOnClickListener { saveOptions(true) }
        binding.btnFixAll.setOnClickListener { fixProblems(null) }
        binding.btnRuntime.setOnClickListener { addRuntimeLog() }
        binding.btnRawLog.setOnClickListener {
            if (session.isCurrent(prefs)) report.optString("job_id").takeIf { it.isNotBlank() && it != "null" }
                ?.let { BuildLogActivity.startRaw(this, it) }
        }
        binding.btnArtifact.setOnClickListener {
            if (session.isCurrent(prefs)) report.optJSONObject("artifact")?.let { ApkActivity.start(this, projectId, it.getString("job_id"), true) }
        }
        setActions(false)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    try {
                        val data = withContext(Dispatchers.IO) {
                            requireCurrentSession()
                            if (projectId.isBlank()) projectId = jobId?.let { api.getJob(it).projectId }.orEmpty()
                            require(projectId.isNotBlank()) { "缺少项目" }
                            api.feedback(projectId, jobId).also { requireCurrentSession() }
                        }
                        requireCurrentSession()
                        report = data
                        render()
                    } catch (e: kotlinx.coroutines.CancellationException) { throw e }
                    catch (e: Exception) { binding.textStatus.text = e.message; }
                    delay(5000)
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        if (!session.isCurrent(prefs)) {
            binding.root.visibility = android.view.View.INVISIBLE
            finish()
        }
    }

    private fun requireCurrentSession() {
        if (!session.isCurrent(prefs)) throw CancellationException("账号已切换，请重新打开验证详情")
    }

    private fun render() {
        val artifact = report.optJSONObject("artifact")
        val issues = report.optJSONArray("problems") ?: JSONArray()
        val summary = FeedbackPresentation.from(report)
        binding.textStatus.text = "${summary.verification.title}\n${summary.verification.build}\n安装未验证"
        binding.textSummary.text = summary.duration
        binding.textTaskMetrics.text = summary.tasks
        binding.textWarningMetrics.text = summary.warnings
        binding.textTestMetrics.text = summary.tests
        binding.textArtifactMetrics.text = summary.artifact
        binding.textStatus.setTextColor(getColor(when {
            summary.verification.buildFailed -> R.color.status_failed
            else -> R.color.signal_on_surface
        }))
        binding.btnArtifact.isVisible = artifact != null
        binding.btnRawLog.isEnabled = !report.isNull("job_id")
        if (!optionsLoaded) {
            val options = report.optJSONObject("settings") ?: JSONObject()
            binding.switchBuild.isChecked = options.optBoolean("build_after_changes")
            binding.switchTests.isChecked = options.optBoolean("run_tests")
            binding.switchFix.isChecked = options.optBoolean("fix_failures")
            optionsLoaded = true
        }
        binding.textProblemsTitle.text = "${issues.length()} 个问题"
        binding.layoutProblems.removeAllViews()
        if (issues.length() == 0) binding.layoutProblems.addView(TextView(this).apply { text = getString(R.string.problems_none); setPadding(0, 20, 0, 20) })
        for (index in 0 until issues.length()) {
            val issue = issues.getJSONObject(index)
            binding.layoutProblems.addView(TextView(this).apply {
                text = "${issue.optString("severity").uppercase()} · ${issue.optString("source")}\n" +
                    (issue.optString("path").takeIf { it.isNotBlank() && it != "null" }?.let { "$it:${issue.optInt("line", 1)}\n" } ?: "") + issue.optString("message")
                setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodyMedium)
                val pad = (16 * resources.displayMetrics.density).toInt()
                setPadding(pad, pad, pad, pad)
                setLineSpacing(0f, 1.5f)
                background = getDrawable(R.drawable.bg_soft_list_row)
                setTextColor(getColor(when (issue.optString("severity").lowercase()) {
                    "error" -> R.color.status_failed
                    "warning" -> R.color.status_warning
                    else -> R.color.signal_on_surface
                }))
                minHeight = (64 * resources.displayMetrics.density).toInt()
                isFocusable = true
                setOnClickListener { problemActions(issue) }
            })
        }
        setActions(!busy)
        if (intent.getBooleanExtra("problems", false)) {
            val content = binding.textStatus.parent as android.view.ViewGroup
            for (index in 0 until content.childCount) {
                val child = content.getChildAt(index)
                if (child === binding.textProblemsTitle) break
                child.visibility = android.view.View.GONE
            }
            if (initialScroll) {
                initialScroll = false
                binding.scroll.post { binding.scroll.scrollTo(0, 0) }
            }
        }
    }

    private fun problemActions(issue: JSONObject) {
        if (!session.isCurrent(prefs)) { finish(); return }
        val path = issue.optString("path").takeIf { it.isNotBlank() && it != "null" }
        val actions = mutableListOf(getString(R.string.feedback_fix_one), getString(R.string.copy))
        if (path != null) actions.add(0, getString(R.string.feedback_open_file))
        AlertDialog.Builder(this).setTitle(issue.optString("source"))
            .setItems(actions.toTypedArray()) { _, index ->
                when (actions[index]) {
                    getString(R.string.feedback_open_file) -> FileBrowserActivity.start(this, ProjectInfo(projectId, projectId, "", false, null, null), prefs.serverUrl, prefs.apiToken, path, issue.optInt("line", 1))
                    getString(R.string.copy) -> { (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).setPrimaryClip(ClipData.newPlainText("Problem", issue.optString("message"))); toast(getString(R.string.copied)) }
                    else -> fixProblems(issue)
                }
            }.setNegativeButton(R.string.cancel, null).show()
    }

    private fun saveOptions(run: Boolean) {
        if (busy || !optionsLoaded) return
        if (!session.isCurrent(prefs)) { finish(); return }
        busy = true; setActions(false)
        val options = JSONObject().put("build_after_changes", binding.switchBuild.isChecked)
            .put("run_tests", binding.switchTests.isChecked).put("fix_failures", binding.switchFix.isChecked)
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) { requireCurrentSession(); api.saveFeedbackSettings(projectId, options) }
                requireCurrentSession()
                if (run) {
                    val (conversation, job) = withContext(Dispatchers.IO) {
                        requireCurrentSession()
                        val conv = api.createConversation(projectId, getString(R.string.feedback_title))
                        requireCurrentSession()
                        conv to api.askConversation(conv.id, "运行项目构建与已启用的测试，并按项目设置修复失败。", feedbackRequested = true)
                    }
                    requireCurrentSession()
                    ConversationActivity.start(this@FeedbackActivity, projectId, conversation.id, conversation.title, job.id)
                } else toast(getString(R.string.saved))
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { toast(e.message.orEmpty()) }
            finally { busy = false; setActions(true) }
        }
    }

    private fun addRuntimeLog() {
        if (!session.isCurrent(prefs)) { finish(); return }
        val input = android.widget.EditText(this).apply { minLines = 4; maxLines = 10; hint = getString(R.string.feedback_add_runtime); filters = arrayOf(android.text.InputFilter.LengthFilter(24_000)) }
        AlertDialog.Builder(this).setTitle(R.string.feedback_add_runtime).setView(input)
            .setPositiveButton(R.string.add) { _, _ ->
                val text = input.text.toString().trim()
                if (text.isNotEmpty()) lifecycleScope.launch {
                    try {
                        withContext(Dispatchers.IO) { requireCurrentSession(); api.addRuntimeDiagnostic(projectId, text) }
                        report = withContext(Dispatchers.IO) { requireCurrentSession(); api.feedback(projectId, jobId) }
                        requireCurrentSession()
                        render()
                    } catch (e: CancellationException) { throw e }
                    catch (e: Exception) { toast(e.message.orEmpty()) }
                }
            }.setNegativeButton(R.string.cancel, null).show()
    }

    private fun fixProblems(issue: JSONObject?) {
        if (busy) return
        if (!session.isCurrent(prefs)) { finish(); return }
        val issues = issue?.let { listOf(it) } ?: (report.optJSONArray("problems") ?: JSONArray()).let { array -> (0 until array.length()).map { array.getJSONObject(it) } }
        if (issues.isEmpty()) return
        val diagnostics = issues.joinToString("\n\n") { "${it.optString("source")} ${it.optString("path", "")}:${it.optString("line", "")}\n${it.optString("message")}" }.take(24_000)
        val contexts = mutableListOf(ContextAttachment("diff", "Current diff"), ContextAttachment("error", "Build / Test / Problems", text = diagnostics))
        report.optString("job_id").takeIf { it.isNotBlank() && it != "null" }?.let { contexts += ContextAttachment("build_log", "Build log", refId = it) }
        busy = true; setActions(false)
        lifecycleScope.launch {
            try {
                val (conversation, job) = withContext(Dispatchers.IO) {
                    requireCurrentSession()
                    val conv = api.createConversation(projectId, getString(R.string.feedback_fix_all))
                    requireCurrentSession()
                    conv to api.askConversation(conv.id, "修复项目 $projectId 的以下问题。结合当前 Diff、构建错误和测试失败定位原因，保留无关修改，修复后重新构建并验证测试。", contexts = contexts)
                }
                requireCurrentSession()
                ConversationActivity.start(this@FeedbackActivity, projectId, conversation.id, conversation.title, job.id)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { toast(e.message.orEmpty()) }
            finally { busy = false; setActions(true) }
        }
    }

    private fun setActions(enabled: Boolean) {
        binding.btnSaveOptions.isEnabled = enabled && optionsLoaded
        binding.btnRunBuild.isEnabled = enabled && optionsLoaded
        binding.btnFixAll.isEnabled = enabled && (report.optJSONArray("problems")?.length() ?: 0) > 0
        listOf(binding.switchBuild, binding.switchTests, binding.switchFix).forEach { it.isEnabled = enabled && optionsLoaded }
    }
    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()

    companion object {
        fun start(context: Context, projectId: String = "", jobId: String? = null, problems: Boolean = false) {
            context.startActivity(Intent(context, FeedbackActivity::class.java).putExtra("project_id", projectId).putExtra("job_id", jobId).putExtra("problems", problems))
        }
    }
}
