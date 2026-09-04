package com.androidagent.client

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.CountDownTimer
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import com.androidagent.client.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 入口页：负责邮箱密码登录、访客入口与注册。
 * 登录或选择访客模式后进入 MainNavActivity，本页不再承载项目、任务与日志流程。
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: AgentPrefs
    private var codeLogin = false
    private var codeTimer: CountDownTimer? = null

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* 拒绝也不阻塞使用，仅少一条完成提醒 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        prefs = AgentPrefs(this)
        applyConfiguredServer()
        JobNotifier.ensureChannel(this)

        val loginRequired = intent.getBooleanExtra(DeepLink.EXTRA_LOGIN_REQUIRED, false)
        val connected = prefs.apiToken.isNotBlank() && prefs.serverUrl.isNotBlank()
        if (!loginRequired && (connected || prefs.guestMode)) {
            routeConnected()
            return
        }

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.textStatus.isVisible = false

        binding.btnGoRegister.setOnClickListener { RegisterActivity.start(this) }
        binding.btnForgot.setOnClickListener { ForgotPasswordActivity.start(this) }
        binding.btnLoginCloud.setOnClickListener { loginAccount() }
        binding.btnContinueGuest.setOnClickListener { finish() }
        binding.btnSendCode.setOnClickListener { requestLoginCode() }
        binding.authMethodToggle.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (isChecked) switchAuthMethod(checkedId == R.id.btnCodeMode)
        }
        binding.editPassword.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                loginAccount()
                true
            } else {
                false
            }
        }
        binding.editCode.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                loginAccount()
                true
            } else false
        }
        savedInstanceState?.let { state ->
            binding.editEmail.setText(state.getString(STATE_EMAIL).orEmpty())
            binding.editPassword.setText(state.getString(STATE_PASSWORD).orEmpty())
            binding.editCode.setText(state.getString(STATE_CODE).orEmpty())
            switchAuthMethod(state.getBoolean(STATE_CODE_MODE, false))
        }
    }

    private fun applyConfiguredServer() {
        val configuredUrl = BuildConfig.AGENT_SERVER_URL.trim().trimEnd('/')
        val previousUrl = prefs.serverUrl.trim().trimEnd('/')
        if (previousUrl != configuredUrl && prefs.apiToken.isNotBlank()) {
            prefs.clearAuth()
            prefs.guestMode = false
        }
        prefs.serverUrl = configuredUrl
    }

    private fun loginAccount() {
        binding.layoutEmail.error = null
        binding.layoutPassword.error = null
        binding.layoutCode.error = null
        binding.textStatus.isVisible = false
        val email = binding.editEmail.text?.toString()?.trim().orEmpty()
        val password = binding.editPassword.text?.toString().orEmpty()
        val code = binding.editCode.text?.toString()?.trim().orEmpty()
        if (email.isBlank()) {
            binding.layoutEmail.error = getString(R.string.email_required)
            return
        }
        if (!codeLogin && password.isBlank()) {
            binding.layoutPassword.error = getString(R.string.password_required)
            return
        }
        if (codeLogin && code.length < 6) {
            binding.layoutCode.error = getString(R.string.code_required)
            return
        }
        val serverUrl = prefs.serverUrl
        binding.btnLoginCloud.isEnabled = false
        binding.btnLoginCloud.setText(R.string.logging_in)
        binding.textStatus.text = getString(R.string.auth_connecting)
        binding.textStatus.isVisible = true
        lifecycleScope.launch {
            try {
                val auth = withContext(Dispatchers.IO) {
                    if (codeLogin) {
                        AgentApi(serverUrl).loginWithEmailCode(
                            email,
                            code,
                            currentDevice(this@MainActivity),
                        )
                    } else {
                        AgentApi(serverUrl).login(email, password, currentDevice(this@MainActivity))
                    }
                }
                prefs.serverUrl = serverUrl
                prefs.saveAuth(auth)
                maybeRequestNotificationPermission()
                MainNavActivity.start(this@MainActivity)
                finish()
            } catch (e: ApiException) {
                when (e.errorCode) {
                    "account_not_found" -> {
                        binding.textStatus.isVisible = false
                        binding.layoutEmail.error = e.detail
                    }
                    "invalid_password" -> {
                        binding.textStatus.isVisible = false
                        binding.layoutPassword.error = e.detail
                    }
                    "invalid_code" -> {
                        binding.textStatus.isVisible = false
                        binding.layoutCode.error = e.detail
                    }
                    "email_not_verified" -> VerifyEmailActivity.start(this@MainActivity, email)
                    else -> binding.textStatus.text = e.detail
                }
            } catch (e: Exception) {
                binding.textStatus.text = getString(R.string.connect_failed_status, e.message ?: "")
            } finally {
                binding.btnLoginCloud.isEnabled = true
                binding.btnLoginCloud.setText(R.string.login)
            }
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        if (::binding.isInitialized) {
            outState.putString(STATE_EMAIL, binding.editEmail.text?.toString().orEmpty())
            outState.putString(STATE_PASSWORD, binding.editPassword.text?.toString().orEmpty())
            outState.putString(STATE_CODE, binding.editCode.text?.toString().orEmpty())
            outState.putBoolean(STATE_CODE_MODE, codeLogin)
        }
        super.onSaveInstanceState(outState)
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
        val connected = prefs.apiToken.isNotBlank() && prefs.serverUrl.isNotBlank()
        if (connected && DeepLink.hasConversationTarget(intent)) {
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
            val requestedTab = intent.getStringExtra(DeepLink.EXTRA_TAB)
            val tab = requestedTab ?: if (connected) null else DeepLink.TAB_CREATIVE
            startActivity(DeepLink.mainNavIntent(this, tab))
        }
        finish()
    }

    private fun switchAuthMethod(useCode: Boolean) {
        codeLogin = useCode
        if (::binding.isInitialized) {
            binding.layoutPassword.isVisible = !useCode
            binding.btnForgot.isVisible = !useCode
            binding.codeRow.isVisible = useCode
            binding.authMethodToggle.check(if (useCode) R.id.btnCodeMode else R.id.btnPasswordMode)
            binding.layoutPassword.error = null
            binding.layoutCode.error = null
        }
    }

    private fun requestLoginCode() {
        val email = binding.editEmail.text?.toString()?.trim().orEmpty()
        binding.layoutEmail.error = null
        if (email.isBlank()) {
            binding.layoutEmail.error = getString(R.string.email_required)
            return
        }
        binding.btnSendCode.isEnabled = false
        lifecycleScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    AgentApi(prefs.serverUrl).requestEmailLoginCode(email)
                }
                binding.textStatus.text = getString(R.string.code_sent_hint)
                binding.textStatus.isVisible = true
                startCodeCountdown()
            } catch (e: ApiException) {
                binding.btnSendCode.isEnabled = true
                binding.textStatus.text = e.detail
                binding.textStatus.isVisible = true
            } catch (e: Exception) {
                binding.btnSendCode.isEnabled = true
                binding.textStatus.text = getString(R.string.connect_failed_status, e.message.orEmpty())
                binding.textStatus.isVisible = true
            }
        }
    }

    private fun startCodeCountdown() {
        codeTimer?.cancel()
        codeTimer = object : CountDownTimer(60_000, 1_000) {
            override fun onTick(millisUntilFinished: Long) {
                if (::binding.isInitialized) {
                    binding.btnSendCode.text = getString(R.string.resend_code_seconds, millisUntilFinished / 1000)
                }
            }

            override fun onFinish() {
                if (::binding.isInitialized) {
                    binding.btnSendCode.isEnabled = true
                    binding.btnSendCode.setText(R.string.send_code)
                }
            }
        }.start()
    }

    override fun onDestroy() {
        codeTimer?.cancel()
        super.onDestroy()
    }

    companion object {
        private const val STATE_EMAIL = "login_email"
        private const val STATE_PASSWORD = "login_password"
        private const val STATE_CODE = "login_code"
        private const val STATE_CODE_MODE = "login_code_mode"

        fun startLogin(context: Context) {
            context.startActivity(
                Intent(context, MainActivity::class.java)
                    .putExtra(DeepLink.EXTRA_LOGIN_REQUIRED, true),
            )
        }
    }
}
