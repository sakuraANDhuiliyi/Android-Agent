package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.androidagent.client.databinding.ActivitySimplePageBinding
import com.androidagent.client.databinding.ItemSettingsRowBinding

class AboutActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivitySimplePageBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setTitle(R.string.about_title)
        binding.toolbar.setNavigationOnClickListener { finish() }
        val connected = AgentPrefs(this).apiToken.isNotBlank()
        binding.content.addView(TextView(this).apply {
            text = getString(R.string.app_name)
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_HeadlineSmall)
        })
        binding.content.addView(TextView(this).apply {
            text = "v${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})"
            setPadding(0, 8, 0, 0)
        })
        binding.content.addView(TextView(this).apply {
            text = getString(if (connected) R.string.about_connected else R.string.status_disconnected)
            setTextColor(UiFormat.statusColor(this@AboutActivity, if (connected) "succeeded" else "failed"))
            setPadding(0, 8, 0, 24)
        })
        listOf(R.string.user_agreement, R.string.privacy_policy, R.string.open_source_licenses, R.string.feedback_export).forEach { res ->
            val row = ItemSettingsRowBinding.inflate(layoutInflater, binding.content, true)
            row.textRowTitle.setText(res)
            row.root.setOnClickListener { CloudPreview.show(this) }
        }
        binding.content.addView(TextView(this).apply {
            text = getString(R.string.about_privacy_note) + "\n\n" + getString(R.string.about_copyright)
            setPadding(0, 24, 0, 0)
            setTextAppearance(com.google.android.material.R.style.TextAppearance_Material3_BodySmall)
        })
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, AboutActivity::class.java))
        }
    }
}
