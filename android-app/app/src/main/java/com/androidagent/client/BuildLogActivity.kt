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

class BuildLogActivity : AppCompatActivity() {

    private lateinit var binding: ActivityBuildLogBinding
    private var jobId: String = ""
    private var logText: String = ""
    private var errors: List<String> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityBuildLogBinding.inflate(layoutInflater)
        setContentView(binding.root)
        val prefs = AgentPrefs(this)
        jobId = intent.getStringExtra(EXTRA_JOB_ID).orEmpty()
        binding.toolbar.title = getString(R.string.build_and_logs)
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }
        if (jobId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
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
        binding.btnAgentFix.setOnClickListener { agentFix(prefs) }
        val cached = prefs.getSnippet("buildlog:$jobId")
        if (!cached.isNullOrBlank()) applyLog(cached)
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val content = withContext(Dispatchers.IO) { api.getTaskBuildLog(jobId) }
                val display = if (content.length > 200_000) {
                    content.take(200_000) + "\n\n… " + getString(R.string.large_diff_truncated)
                } else content
                applyLog(display)
                prefs.cacheSnippet("buildlog:$jobId", display)
            } catch (e: Exception) {
                val msg = when (e) {
                    is ApiException -> if (e.isNotFound || e.isForbidden) getString(R.string.resource_unavailable) else e.message
                    else -> e.message
                }
                toast(msg ?: "错误")
            }
        }
    }

    private fun applyLog(content: String) {
        logText = content
        errors = extractErrors(content)
        binding.cardFailure.visibility = if (errors.isNotEmpty() || content.contains("BUILD FAILED", true)) View.VISIBLE else View.GONE
        binding.textFailure.text = errors.firstOrNull() ?: getString(R.string.build_failed_at)
        binding.textLogPath.text = "job/$jobId/build.log"
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
        binding.textBuildLog.text = content
        binding.tabs.getTabAt(0)?.text = getString(R.string.tab_key_errors) + " (${errors.size})"
        showTab(binding.tabs.selectedTabPosition.coerceAtLeast(0))
    }

    private fun showTab(position: Int) {
        val errorsTab = position == 0
        binding.layoutErrors.visibility = if (errorsTab) View.VISIBLE else View.GONE
        binding.textBuildLog.visibility = if (errorsTab) View.GONE else View.VISIBLE
    }

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

    private fun agentFix(prefs: AgentPrefs) {
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        binding.btnAgentFix.isEnabled = false
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    api.followUpJob(jobId, getString(R.string.agent_fix_prompt))
                }
                toast(getString(R.string.let_agent_fix))
            } catch (e: Exception) {
                toast(e.message ?: getString(R.string.send_failed_retry))
            } finally {
                binding.btnAgentFix.isEnabled = true
            }
        }
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val EXTRA_JOB_ID = "job_id"
        fun start(context: Context, jobId: String) {
            context.startActivity(Intent(context, BuildLogActivity::class.java).putExtra(EXTRA_JOB_ID, jobId))
        }
    }
}
