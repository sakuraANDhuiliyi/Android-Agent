package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.lifecycle.lifecycleScope
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivityTokenUsageBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class TokenUsageActivity : AppCompatActivity() {
    private lateinit var binding: ActivityTokenUsageBinding
    private var cachedJobs: List<JobInfo> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityTokenUsageBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.chipPeriod.setOnCheckedStateChangeListener { _, checkedIds ->
            val days = when (checkedIds.firstOrNull()) {
                R.id.chipToday -> 1
                R.id.chip7d -> 7
                R.id.chip30d -> 30
                else -> null
            }
            render(UsageStats.forJobs(cachedJobs, days))
        }
        binding.btnDetails.setOnClickListener { TokenUsageDetailActivity.start(this) }
        binding.btnManageApi.setOnClickListener { ModelApiActivity.start(this) }
        lifecycleScope.launch {
            try {
                val prefs = AgentPrefs(this@TokenUsageActivity)
                cachedJobs = withContext(Dispatchers.IO) {
                    AgentApi(prefs.serverUrl, prefs.apiToken).listJobs()
                }
                render(UsageStats.forJobs(cachedJobs))
            } catch (_: Exception) {
                binding.textUsageNumbers.setText(R.string.token_usage_unavailable)
            }
        }
    }

    private fun render(stats: UsageStats) {
        binding.textUsageNumbers.text = getString(
            R.string.usage_numbers_format,
            UsageStats.compact(stats.input),
            UsageStats.compact(stats.output),
            UsageStats.compact(stats.total),
        )
        binding.progressUsage.max = maxOf(stats.total, 1)
        binding.progressUsage.progress = stats.total
        binding.textUsageMeta.text = getString(R.string.usage_task_count, stats.taskCount)
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, TokenUsageActivity::class.java))
        }
    }
}
