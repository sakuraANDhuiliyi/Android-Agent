package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityModelApiBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ModelApiActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityModelApiBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }
        binding.rowBaseUrl.textRowTitle.setText(R.string.custom_base_url)
        binding.rowBaseUrl.textRowValue.visibility = android.view.View.VISIBLE
        binding.rowBaseUrl.textRowValue.setText(R.string.not_set)
        binding.rowBaseUrl.root.setOnClickListener { CloudPreview.show(this) }
        binding.btnAddKey.setOnClickListener { ProviderKeyActivity.start(this) }

        val prefs = AgentPrefs(this)
        lifecycleScope.launch {
            try {
                val (health, catalog) = withContext(Dispatchers.IO) {
                    val api = AgentApi(prefs.serverUrl, prefs.apiToken)
                    api.health() to runCatching { api.listModels() }.getOrNull()
                }
                val ready = health.apiKeyConfigured
                binding.textReady.text = getString(if (ready) R.string.api_ready else R.string.api_missing_key)
                binding.textReady.setTextColor(UiFormat.statusColor(this@ModelApiActivity, if (ready) "succeeded" else "failed"))
                binding.textDefaults.text = buildString {
                    append(getString(R.string.model_api_provider, health.provider.ifBlank { "—" }))
                    append("\n")
                    append(getString(R.string.default_model)).append("：").append(health.model.ifBlank { "—" })
                    catalog?.models?.takeIf { it.isNotEmpty() }?.let { models ->
                        append("\n\n可用模型：")
                        append(models.take(4).joinToString("、") { it.label.ifBlank { it.model } })
                    }
                }
                binding.textProviderName.text = health.provider.ifBlank { getString(R.string.row_model_api) }
                binding.textProviderStatus.text = getString(if (ready) R.string.model_api_configured else R.string.model_api_missing)
                binding.btnAddKey.setText(if (ready) R.string.change_key else R.string.add_key)
            } catch (_: Exception) {
                binding.textReady.setText(R.string.api_missing_key)
                binding.textProviderStatus.setText(R.string.model_api_missing)
            }
        }
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, ModelApiActivity::class.java))
        }
    }
}
