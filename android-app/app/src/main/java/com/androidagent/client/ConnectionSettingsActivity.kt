package com.androidagent.client

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityConnectionSettingsBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 已连接设置页：connection card + 重新连接 / 编辑 / 断开。 */
class ConnectionSettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivityConnectionSettingsBinding
    private lateinit var prefs: AgentPrefs

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        binding = ActivityConnectionSettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        prefs = AgentPrefs(this)

        binding.toolbar.setNavigationOnClickListener { finish() }
        renderConnection()

        binding.btnReconnect.setOnClickListener { reconnect() }
        binding.btnEditConnection.setOnClickListener {
            startActivity(
                Intent(this, MainActivity::class.java)
                    .putExtra(DeepLink.EXTRA_EDIT_CONNECTION, true),
            )
        }
        binding.btnDisconnect.setOnClickListener { confirmDisconnect() }
    }

    private fun renderConnection() {
        binding.textConnHost.text = prefs.serverUrl
        binding.textConnUser.text = getString(R.string.conn_user, prefs.userId.ifBlank { getString(R.string.conn_user_unknown) })
        binding.textConnTls.text = getString(
            if (prefs.serverUrl.startsWith("https", ignoreCase = true)) {
                R.string.conn_tls_https
            } else {
                R.string.conn_tls_http
            },
        )
        val syncAt = prefs.lastSyncAt
        binding.textConnSync.text = if (syncAt <= 0L) {
            getString(R.string.conn_sync_never)
        } else {
            getString(R.string.conn_sync_last, UiFormat.relativeTime(this, syncAt / 1000.0))
        }
    }

    private fun reconnect() {
        binding.btnReconnect.isEnabled = false
        val api = AgentApi(prefs.serverUrl, prefs.apiToken)
        lifecycleScope.launch {
            try {
                val health = withContext(Dispatchers.IO) { api.health() }
                if (health.userId.isNotBlank()) prefs.userId = health.userId
                prefs.lastSyncAt = System.currentTimeMillis()
                renderConnection()
                toast("连接正常")
            } catch (e: Exception) {
                toast(e.message ?: "连接失败")
            } finally {
                binding.btnReconnect.isEnabled = true
            }
        }
    }

    private fun confirmDisconnect() {
        AlertDialog.Builder(this)
            .setTitle(R.string.disconnect_confirm_title)
            .setMessage(R.string.disconnect_confirm_message)
            .setPositiveButton(R.string.disconnect) { _, _ ->
                prefs.apiToken = ""
                prefs.userId = ""
                MainNavActivity.start(this)
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(Intent(context, ConnectionSettingsActivity::class.java))
        }
    }
}
