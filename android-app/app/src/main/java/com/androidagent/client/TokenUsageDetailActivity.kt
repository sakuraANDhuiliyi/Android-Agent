package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivitySimplePageBinding
import com.google.android.material.card.MaterialCardView
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class TokenUsageDetailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivitySimplePageBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setTitle(R.string.view_usage_details)
        binding.toolbar.setNavigationOnClickListener { finish() }
        lifecycleScope.launch {
            val prefs = AgentPrefs(this@TokenUsageDetailActivity)
            val jobs = runCatching {
                withContext(Dispatchers.IO) {
                    AgentApi(prefs.serverUrl, prefs.apiToken).listJobs()
                }
            }.getOrElse { emptyList() }
            if (jobs.isEmpty()) {
                binding.content.addView(TextView(this@TokenUsageDetailActivity).apply {
                    text = getString(R.string.token_usage_unavailable)
                    setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodyMedium)
                })
                return@launch
            }
            jobs.sortedByDescending { it.createdAt ?: 0.0 }.take(50).forEach { job ->
                val card = MaterialCardView(this@TokenUsageDetailActivity).apply {
                    radius = resources.getDimension(R.dimen.radius_card)
                    cardElevation = 0f
                    strokeWidth = resources.getDimensionPixelSize(R.dimen.space_tiny)
                    setStrokeColor(getColor(R.color.signal_outline_variant))
                    setContentPadding(16, 14, 16, 14)
                }
                card.addView(TextView(this@TokenUsageDetailActivity).apply {
                    text = buildString {
                        append(job.prompt.lineSequence().firstOrNull()?.take(48) ?: "任务")
                        append("\n")
                        append(job.provider.orEmpty())
                        if (!job.model.isNullOrBlank()) append(" / ${job.model}")
                        append("\n输入 ${UsageStats.compact(job.inputTokens ?: 0)} · 输出 ${UsageStats.compact(job.outputTokens ?: 0)} · 合计 ${UsageStats.compact(job.totalTokens ?: 0)}")
                    }
                    setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodyMedium)
                    setTextColor(getColor(R.color.signal_on_surface))
                })
                binding.content.addView(card, android.widget.LinearLayout.LayoutParams(-1, -2).apply {
                    bottomMargin = resources.getDimensionPixelSize(R.dimen.space_small)
                })
            }
        }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, TokenUsageDetailActivity::class.java))
        }
    }
}
