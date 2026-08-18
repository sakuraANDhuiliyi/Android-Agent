package com.androidagent.client

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 连接 gate：仅负责首次连接 / 注册 / 编辑凭据。
 * 连接成功后进入 MainNavActivity，本页不再承载项目、任务与日志流程。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: AgentPrefs

    private var advancedOpen = false

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* 拒绝也不阻塞使用，仅少一条完成提醒 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        prefs = AgentPrefs(this)
        JobNotifier.ensureChannel(this)

        if (!intent.getBooleanExtra(DeepLink.EXTRA_EDIT_CONNECTION, false) &&
            prefs.apiToken.isNotBlank() &&
            prefs.serverUrl.isNotBlank()
        ) {
            routeConnected()
            return
        }

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.editServerUrl.setText(prefs.serverUrl)
        binding.editApiToken.setText(prefs.apiToken)
        binding.textStatus.text = getString(R.string.status_disconnected)

        binding.btnConnect.setOnClickListener { connectServer() }
        binding.btnRegister.setOnClickListener { confirmRegisterUser() }
        binding.btnToggleAdvanced.setOnClickListener { toggleAdvanced() }

        if (binding.editApiToken.text.isNullOrBlank() && prefs.apiToken.isNotBlank()) {
            binding.editApiToken.setText(prefs.apiToken)
        }
    }

    private fun toggleAdvanced() {
        advancedOpen = !advancedOpen
        binding.layoutAdvanced.visibility = if (advancedOpen) View.VISIBLE else View.GONE
        binding.btnToggleAdvanced.text = getString(
            if (advancedOpen) R.string.collapse else R.string.advanced,
        )
    }

    private fun connectServer() {
        binding.layoutServerUrl.error = null
        binding.layoutApiToken.error = null
        val serverUrl = binding.editServerUrl.text?.toString()?.trim().orEmpty()
        if (serverUrl.isBlank()) {
            binding.layoutServerUrl.error = getString(R.string.server_url_required)
            return
        }
        val apiToken = binding.editApiToken.text?.toString()?.trim().orEmpty()
        if (apiToken.isBlank()) {
            binding.layoutApiToken.error = getString(R.string.api_token_required)
            return
        }

        val api = AgentApi(serverUrl, apiToken)
        binding.btnConnect.isEnabled = false
        binding.textStatus.text = getString(R.string.connecting)
        lifecycleScope.launch {
            try {
                val health = withContext(Dispatchers.IO) { api.health() }
                prefs.serverUrl = serverUrl
                prefs.apiToken = apiToken
                if (health.userId.isNotBlank()) {
                    prefs.userId = health.userId
                }
                prefs.lastSyncAt = System.currentTimeMillis()
                maybeRequestNotificationPermission()
                binding.textStatus.text = getString(R.string.status_connected)
                MainNavActivity.start(this@MainActivity)
                finish()
            } catch (e: Exception) {
                binding.layoutApiToken.error = e.message ?: getString(R.string.connect_failed)
                binding.textStatus.text = getString(R.string.connect_failed_status, e.message ?: "")
            } finally {
                binding.btnConnect.isEnabled = true
            }
        }
    }

    private fun confirmRegisterUser() {
        val serverUrl = binding.editServerUrl.text?.toString()?.trim().orEmpty()
        val registrationToken =
            binding.editRegistrationToken.text?.toString()?.trim().orEmpty()
        if (serverUrl.isBlank()) {
            toast("请填写服务器地址")
            return
        }
        if (registrationToken.isBlank()) {
            toast("请输入服务端配置的注册密钥")
            return
        }
        if (prefs.apiToken.isBlank()) {
            registerUser(serverUrl, registrationToken)
            return
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.register_confirm_title)
            .setMessage(R.string.register_confirm_message)
            .setPositiveButton(R.string.register_user) { _, _ ->
                registerUser(serverUrl, registrationToken)
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun registerUser(serverUrl: String, registrationToken: String) {
        prefs.serverUrl = serverUrl
        binding.btnRegister.isEnabled = false
        lifecycleScope.launch {
            try {
                val account = withContext(Dispatchers.IO) {
                    AgentApi(serverUrl).register(registrationToken)
                }
                prefs.userId = account.userId
                prefs.apiToken = account.token
                prefs.selectedProjectId = null
                binding.editApiToken.setText(account.token)
                binding.editRegistrationToken.text?.clear()
                binding.textStatus.text = "注册成功，正在连接..."
                connectServer()
            } catch (e: Exception) {
                binding.textStatus.text = "注册失败: ${e.message}"
                toast("注册失败")
            } finally {
                binding.btnRegister.isEnabled = true
            }
        }
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun routeConnected() {
        if (DeepLink.hasConversationTarget(intent)) {
            startActivity(
                DeepLink.conversationIntent(
                    this,
                    intent.getStringExtra(DeepLink.EXTRA_PROJECT_ID).orEmpty(),
                    intent.getStringExtra(DeepLink.EXTRA_CONVERSATION_ID).orEmpty(),
                    intent.getStringExtra(DeepLink.EXTRA_CONVERSATION_TITLE).orEmpty(),
                    intent.getStringExtra(DeepLink.EXTRA_JOB_ID),
                ),
            )
        } else {
            startActivity(DeepLink.mainNavIntent(this, intent.getStringExtra(DeepLink.EXTRA_TAB)))
        }
        finish()
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
