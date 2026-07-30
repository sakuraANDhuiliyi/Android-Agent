package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityBuildLogBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class BuildLogActivity : AppCompatActivity() {

    private lateinit var binding: ActivityBuildLogBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityBuildLogBinding.inflate(layoutInflater)
        setContentView(binding.root)
        val prefs = AgentPrefs(this)
        val jobId = intent.getStringExtra(EXTRA_JOB_ID).orEmpty()
        binding.toolbar.title = getString(R.string.build_log)
        binding.toolbar.setNavigationIcon(android.R.drawable.ic_menu_close_clear_cancel)
        binding.toolbar.setNavigationOnClickListener { finish() }
        if (jobId.isBlank() || prefs.apiToken.isBlank()) {
            toast(getString(R.string.resource_unavailable))
            finish()
            return
        }
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        val cached = prefs.getSnippet("buildlog:$jobId")
        if (!cached.isNullOrBlank()) binding.textBuildLog.text = cached
        lifecycleScope.launch {
            try {
                val content = withContext(Dispatchers.IO) { api.getTaskBuildLog(jobId) }
                val display = if (content.length > 200_000) {
                    content.take(200_000) + "\n\n… " + getString(R.string.large_diff_truncated)
                } else content
                binding.textBuildLog.text = display
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

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()

    companion object {
        private const val EXTRA_JOB_ID = "job_id"
        fun start(context: Context, jobId: String) {
            context.startActivity(Intent(context, BuildLogActivity::class.java).putExtra(EXTRA_JOB_ID, jobId))
        }
    }
}
