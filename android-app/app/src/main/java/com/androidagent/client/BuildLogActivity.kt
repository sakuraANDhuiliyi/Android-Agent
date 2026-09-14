package com.androidagent.client

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityBuildLogBinding
import com.google.android.material.tabs.TabLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

class BuildLogActivity : AppCompatActivity() {

    private lateinit var binding: ActivityBuildLogBinding
    private lateinit var api: AgentApi
    private lateinit var prefs: AgentPrefs
    private var jobId: String = ""
    private var logText: String = ""
    private var errors: List<String> = emptyList()
    private var loadedChars: Int = 0
    private var totalSize: Long = 0L
    private var hasMore: Boolean = false
    private var loadingMore = false
    private var summariesApplied = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityBuildLogBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)
        jobId = intent.getStringExtra(EXTRA_JOB_ID).orEmpty()
        binding.toolbar.title = getString(R.string.build_and_logs)
        binding.toolbar.setNavigationIcon(R.drawable.ic_arrow_back)
        binding.toolbar.setNavigationOnClickListener { finish() }
        if (jobId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.tabs.addTab(binding.tabs.newTab().setText(R.string.tab_key_errors))
        binding.tabs.addTab(binding.tabs.newTab().setText(R.string.tab_full_log))
        binding.tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) = showTab(tab.position)
            override fun onTabUnselected(tab: TabLayout.Tab) = Unit
            override fun onTabReselected(tab: TabLayout.Tab) = Unit
        })
        binding.btnCopyPath.setOnClickListener {
            val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("log", binding.textLogPath.text))
            toast(getString(R.string.copied))
        }
        binding.btnAgentFix.setOnClickListener { FeedbackActivity.start(this, jobId = jobId, problems = true) }
        binding.btnLoadMore.setOnClickListener { loadNextPage() }
        val cached = prefs.getSnippet("buildlog:$jobId")
        if (!cached.isNullOrBlank()) applyLogPage(cached, more = false)
        lifecycleScope.launch {
            try {
                // Domain Core：错误清单来自服务端结构化摘要，客户端不再解析 gradle 日志
                val job = withContext(Dispatchers.IO) { api.getJob(jobId) }
                val summaries = structuredSummaries(job.events)
                val build = summaries.lastOrNull { it.kind == "build" }
                val test = summaries.lastOrNull { it.kind == "test" }
                applySummaries(build, test)
                // 性能：日志分页加载，10MB 级日志不全量下发
                loadPage(offset = 0)
            } catch (e: Exception) {
                val msg = when (e) {
                    is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message
                    else -> e.message
                }
                toast(msg ?: "错误")
            }
        }
    }

    private suspend fun loadPage(offset: Int) {
        val page = withContext(Dispatchers.IO) { api.getTaskBuildLogPage(jobId, offset, LOG_PAGE_CHARS) }
        if (offset == 0) {
            applyLogPage(page.content, page.hasMore)
            prefs.cacheSnippet("buildlog:$jobId", page.content.take(LOG_PAGE_CHARS))
        } else {
            appendLogPage(page.content, page.hasMore)
        }
        totalSize = page.totalSize
    }

    private fun loadNextPage() {
        if (loadingMore || !hasMore) return
        loadingMore = true
        lifecycleScope.launch {
            try {
                loadPage(offset = loadedChars)
            } catch (e: Exception) {
                toast(e.message ?: "加载失败")
            } finally {
                loadingMore = false
            }
        }
    }

    private fun applyLogPage(content: String, more: Boolean) {
        logText = content
        loadedChars = content.length
        hasMore = more
        binding.textBuildLog.text = content
        updateLoadMoreUi()
        if (!summariesApplied) {
            // 摘要未到时先用首页内容兜底解析错误
            errors = extractErrors(content)
            renderErrorList(content)
        }
        showTab(binding.tabs.selectedTabPosition.coerceAtLeast(0))
    }

    private fun appendLogPage(content: String, more: Boolean) {
        logText += content
        loadedChars += content.length
        hasMore = more
        binding.textBuildLog.text = logText
        updateLoadMoreUi()
    }

    private fun updateLoadMoreUi() {
        binding.btnLoadMore.visibility = if (hasMore) View.VISIBLE else View.GONE
        binding.btnLoadMore.text = if (totalSize > 0) {
            getString(R.string.log_loaded_of, formatKb(loadedChars.toLong()), formatKb(totalSize))
        } else {
            getString(R.string.load_more_log)
        }
    }

    private fun formatKb(chars: Long): String =
        if (chars >= 1024) "${chars / 1024}KB" else "${chars}B"

    private fun applySummaries(build: StructuredSummary?, test: StructuredSummary?) {
        val structured = listOfNotNull(build, test)
        if (structured.isEmpty()) return
        summariesApplied = true
        errors = structured.flatMap { it.errors }
        val failed = structured.any { !it.success }
        binding.cardFailure.visibility = if (failed || errors.isNotEmpty()) View.VISIBLE else View.GONE
        binding.textFailure.text = errors.firstOrNull()
            ?: getString(if (test?.success == false) R.string.test_failed else R.string.build_failed_at)
        binding.textLogPath.text = build?.logPath?.takeIf { it.isNotBlank() } ?: "job/$jobId/build.log"
        renderErrorList(logText)
    }

    private fun renderErrorList(fallbackContent: String) {
        if (!summariesApplied) {
            binding.cardFailure.visibility =
                if (errors.isNotEmpty() || fallbackContent.contains("BUILD FAILED", true)) View.VISIBLE else View.GONE
            binding.textFailure.text = errors.firstOrNull() ?: getString(R.string.build_failed_at)
            binding.textLogPath.text = "job/$jobId/build.log"
        }
        binding.layoutErrors.removeAllViews()
        if (errors.isEmpty()) {
            binding.layoutErrors.addView(TextView(this).apply {
                text = getString(R.string.no_key_errors)
                setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodyMedium)
            })
        } else {
            errors.take(8).forEach { err ->
                binding.layoutErrors.addView(TextView(this).apply {
                    text = err
                    setPadding(0, 16, 0, 16)
                    setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodyMedium)
                    setTextColor(getColor(R.color.status_failed))
                })
            }
        }
        binding.tabs.getTabAt(0)?.text = getString(R.string.tab_key_errors) + " (${errors.size})"
    }

    private fun showTab(position: Int) {
        val errorsTab = position == 0
        binding.layoutErrors.visibility = if (errorsTab) View.VISIBLE else View.GONE
        binding.textBuildLog.visibility = if (errorsTab) View.GONE else View.VISIBLE
    }

    /** 旧任务回退：从原始日志正则提取错误行。 */
    private fun extractErrors(log: String): List<String> {
        val lines = log.lineSequence()
            .map { it.trim() }
            .filter { line ->
                line.contains("error:", ignoreCase = true) ||
                    line.contains("e: ", ignoreCase = true) ||
                    line.contains("FAILED", ignoreCase = false)
            }
            .distinct()
            .take(12)
            .toList()
        return lines
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    /** 服务端结构化摘要（Domain Core），job.events 中的 build_summary / test_summary。 */
    data class StructuredSummary(
        val kind: String,
        val success: Boolean,
        val errors: List<String>,
        val logPath: String?,
    )

    companion object {
        private const val EXTRA_JOB_ID = "job_id"
        private val SUMMARY_TYPES = setOf("build_summary", "test_summary")
        /** 首屏/每页日志字符数：64K，10MB 日志按需翻页 */
        const val LOG_PAGE_CHARS = 65_536

        fun start(context: Context, jobId: String) {
            FeedbackActivity.start(context, jobId = jobId)
        }

        fun startRaw(context: Context, jobId: String) {
            context.startActivity(Intent(context, BuildLogActivity::class.java).putExtra(EXTRA_JOB_ID, jobId))
        }

        /** job.events 是平铺事件 {id, type, ts, ...summary 字段}。 */
        fun structuredSummaries(events: List<JSONObject>): List<StructuredSummary> = events
            .filter { it.optString("type") in SUMMARY_TYPES }
            .mapNotNull { ev ->
                if (!ev.has("kind")) return@mapNotNull null
                StructuredSummary(
                    kind = ev.optString("kind"),
                    success = ev.optBoolean("success", false),
                    errors = ev.optJSONArray("errors")?.let { arr ->
                        (0 until arr.length()).mapNotNull { i -> arr.optString(i).takeIf { it.isNotBlank() } }
                    } ?: emptyList(),
                    logPath = ev.optString("log_path").takeIf { it.isNotBlank() },
                )
            }
    }
}
